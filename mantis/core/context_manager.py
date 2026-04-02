# mantis/core/context_manager.py
from typing import List, Dict, Any


class ContextManager:
    """Manages conversation context and token budgets."""

    def __init__(self, max_tokens: int = 128000):
        """
        Initialize the ContextManager.

        Args:
            max_tokens: Maximum token budget for the conversation context.
        """
        self.max_tokens = max_tokens
        self.messages: List[Dict[str, str]] = []

    def add_message(self, role: str, content: str) -> None:
        """
        Add a message to the conversation context.

        Args:
            role: The role of the message sender (e.g., 'user', 'assistant', 'system').
            content: The content of the message.
        """
        self.messages.append({"role": role, "content": content})

    def get_messages(self) -> List[Dict[str, str]]:
        """
        Get all messages in the conversation context.

        Returns:
            List of message dictionaries with 'role' and 'content' keys.
        """
        return self.messages.copy()

    def token_count(self) -> int:
        """
        Estimate the total token count of all messages.

        Returns:
            Estimated token count based on character count divided by 4.
        """
        total_chars = sum(len(msg["content"]) for msg in self.messages)
        return total_chars // 4

    def truncate_to_fit(self, reserve_tokens: int = 4096) -> None:
        """
        Remove oldest messages to fit within the token budget.

        Args:
            reserve_tokens: Number of tokens to reserve for the current request.
                           Defaults to 4096.
        """
        available_tokens = self.max_tokens - reserve_tokens

        while self.token_count() > available_tokens and len(self.messages) > 0:
            self.messages.pop(0)

    def clear(self) -> None:
        """Clear all messages from the conversation context."""
        self.messages.clear()