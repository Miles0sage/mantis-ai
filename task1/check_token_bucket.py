#!/usr/bin/env python3
"""Check that the TokenBucket class implements the exact required API."""

import sys
import time
from token_bucket import TokenBucket

def test_api():
    """Test the exact API as required."""
    
    # Test 1: Check __init__ signature
    print("Test 1: Creating TokenBucket with capacity=10, refill_rate=1.0")
    bucket = TokenBucket(capacity=10, refill_rate=1.0)
    assert bucket.capacity == 10
    assert bucket.refill_rate == 1.0
    print("✓ __init__(capacity: int, refill_rate: float) works")
    
    # Test 2: Check allow() method signature and behavior
    print("\nTest 2: Testing allow(tokens: int = 1) -> bool")
    # Should allow first 10 tokens
    for i in range(10):
        assert bucket.allow() == True, f"Should allow token {i+1}"
    # Should reject 11th token
    assert bucket.allow() == False, "Should reject when bucket is empty"
    print("✓ allow(tokens: int = 1) -> bool works")
    
    # Test 3: Check available() method signature and behavior
    print("\nTest 3: Testing available() -> float")
    # Create fresh bucket
    bucket = TokenBucket(capacity=5, refill_rate=0.5)
    assert isinstance(bucket.available(), float), "available() should return float"
    assert bucket.available() == 5.0, "Should start with full capacity"
    
    # Consume some tokens
    bucket.allow(2)
    available = bucket.available()
    assert abs(available - 3.0) < 0.0001, f"Should have ~3 tokens after consuming 2, got {available}"
    
    # Test with refill
    time.sleep(0.2)  # Wait for some refill
    available = bucket.available()
    assert 3.0 < available <= 3.1, f"Should have refilled slightly, got {available}"
    print("✓ available() -> float works")
    
    # Test 4: Check allow with custom token amount
    print("\nTest 4: Testing allow with custom token amount")
    bucket = TokenBucket(capacity=10, refill_rate=1.0)
    assert bucket.allow(5) == True, "Should allow 5 tokens"
    assert bucket.available() == 5.0, "Should have 5 tokens left"
    assert bucket.allow(6) == False, "Should reject 6 tokens when only 5 available"
    print("✓ allow(tokens: int) works with custom amounts")
    
    print("\n✅ All API tests passed!")

if __name__ == "__main__":
    try:
        test_api()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)