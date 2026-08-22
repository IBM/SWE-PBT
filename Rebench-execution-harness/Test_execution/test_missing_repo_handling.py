#!/usr/bin/env python3
"""
Test script to verify that missing repos/versions are handled with default pytest-based specs.
"""

from tddbench.harness.log_parsers import get_parser_for_repo, parse_log_pytest_v2
from tddbench.harness.test_spec import make_test_spec
from tddbench.harness.constants import MAP_REPO_VERSION_TO_SPECS

def test_parser_fallback():
    """Test that get_parser_for_repo returns default parser for unknown repos"""
    print("Testing parser fallback for unknown repos...")
    
    # Test with a known repo
    known_repo = "django/django"
    parser = get_parser_for_repo(known_repo)
    print(f"✓ Known repo '{known_repo}': {parser.__name__}")
    
    # Test with an unknown repo (pgmpy/pgmpy)
    unknown_repo = "pgmpy/pgmpy"
    parser = get_parser_for_repo(unknown_repo)
    assert parser == parse_log_pytest_v2, f"Expected parse_log_pytest_v2, got {parser.__name__}"
    print(f"✓ Unknown repo '{unknown_repo}': {parser.__name__} (default)")
    
    print("✓ Parser fallback test passed!\n")

def test_test_spec_fallback():
    """Test that make_test_spec handles unknown repos with default specs"""
    print("Testing TestSpec creation for unknown repos...")
    
    # Create a mock instance for pgmpy/pgmpy
    mock_instance = {
        "instance_id": "pgmpy__pgmpy-3137",
        "repo": "pgmpy/pgmpy",
        "version": "1.0",
        "base_commit": "abc123",
        "problem_statement": "Test problem",
        "hints_text": "",
        "test_patch": "",
    }
    
    # Verify repo is not in MAP_REPO_VERSION_TO_SPECS
    assert "pgmpy/pgmpy" not in MAP_REPO_VERSION_TO_SPECS, "pgmpy/pgmpy should not be in MAP_REPO_VERSION_TO_SPECS"
    print(f"✓ Confirmed 'pgmpy/pgmpy' is not in MAP_REPO_VERSION_TO_SPECS")
    
    # Create TestSpec - should not raise KeyError
    try:
        test_spec = make_test_spec(mock_instance)
        print(f"✓ TestSpec created successfully for unknown repo")
        print(f"  - instance_id: {test_spec.instance_id}")
        print(f"  - repo: {test_spec.repo}")
        print(f"  - version: {test_spec.version}")
        
        # Verify default pytest command is in eval_script
        eval_script = test_spec.eval_script
        assert "pytest -xvs" in eval_script, "Default pytest command should be in eval_script"
        print(f"✓ Default pytest command found in eval_script")
        
    except KeyError as e:
        print(f"✗ KeyError raised: {e}")
        raise
    
    print("✓ TestSpec fallback test passed!\n")

if __name__ == "__main__":
    print("=" * 60)
    print("Testing default pytest-based specs for missing repos/versions")
    print("=" * 60 + "\n")
    
    try:
        test_parser_fallback()
        test_test_spec_fallback()
        
        print("=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"✗ Test failed: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        exit(1)

# Made with Bob
