#!/usr/bin/env python3
"""
Smoke test script to verify coverage feedback extraction.
Tests that we can correctly map line numbers from coverage reports to source code.
"""

import json
import os
import sys
from pathlib import Path
import docker

# Add Test_execution to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Test_execution'))

from tddbench.harness.docker_utils import copy_from_container
from tddbench.harness.test_spec import make_test_spec
from tddbench.harness.utils import load_tddbench_dataset
from tddbench.harness.constants import SWEbenchInstance
from typing import cast

# Needed for extracting multi-line statements
import ast


def find_function_name_at_line(source_code, target_line_num):
    """
    Find the name of the function containing the target line.
    
    Args:
        source_code: Full source code as string
        target_line_num: Line number (1-based)
    
    Returns:
        str: Function name, or "<module>" if not in a function
    """
    try:
        tree = ast.parse(source_code)
        
        # Find all function definitions
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
                    if node.lineno <= target_line_num <= node.end_lineno:
                        return node.name
        
        return "<module>"
    except:
        return "<unknown>"


def extract_statement_at_line(source_code, target_line_num):
    """
    Extract statement at the given line number.
    - For simple single-line statements: returns just that line
    - For multi-line statements (function calls, list comprehensions, etc.): returns the complete statement
    - For compound statements (if/for/while/try): returns ONLY the header line, not the entire block
    
    Args:
        source_code: Full source code as string
        target_line_num: Line number (1-based)
    
    Returns:
        str: Statement source code with indentation preserved
    """
    try:
        lines = source_code.split('\n')
        if not (0 < target_line_num <= len(lines)):
            return ""
        
        # Parse the source code with Python's AST
        tree = ast.parse(source_code)
        
        # Compound statement types where we only want the header line
        compound_types = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With,
                         ast.AsyncWith, ast.Try, ast.FunctionDef, ast.AsyncFunctionDef,
                         ast.ClassDef)
        
        # Simple statement types where we want the full statement (may span multiple lines)
        simple_statement_types = (
            ast.Return, ast.Delete, ast.Assign, ast.AugAssign, ast.AnnAssign,
            ast.Raise, ast.Assert, ast.Import, ast.ImportFrom,
            ast.Global, ast.Nonlocal, ast.Expr, ast.Pass, ast.Break, ast.Continue
        )
        
        # Find the smallest node that contains our target line
        best_node = None
        best_span = float('inf')
        
        for node in ast.walk(tree):
            if isinstance(node, simple_statement_types + compound_types):
                if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
                    if node.lineno <= target_line_num <= node.end_lineno:
                        span = node.end_lineno - node.lineno
                        if span < best_span:
                            best_node = node
                            best_span = span
        
        if best_node:
            # For compound statements, only return the header line
            if isinstance(best_node, compound_types):
                return lines[best_node.lineno - 1].rstrip()
            
            # For simple statements, return the full statement (may be multi-line)
            else:
                statement_lines = lines[best_node.lineno - 1:best_node.end_lineno]
                return '\n'.join(line.rstrip() for line in statement_lines)
    
    except Exception as e:
        pass
    
    # Fallback to single line
    if 0 < target_line_num <= len(lines):
        return lines[target_line_num - 1].rstrip()
    return ""


def extract_coverage_feedback(instance_id, attempt, container_name, run_id, branch_type="regression", focal_files=None, filter_by="function"):
    """
    Extract source code for missed lines/branches from RAW coverage text report.
    Parses the unfiltered coverage report to get ALL missed lines/branches filtered by focal files or focal functions.
    
    Args:
        instance_id: Instance identifier (e.g., "django__django-11138")
        attempt: Iteration number (0-based)
        container_name: Docker container name
        run_id: Run identifier for log paths (e.g., "potter_5iter_cov")
        branch_type: Which branch is being repaired - "regression" or "reproduction"
                    (determines which coverage file to read)
        focal_files: Set of focal file paths to filter coverage (e.g., {"/testbed/django/db/backends/sqlite3/operations.py"})
                    If None, loads from localization file
        filter_by: Filtering mode - "file" or "function"
                  - "file": Filter by focal files only (default behavior)
                  - "function": Filter by focal functions (match function names from localization)
    
    Returns:
        str: Formatted string with missed lines and edges, or empty string if none found
    """
    # Load focal files and functions from localization if not provided
    focal_functions = {}  # {filepath: set(function_names)}
    
    if focal_files is None or filter_by == "function":
        localization_path = f"{Path(__file__).parent.parent.parent.parent}/model_output/{instance_id}_localization_claude_base.json"
        if os.path.exists(localization_path):
            with open(localization_path) as f:
                localization_data = json.load(f)
                if localization_data and len(localization_data) > 0:
                    file_function = localization_data[0].get('file_function', {})
                    
                    if focal_files is None:
                        # Convert to set of full paths with /testbed prefix
                        focal_files = {f"/testbed/{filepath}" for filepath in file_function.keys()}
                    
                    # Load focal functions for function-level filtering
                    if filter_by == "function":
                        for filepath, functions in file_function.items():
                            full_path = f"/testbed/{filepath}"
                            focal_functions[full_path] = set(functions)
        
        if not focal_files:
            print(f"[coverage feedback] No focal files found for {instance_id}")
            return ""
        
        if filter_by == "function" and not focal_functions:
            print(f"[coverage feedback] No focal functions found for {instance_id}")
            return ""
    
    # Read RAW text coverage report (unfiltered by gold patch)
    # Use branch_type to determine which coverage file to read
    txt_report_path = f"{Path(__file__).parent.parent.parent}/logs/run_evaluation/{run_id}/run{attempt}/gold/{instance_id}/{branch_type}__test_coverage_test_1_code_0.txt"

    if not os.path.exists(txt_report_path):
        return ""
    
    # Parse the text coverage report
    # Format: /path/to/file.py    Stmts   Miss Branch BrPart  Cover   Missing
    #         Line numbers and branches are in the "Missing" column
    file_coverage = {}  # {filepath: {'lines': [int], 'edges': [(int, int)]}}
    
    with open(txt_report_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('Name') or line.startswith('---'):
                continue
            
            # Split by whitespace, file path is first column
            parts = line.split()
            if len(parts) < 6:
                continue
            
            filepath = parts[0]
            if not filepath.startswith('/testbed'):
                continue
            
            # Filter to only focal files
            if focal_files and filepath not in focal_files:
                continue
            
            # The "Missing" column starts after the percentage (6th column)
            # and continues to the end, so we need to join all remaining parts
            # Example: ['35-40,', '43-61,', '72,', ..., '217->223,', ..., '326-330']
            missing_parts = parts[6:]  # Everything after the percentage column
            missing_str = ''.join(missing_parts)  # Join without spaces to get: "35-40,43-61,72,...,217->223,...,326-330"
            
            if missing_str == '0' or not missing_str:
                continue
            
            # Parse missed items (lines and branches)
            missed_lines = []
            missed_edges = []
            
            for item in missing_str.split(','):
                item = item.strip()
                if '->' in item:
                    # This is a branch (edge)
                    if 'exit' not in item:
                        try:
                            from_line, to_line = item.split('->')
                            missed_edges.append((int(from_line), int(to_line)))
                        except:
                            pass
                elif '-' in item and '->' not in item:
                    # This is a line range like "62-68"
                    try:
                        start, end = item.split('-')
                        missed_lines.extend(range(int(start), int(end) + 1))
                    except:
                        pass
                else:
                    # Single line number
                    try:
                        missed_lines.append(int(item))
                    except:
                        pass
            
            if missed_lines or missed_edges:
                file_coverage[filepath] = {
                    'lines': missed_lines,
                    'edges': missed_edges
                }
    
    # Now extract source code for missed lines/edges
    missed_lines_dict = {}  # Key: <path>:<line_number>, Value: dict with function, line, source
    missed_edges_dict = {}  # Key: <path>:<from>-><to>, Value: dict with function, lines, sources
    
    for filepath, coverage_data in file_coverage.items():
        missed_line_nums = coverage_data['lines']
        missed_edge_tuples = coverage_data['edges']
        
        if not missed_line_nums and not missed_edge_tuples:
            continue
        
        # Copy file from container to temp location
        container_filepath = filepath if filepath.startswith("/testbed") else f"/testbed{filepath}"
        temp_file = f"/tmp/test_{instance_id}_{os.path.basename(filepath)}"
        
        try:
            print(f"   [coverage feedback] Copying from container: {container_filepath}")
            # Get the actual container object
            client = docker.from_env()
            
            # Validate container_name before using it
            if not container_name:
                print(f"   [coverage feedback] Warning: container_name is empty, skipping file copy")
                continue
            
            container = client.containers.get(container_name)
            # Convert paths to Path objects
            copy_from_container(container, Path(container_filepath), Path(temp_file))
        except Exception as e:
            import traceback
            traceback.print_exc()
            continue
        
        # Read file content for AST parsing
        try:
            with open(temp_file) as f:
                source_code = f.read()
            source_lines = source_code.split('\n')
        except Exception as e:
            continue
        
        # Extract missed line source code
        for line_num in missed_line_nums:
            if 0 < line_num <= len(source_lines):
                statement_code = extract_statement_at_line(source_code, line_num)
                func_name = find_function_name_at_line(source_code, line_num)
                
                # Apply function-level filtering if enabled
                if filter_by == "function":
                    # Only include if the function is in focal_functions for this file
                    if filepath in focal_functions and func_name not in focal_functions[filepath]:
                        continue
                
                line_key = f"{filepath}:{line_num}"
                missed_lines_dict[line_key] = {
                    'file': filepath,
                    'function': func_name,
                    'line': line_num,
                    'source': statement_code
                }
        
        # Extract missed edges (branches)
        for from_line, to_line in missed_edge_tuples:
            if 0 < from_line <= len(source_lines) and 0 < to_line <= len(source_lines):
                src_code = extract_statement_at_line(source_code, from_line)
                dst_code = extract_statement_at_line(source_code, to_line)
                src_func = find_function_name_at_line(source_code, from_line)
                dst_func = find_function_name_at_line(source_code, to_line)
                
                # Apply function-level filtering if enabled
                if filter_by == "function":
                    # Only include if both functions are in focal_functions for this file
                    if filepath in focal_functions:
                        if src_func not in focal_functions[filepath] or dst_func not in focal_functions[filepath]:
                            continue
                
                edge_key = f"{filepath}:{from_line}->{to_line}"
                missed_edges_dict[edge_key] = {
                    'file': filepath,
                    'function_from': src_func,
                    'function_to': dst_func,
                    'line_from': from_line,
                    'line_to': to_line,
                    'source_from': src_code,
                    'source_to': dst_code
                }
        
        # Clean up temp file
        try:
            os.remove(temp_file)
        except:
            pass
    
    # Format the output string with structured format
    output_parts = []
    
    if missed_lines_dict:
        output_parts.append("-" * 15)
        output_parts.append("MISSED LINES:")
        output_parts.append("-" * 15)
        for key, info in missed_lines_dict.items():
            output_parts.append("")
            output_parts.append(f"{info['file']}::{info['function']}:{info['line']} --{info['source']}")
    
    if missed_edges_dict:
        if output_parts:
            output_parts.append("")  # Empty line separator
        output_parts.append("-" * 15)
        output_parts.append("MISSED EDGES:")
        output_parts.append("-" * 15)
        for key, info in missed_edges_dict.items():
            output_parts.append("")
            output_parts.append(f"{info['file']}::{info['function_from']}:{info['line_from']}->{info['line_to']}")
            output_parts.append(f"Source Code From: {info['source_from']}")
            output_parts.append(f"Source Code To: {info['source_to']}")
    
    formatted_output = "\n".join(output_parts)
    
    # Print for debugging
    if formatted_output:
        # print("-" * 30)
        # print(f"Formatted Coverage Feedback:")
        # print("-" * 30)
        # print(formatted_output)
        # print("-" * 30)
        return formatted_output
    else:
        # print("No missed lines or edges found (perfect coverage or no focal method changes)")
        return ""


def main():
    """Run smoke test on a sample instance."""
    
    # Test configuration
    instance_id = "django__django-11138"  # Using an instance with actual missed lines
    attempt = 0
    dataset_name = "../TDD_Bench.json"
    run_id = "epotter"  # Use the actual run_id from docker ps
    
    print("Coverage Extraction Smoke Test")
    print("=" * 80)
    
    # Get container name
    try:
        dataset = load_tddbench_dataset(dataset_name, instance_ids=[instance_id])
        instance_obj = cast(SWEbenchInstance, dataset[0])
        test_spec = make_test_spec(instance_obj)
        container_name = test_spec.get_instance_container_name(run_id)
        print(f"Container name: {container_name}")
    except Exception as e:
        print(f"Failed to get container name: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Run test with file-level filtering
    try:
        print("\n" + "=" * 80)
        print("Testing with filter_by='file' (default)")
        print("=" * 80)
        result_file = extract_coverage_feedback(
            instance_id=instance_id,
            attempt=attempt,
            container_name=container_name,
            run_id=run_id,
            branch_type="regression",
            filter_by="file"
        )
        
        if result_file is not False:  # False means error, empty string is valid
            print(f"\nFile-level filtering test PASSED!")
            print(f"Returned string length: {len(result_file) if isinstance(result_file, str) else 0} characters")
        else:
            print(f"\nFile-level filtering test FAILED!")
            return 1
    except Exception as e:
        print(f"\nFile-level filtering test ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Run test with function-level filtering
    try:
        print("\n" + "=" * 80)
        print("Testing with filter_by='function'")
        print("=" * 80)
        result_function = extract_coverage_feedback(
            instance_id=instance_id,
            attempt=attempt,
            container_name=container_name,
            run_id=run_id,
            branch_type="regression",
            filter_by="function"
        )
        
        if result_function is not False:  # False means error, empty string is valid
            print(f"\nFunction-level filtering test PASSED!")
            print(f"Returned string length: {len(result_function) if isinstance(result_function, str) else 0} characters")
            print(f"\nComparison:")
            print(f"  File-level result length: {len(result_file) if isinstance(result_file, str) else 0}")
            print(f"  Function-level result length: {len(result_function) if isinstance(result_function, str) else 0}")
            return 0
        else:
            print(f"\nFunction-level filtering test FAILED!")
            return 1
            
    except Exception as e:
        print(f"\nSmoke test ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

# Made with Bob
