#!/usr/bin/env python3
"""
Test script to diagnose the debug server issue.
This will help identify why PDB commands are being sent to bash.
"""

from debug_client import DebugClient
import time

def test_debug_connection():
    """Test the debug server connection and command execution."""
    
    print("=" * 60)
    print("Testing Debug Server Connection")
    print("=" * 60)
    
    # Connect to debug server
    client = DebugClient(host='localhost', port=9999)
    
    if not client.connect():
        print("[ERROR] Failed to connect to debug server")
        print("Make sure the debug server is running in the container:")
        print("  python3.10 debug_server.py /testbed/path/to/test.py")
        return False
    
    print("[SUCCESS] Connected to debug server\n")
    
    # Test 1: Check initial state
    print("-" * 60)
    print("Test 1: Checking server state")
    print("-" * 60)
    response = client.send_command("where", verbose=True)
    print(f"Response: {response}")
    print(f"Session: {response.get('session')}")
    print(f"State: {response.get('state')}")
    print(f"Output:\n{response.get('output')}\n")
    
    # Test 2: Try a simple PDB command
    print("-" * 60)
    print("Test 2: Testing 'list' command")
    print("-" * 60)
    response = client.send_command("list", verbose=True)
    print(f"Response: {response}")
    print(f"Session: {response.get('session')}")
    print(f"State: {response.get('state')}")
    print(f"Output:\n{response.get('output')}\n")
    
    # Test 3: Try setting a breakpoint
    print("-" * 60)
    print("Test 3: Setting a breakpoint")
    print("-" * 60)
    response = client.send_command("break /testbed/django/db/models/query.py:690", verbose=True)
    print(f"Response: {response}")
    print(f"Session: {response.get('session')}")
    print(f"State: {response.get('state')}")
    print(f"Output:\n{response.get('output')}\n")
    
    # Test 4: Check if we're in the right session
    print("-" * 60)
    print("Test 4: Verifying session mode")
    print("-" * 60)
    if response.get('session') != 'pdb':
        print(f"[WARNING] Expected 'pdb' session but got '{response.get('session')}'")
        print("This explains why bash commands are being executed!")
        print("\nThe debug server needs to properly initialize PDB mode.")
    else:
        print("[SUCCESS] Server is in PDB mode")
    
    # Close connection
    client.close()
    
    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)
    
    return True

if __name__ == '__main__':
    test_debug_connection()

# Made with Bob
