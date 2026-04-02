# mantis/core/tool_registry.py
import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


AsyncToolHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: AsyncToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: AsyncToolHandler,
    ) -> Tool:
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string")
        if not description or not isinstance(description, str):
            raise ValueError("Tool description must be a non-empty string")
        if not isinstance(parameters, dict):
            raise TypeError("Tool parameters must be a JSON schema dictionary")
        if not inspect.iscoroutinefunction(handler):
            raise TypeError("Tool handler must be an async callable")
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")

        tool_obj = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )
        self._tools[name] = tool_obj
        return tool_obj

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Tool '{name}' is not registered") from exc

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())

    def list_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    async def execute(self, name: str, arguments: Any) -> str:
        tool_obj = self.get(name)
        result = await self._invoke_handler(tool_obj.handler, arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result)

    async def execute_tool(self, name: str, arguments: Any) -> str:
        return await self.execute(name, arguments)

    def search(self, query: str) -> list[Tool]:
        if not query or not query.strip():
            return self.list_all()

        terms = [term for term in query.lower().split() if term]
        matches: list[Tool] = []
        for tool in self._tools.values():
            haystack = " ".join(
                [
                    tool.name,
                    tool.description,
                    json.dumps(tool.parameters, sort_keys=True),
                ]
            ).lower()
            if all(term in haystack for term in terms):
                matches.append(tool)
        return matches

    async def _invoke_handler(self, handler: AsyncToolHandler, arguments: Any) -> Any:
        if arguments is None:
            arguments = {}

        if not isinstance(arguments, dict):
            return await handler(arguments)

        signature = inspect.signature(handler)
        params = list(signature.parameters.values())
        has_var_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in params
        )
        named_params = {
            param.name
            for param in params
            if param.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        }

        if has_var_kwargs or set(arguments).issubset(named_params):
            return await handler(**arguments)

        if len(params) == 1 and params[0].kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return await handler(arguments)

        return await handler(**arguments)


default_registry = ToolRegistry()


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> Callable[[AsyncToolHandler], AsyncToolHandler]:
    def decorator(handler: AsyncToolHandler) -> AsyncToolHandler:
        default_registry.register(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )
        setattr(
            handler,
            "__tool__",
            Tool(
                name=name,
                description=description,
                parameters=parameters,
                handler=handler,
            ),
        )
        return handler

    return decorator
