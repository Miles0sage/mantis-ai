def fibonacci(n):
    """
    Calculate the nth Fibonacci number.
    
    Args:
        n (int): The position in the Fibonacci sequence (0-indexed)
        
    Returns:
        int: The nth Fibonacci number
    """
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    else:
        a, b = 0, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        return b


def fibonacci_sequence(length):
    """
    Generate a Fibonacci sequence of given length.
    
    Args:
        length (int): Number of elements in the sequence
        
    Returns:
        list: A list containing the Fibonacci sequence
    """
    if length <= 0:
        return []
    elif length == 1:
        return [0]
    elif length == 2:
        return [0, 1]
    else:
        sequence = [0, 1]
        for i in range(2, length):
            next_num = sequence[i-1] + sequence[i-2]
            sequence.append(next_num)
        return sequence


# Example usage
if __name__ == "__main__":
    print("The 10th Fibonacci number is:", fibonacci(10))
    print("Fibonacci sequence of length 10:", fibonacci_sequence(10))