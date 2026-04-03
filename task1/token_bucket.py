import time
from typing import Optional

class TokenBucket:
    """A token bucket rate limiter implementation."""
    
    def __init__(self, capacity: int, refill_rate: float):
        """
        Initialize a token bucket.
        
        Args:
            capacity: Maximum number of tokens the bucket can hold
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.time()
    
    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
    
    def allow(self, tokens: int = 1) -> bool:
        """
        Check if the requested number of tokens can be consumed.
        
        Args:
            tokens: Number of tokens to consume (default: 1)
            
        Returns:
            True if tokens can be consumed, False otherwise
        """
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
    
    def available(self) -> float:
        """
        Get the current number of available tokens.
        
        Returns:
            Number of available tokens as a float
        """
        self._refill()
        return self.tokens