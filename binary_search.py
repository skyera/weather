def binary_search(arr, target):
    """
    Perform a binary search on a sorted list.
    
    Args:
        arr: A sorted list of elements.
        target: The element to search for.
        
    Returns:
        The index of the target if found, otherwise -1.
    """
    left, right = 0, len(arr) - 1
    
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
            
    return -1

if __name__ == "__main__":
    test_list = [1, 3, 5, 7, 9, 11, 13, 15]
    print(f"Index of 9: {binary_search(test_list, 9)}")  # Expected: 4
    print(f"Index of 2: {binary_search(test_list, 2)}")  # Expected: -1
