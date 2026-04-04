import asyncio
from typing import AsyncGenerator, List, Dict, Optional
import httpx
import json


class BudgetExceededError(RuntimeError):
    pass


class ModelAdapter:
    def __init__(self, base_url: str, api_key: str, model: str, max_tokens: int = 4096,
                 cost_per_1k_input: float = 0.0005, cost_per_1k_output: float = 0.0015,
                 max_budget_usd: Optional[float] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.cost_per_1k_input = cost_per_1k_input
        self.cost_per_1k_output = cost_per_1k_output
        self.max_budget_usd = max_budget_usd
        self.requires_temp_1: bool = False  # set by swap_to_profile for MiniMax/DeepSeek-R1
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost_usd = 0.0
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            timeout=120.0,
        )

    def profile_snapshot(self) -> dict:
        """Capture current provider settings for restore after a per-task swap."""
        return {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "cost_per_1k_input": self.cost_per_1k_input,
            "cost_per_1k_output": self.cost_per_1k_output,
            "requires_temp_1": self.requires_temp_1,
        }

    def restore_snapshot(self, snapshot: dict) -> None:
        """Restore provider settings saved by profile_snapshot()."""
        if snapshot["api_key"] != self.api_key:
            self.client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {snapshot['api_key']}",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
            )
            self.api_key = snapshot["api_key"]
        self.base_url = snapshot["base_url"].rstrip("/")
        self.model = snapshot["model"]
        self.cost_per_1k_input = snapshot["cost_per_1k_input"]
        self.cost_per_1k_output = snapshot["cost_per_1k_output"]
        self.requires_temp_1 = snapshot.get("requires_temp_1", False)

    def swap_to_profile(self, profile) -> None:
        """Swap to a different ModelProfile, replacing httpx client if api_key changes."""
        if profile.api_key != self.api_key:
            self.client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {profile.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
            )
            self.api_key = profile.api_key
        self.base_url = profile.base_url.rstrip("/")
        self.model = profile.name
        self.cost_per_1k_input = profile.cost_per_1k_input
        self.cost_per_1k_output = profile.cost_per_1k_output
        self.requires_temp_1 = getattr(profile, "requires_temp_1", False)

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    @property
    def remaining_budget_usd(self) -> Optional[float]:
        if self.max_budget_usd is None:
            return None
        return max(self.max_budget_usd - self._total_cost_usd, 0.0)

    def _check_budget(self) -> None:
        if self.max_budget_usd is None:
            return
        if self._total_cost_usd >= self.max_budget_usd:
            raise BudgetExceededError(
                f"Budget exceeded: ${self._total_cost_usd:.6f} spent of ${self.max_budget_usd:.6f} limit"
            )

    def _register_usage(self, usage: dict) -> None:
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._total_cost_usd += (input_tokens * self.cost_per_1k_input / 1000) + (
            output_tokens * self.cost_per_1k_output / 1000
        )

    @staticmethod
    def _coerce_text(content) -> str:
        """Normalize LLM content field: str pass-through, list-of-chunks concatenated."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for chunk in content:
                if isinstance(chunk, str):
                    parts.append(chunk)
                elif isinstance(chunk, dict):
                    parts.append(chunk.get("text", ""))
            return "".join(parts)
        return ""

    async def _make_request(self, url: str, payload: dict) -> httpx.Response:
        for attempt in range(1, 4):  # max 3 retries
            try:
                response = await self.client.post(url, json=payload)

                if response.status_code == 200:
                    return response
                elif response.status_code in [429, 500, 502, 503]:
                    if attempt == 3:
                        raise Exception(f"Request failed after 3 attempts. Status: {response.status_code}")

                    # Respect Retry-After header when present (429 rate limit)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait_time = float(retry_after)
                        except ValueError:
                            wait_time = 2 ** attempt
                    else:
                        wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                else:
                    response.raise_for_status()
            except httpx.RequestError as e:
                if attempt == 3:
                    raise e
                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)

        raise Exception("Unexpected flow - should not reach here")

    async def chat(self, messages: List[Dict], tools: List[Dict] = None, temperature: float = 0.7) -> Dict:
        self._check_budget()
        url = f"{self.base_url}/chat/completions"

        # Some models (MiniMax, DeepSeek-R1) require temperature=1.0 exactly.
        if self.requires_temp_1:
            temperature = 1.0

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }

        if tools:
            payload["tools"] = tools

        response = await self._make_request(url, payload)
        result = response.json()

        usage = result.get("usage", {})
        self._register_usage(usage)

        # Auto-continuation: if the model stopped due to token limit, keep going
        accumulated_text = ""
        choice = result.get("choices", [{}])[0]
        finish_reason = choice.get("finish_reason", "")
        if choice.get("message", {}).get("content"):
            accumulated_text = self._coerce_text(choice["message"]["content"])

        while finish_reason == "length":
            self._check_budget()
            continuation_messages = list(messages) + [
                {"role": "assistant", "content": accumulated_text},
                {"role": "user", "content": "Continue."},
            ]
            cont_payload = dict(payload)
            cont_payload["messages"] = continuation_messages
            cont_response = await self._make_request(url, cont_payload)
            cont_result = cont_response.json()
            self._register_usage(cont_result.get("usage", {}))
            cont_choice = cont_result.get("choices", [{}])[0]
            chunk = self._coerce_text(cont_choice.get("message", {}).get("content", ""))
            accumulated_text += chunk
            finish_reason = cont_choice.get("finish_reason", "stop")

        if accumulated_text:
            result.setdefault("choices", [{}])
            if result["choices"]:
                result["choices"][0].setdefault("message", {})
                result["choices"][0]["message"]["content"] = accumulated_text

        return result

    async def stream(self, messages: List[Dict], tools: List[Dict] = None, tool_choice=None) -> AsyncGenerator:
        self._check_budget()
        url = f"{self.base_url}/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.max_tokens
        }
        
        if tools:
            payload["tools"] = tools
            
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        
        async with self.client.stream("POST", url, json=payload) as response:
            async for chunk in response.aiter_lines():
                if chunk.startswith("data: "):
                    data_str = chunk[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        yield data
                        
                        if "usage" in data:
                            self._register_usage(data["usage"])
                    except json.JSONDecodeError:
                        continue
