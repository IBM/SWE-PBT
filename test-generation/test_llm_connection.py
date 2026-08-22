#!/usr/bin/env python3
"""
Simple test script to verify LLM connection to Claude via LiteLLM proxy.
Tests the end-to-end API connection and response handling.
"""

import os
import sys
from utility import generate_text, get_llm_client

def test_llm_connection():
    """Test the LLM connection by making a simple API call to Claude."""
    
    print("=" * 60)
    print("LLM Connection Test - Claude via LiteLLM Proxy")
    print("=" * 60)
    
    # Check if API key is set
    api_key = os.getenv("CLAUDE_API")
    if not api_key:
        print("FAILED: CLAUDE_API environment variable is not set")
        print("   Please set the CLAUDE_API environment variable before running this test.")
        return False
    
    print(f"✓ API key found (length: {len(api_key)} chars)")
    
    # Test client initialization
    print("\n[1/3] Testing client initialization...")
    try:
        client = get_llm_client()
        print("Client initialized successfully")
    except Exception as e:
        print(f"FAILED: Could not initialize client")
        print(f"   Error: {e}")
        return False
    
    # Test API call
    print("\n[2/3] Testing API call to Claude...")
    test_prompt = "Say 'Connection successful!' and nothing else."
    
    try:
        response = generate_text(test_prompt, model="claude", temperature=0, max_tokens=100)
        print(f"API call successful")
    except Exception as e:
        print(f"FAILED: API call failed")
        print(f"   Error: {e}")
        return False
    
    # Verify response
    print("\n[3/3] Verifying response...")
    if response and isinstance(response, str) and len(response) > 0:
        print(f"Response received and is valid")
        print(f"\nResponse from Claude:")
        print(f"   {response}")
    else:
        print(f"FAILED: Response is invalid or empty")
        return False
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED - LLM Connection is working!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    success = test_llm_connection()
    sys.exit(0 if success else 1)
