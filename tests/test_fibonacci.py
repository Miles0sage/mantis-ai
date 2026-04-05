"""Pytest tests for fibonacci.py"""

import pytest
from fibonacci import fibonacci, fibonacci_sequence


def test_fibonacci_basic():
    """Test basic Fibonacci number calculations."""
    assert fibonacci(0) == 0
    assert fibonacci(1) == 1
    assert fibonacci(2) == 1
    assert fibonacci(3) == 2
    assert fibonacci(4) == 3
    assert fibonacci(5) == 5
    assert fibonacci(6) == 8
    assert fibonacci(7) == 13
    assert fibonacci(8) == 21
    assert fibonacci(9) == 34
    assert fibonacci(10) == 55


def test_fibonacci_edge_cases():
    """Test edge cases for Fibonacci function."""
    assert fibonacci(0) == 0
    assert fibonacci(1) == 1
    # Test negative input
    assert fibonacci(-1) == 0
    assert fibonacci(-5) == 0
    assert fibonacci(-10) == 0


def test_fibonacci_sequence_basic():
    """Test basic Fibonacci sequence generation."""
    assert fibonacci_sequence(0) == []
    assert fibonacci_sequence(1) == [0]
    assert fibonacci_sequence(2) == [0, 1]
    assert fibonacci_sequence(3) == [0, 1, 1]
    assert fibonacci_sequence(4) == [0, 1, 1, 2]
    assert fibonacci_sequence(5) == [0, 1, 1, 2, 3]
    assert fibonacci_sequence(6) == [0, 1, 1, 2, 3, 5]
    assert fibonacci_sequence(7) == [0, 1, 1, 2, 3, 5, 8]
    assert fibonacci_sequence(8) == [0, 1, 1, 2, 3, 5, 8, 13]
    assert fibonacci_sequence(9) == [0, 1, 1, 2, 3, 5, 8, 13, 21]
    assert fibonacci_sequence(10) == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]


def test_fibonacci_sequence_edge_cases():
    """Test edge cases for Fibonacci sequence function."""
    assert fibonacci_sequence(0) == []
    assert fibonacci_sequence(1) == [0]
    # Test negative input
    assert fibonacci_sequence(-1) == []
    assert fibonacci_sequence(-5) == []
    assert fibonacci_sequence(-10) == []


def test_fibonacci_consistency():
    """Test that fibonacci(n) matches the last element of fibonacci_sequence(n+1)."""
    for n in range(0, 15):
        if n == 0:
            # fibonacci(0) = 0, fibonacci_sequence(1) = [0]
            assert fibonacci(0) == fibonacci_sequence(1)[-1]
        else:
            # fibonacci(n) should equal the nth element (0-indexed) in fibonacci_sequence(n+1)
            assert fibonacci(n) == fibonacci_sequence(n + 1)[-1]


def test_fibonacci_large_values():
    """Test Fibonacci function with larger values."""
    # Test some known Fibonacci numbers
    assert fibonacci(15) == 610
    assert fibonacci(20) == 6765
    assert fibonacci(25) == 75025


def test_fibonacci_sequence_large_values():
    """Test Fibonacci sequence function with larger values."""
    seq_15 = fibonacci_sequence(15)
    assert len(seq_15) == 15
    assert seq_15[-1] == 377  # F14 = 377
    assert seq_15[-2] == 233  # F13 = 233
    
    seq_20 = fibonacci_sequence(20)
    assert len(seq_20) == 20
    assert seq_20[-1] == 4181  # F19 = 4181


if __name__ == "__main__":
    pytest.main([__file__, "-v"])