import json
from typing import Dict, Any, List, Optional
from openclaw.core.model_adapter import ModelAdapter
from openclaw.core.tool_registry import ToolRegistry


class QueryEngine:
    def __init__(self, model_adapter: ModelAdapter, tool_registry: ToolRegistry, max_iterations: int = 25):
        self.model_adapter = model_adapter
        self.tool_registry = tool_registry
        self.max_iterations = max_iterations

    async def run(self, prompt: str, system_prompt: str = None) -> str:
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": prompt})
        
        iteration = 0
        
        while iteration < self.max_iterations:
            # Get response from LLM
            response = await self.model_adapter.chat_completion(messages)
            
            # Track tokens
            input_tokens = response.get("usage", {}).get("prompt_tokens", 0)
            output_tokens = response.get("usage", {}).get("completion_tokens", 0)
            
            # Check if the response has tool calls
            choices = response.get("choices", [])
            if not choices:
                raise Exception("No choices returned from model")
                
            choice = choices[0]
            message = choice.get("message", {})
            
            # Handle both function_call and tool_calls formats
            has_tool_calls = False
            
            # Check for legacy function_call format
            if "function_call" in message:
                function_call = message["function_call"]
                name = function_call.get("name")
                arguments_str = function_call.get("arguments")
                
                try:
                    arguments = json.loads(arguments_str)
                except json.JSONDecodeError:
                    raise Exception(f"Invalid JSON in function call arguments: {arguments_str}")
                
                # Execute the tool
                result = await self.tool_registry.execute_tool(name, arguments)
                
                # Add assistant's tool request and the result to messages
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "function_call": {
                        "name": name,
                        "arguments": arguments_str
                    }
                })
                
                messages.append({
                    "role": "function",
                    "name": name,
                    "content": json.dumps(result)
                })
                
                has_tool_calls = True
                
            # Check for newer tool_calls format
            elif "tool_calls" in message:
                tool_calls = message["tool_calls"]
                
                for tool_call in tool_calls:
                    call_id = tool_call.get("id")
                    function_info = tool_call.get("function", {})
                    name = function_info.get("name")
                    arguments_str = function_info.get("arguments")
                    
                    try:
                        arguments = json.loads(arguments_str)
                    except json.JSONDecodeError:
                        raise Exception(f"Invalid JSON in tool call arguments: {arguments_str}")
                    
                    # Execute the tool
                    result = await self.tool_registry.execute_tool(name, arguments)
                    
                    # Add assistant's tool request and the result to messages
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": arguments_str
                            }
                        }]
                    })
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps(result)
                    })
                
                has_tool_calls = True
            
            # If there were no tool calls, we're done
            if not has_tool_calls:
                final_content = message.get("content", "")
                return final_content.strip()
            
            iteration += 1
        
        # Max iterations reached
        raise Exception(f"Max iterations ({self.max_iterations}) reached without completion")
