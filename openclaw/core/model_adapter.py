import asyncio
from typing import AsyncGenerator, List, Dict, Optional
import httpx
import json


class ModelAdapter:
    def __init__(self, base_url: str, api_key: str, model: str, max_tokens: int = 4096):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost_usd = 0.0
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
        )

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    async def _make_request(self, url: str, payload: dict) -> httpx.Response:
        for attempt in range(1, 4):  # max 3 retries
            try:
                response = await self.client.post(url, json=payload)
                
                if response.status_code == 200:
                    return response
                elif response.status_code in [429, 500, 502, 503]:
                    if attempt == 3:  # last attempt
                        raise Exception(f"Request failed after 3 attempts. Status: {response.status_code}")
                    
                    # Exponential backoff: wait 2^attempt seconds
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
        url = f"{self.base_url}/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens
        }
        
        if tools:
            payload["tools"] = tools
        
        response = await self._make_request(url, payload)
        result = response.json()
        
        # Update token counts
        usage = result.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        
        # Calculate cost (assuming standard pricing - adjust as needed)
        # Cost is in USD per 1K tokens
        input_cost_per_1k = 0.0005  # Example pricing
        output_cost_per_1k = 0.0015  # Example pricing
        self._total_cost_usd += (input_tokens * input_cost_per_1k / 1000) + (output_tokens * output_cost_per_1k / 1000)
        
        return result

    async def stream(self, messages: List[Dict], tools: List[Dict] = None, tool_choice=None) -> AsyncGenerator:
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
                        
                        # Update token counts when usage is available
                        if "usage" in data:
                            usage = data["usage"]
                            input_tokens = usage.get("prompt_tokens", 0)
                            output_tokens = usage.get("completion_tokens", 0)
                            self._total_input_tokens += input_tokens
                            self._total_output_tokens += output_tokens
                            
                            # Calculate cost (assuming standard pricing - adjust as needed)
                            input_cost_per_1k = 0.0005  # Example pricing
                            output_cost_per_1k = 0.0015  # Example pricing
                            self._total_cost_usd += (input_tokens * input_cost_per_1k / 1000) + (output_tokens * output_cost_per_1k / 1000)
                    except json.JSONDecodeError:
                        continue
