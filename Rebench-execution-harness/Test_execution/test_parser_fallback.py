#!/usr/bin/env python3
"""
Simple test to verify parser fallback works for unknown repos.
"""

import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import only what we need
from tddbench.harness.log_parsers import get_parser_for_repo, parse_log_pytest_v2, MAP_REPO_TO_PARSER

def test_parser_fallback():
    """Test that get_parser_for_repo returns default parser for unknown repos"""
    print("Testing parser fallback for unknown repos...")
    print(f"Total known repos in MAP_REPO_TO_PARSER: {len(MAP_REPO_TO_PARSER)}")
    
    # Test with a known repo
    known_repo = "django/django"
    parser = get_parser_for_repo(known_repo)
    print(f"\n✓ Known repo '{known_repo}':")
    print(f"  Parser: {parser.__name__}")
    assert known_repo in MAP_REPO_TO_PARSER, f"{known_repo} should be in MAP_REPO_TO_PARSER"
    
    # Test with an unknown repo (pgmpy/pgmpy)
    unknown_repo = "pgmpy/pgmpy"
    parser = get_parser_for_repo(unknown_repo)
    print(f"\n✓ Unknown repo '{unknown_repo}':")
    print(f"  Parser: {parser.__name__}")
    assert parser == parse_log_pytest_v2, f"Expected parse_log_pytest_v2, got {parser.__name__}"
    assert unknown_repo not in MAP_REPO_TO_PARSER, f"{unknown_repo} should NOT be in MAP_REPO_TO_PARSER"
    print(f"  ✓ Correctly falls back to default parser (parse_log_pytest_v2)")
    
    # Test with another unknown repo
    another_unknown = "some/random-repo"
    parser = get_parser_for_repo(another_unknown)
    print(f"\n✓ Another unknown repo '{another_unknown}':")
    print(f"  Parser: {parser.__name__}")
    assert parser == parse_log_pytest_v2, f"Expected parse_log_pytest_v2, got {parser.__name__}"
    print(f"  ✓ Correctly falls back to default parser (parse_log_pytest_v2)")
    
    print("\n" + "=" * 60)
    print("✓ All parser fallback tests passed!")
    print("=" * 60)

if __name__ == "__main__":
    print("=" * 60)
    print("Testing parser fallback for missing repos")
    print("=" * 60 + "\n")
    
    try:
        test_parser_fallback()
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"✗ Test failed: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        exit(1)

# Made with Bob
