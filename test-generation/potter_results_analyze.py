#!/usr/bin/env python3
"""
Analyze test results from run directories and generate a CSV report.

This script processes test output files from multiple run iterations,
extracts pass/fail status for regression and reproduction tests,
and generates a CSV report with coverage scores.
"""

import os
import re
import csv
import sys
from pathlib import Path
from typing import Dict, Set, Optional


def extract_test_status(file_path: str) -> Optional[str]:
    """
    Extract test status from a test output file.
    
    Args:
        file_path: Path to the test output file
        
    Returns:
        "pass"   if test passed
        "fail"   if test failed
        "error"  if pytest encountered a collection/runtime error
        "notest" if no tests were collected or run
        None     if file not found or unreadable
    """
    if not os.path.exists(file_path):
        return None
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Strip ANSI colour codes that pytest injects when the terminal
        # supports colour — they sit between the `=+` and the digit/keyword
        # and would otherwise prevent every regex from matching.
        content = re.sub(r'\x1b\[[0-9;]*m', '', content)

        # Primary: per-test status lines (survive session crashes where the
        # final summary line is never printed, e.g. UnicodeEncodeError in conftest).
        if re.search(r'test\.py::\S+ PASSED', content):
            return "pass"
        if re.search(r'test\.py::\S+ FAILED', content):
            return "fail"
        # Fallback: summary line (handles runs without verbose per-test lines)
        if re.search(r'=+\s*\d+\s+passed', content):
            return "pass"
        if re.search(r'=+\s*\d+\s+failed', content):
            return "fail"
        if re.search(r'=+.*\d+\s+error', content):
            return "error"
        if re.search(r'no tests ran|collected 0 items', content):
            return "notest"
        return None
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return None


def calculate_cov_score_from_map(cov_map: Dict) -> float:
    """
    Calculate coverage score from a coverage map.
    
    Args:
        cov_map: Dictionary with structure:
            {
                'filename': {
                    'changed_line': [list of line numbers],
                    'missed_line': [list of line numbers]
                },
                ...
            }
    
    Returns:
        Coverage score (0.0 to 1.0)
    """
    total_changed = 0
    total_missed = 0
    
    for filename, data in cov_map.items():
        changed_lines = set(data.get('changed_line', []))
        missed_lines = set(data.get('missed_line', []))
        
        total_changed += len(changed_lines)
        total_missed += len(missed_lines)
    
    if total_changed == 0:
        return 0.0
    
    return (total_changed - total_missed) / total_changed


def merge_cov_maps_disjunctive(cov_map1: Dict, cov_map2: Dict) -> Dict:
    """
    Merge two coverage maps using disjunctive (OR) logic.
    A line is considered covered if EITHER map covers it.
    
    Args:
        cov_map1: First coverage map
        cov_map2: Second coverage map
    
    Returns:
        Merged coverage map with same structure
    """
    merged = {}
    
    # Get all filenames from both maps
    all_files = set(cov_map1.keys()) | set(cov_map2.keys())
    
    for filename in all_files:
        # Get data from both maps
        data1 = cov_map1.get(filename, {'changed_line': [], 'missed_line': []})
        data2 = cov_map2.get(filename, {'changed_line': [], 'missed_line': []})
        
        # Convert to sets
        changed1 = set(data1.get('changed_line', []))
        changed2 = set(data2.get('changed_line', []))
        missed1 = set(data1.get('missed_line', []))
        missed2 = set(data2.get('missed_line', []))
        
        # Union of all changed lines
        all_changed = changed1 | changed2
        
        # Lines covered by at least one map
        covered1 = changed1 - missed1
        covered2 = changed2 - missed2
        covered_by_either = covered1 | covered2
        
        # Lines not covered by any map
        missed_by_both = all_changed - covered_by_either
        
        # Store in merged map
        merged[filename] = {
            'changed_line': list(all_changed),
            'missed_line': list(missed_by_both)
        }
    
    return merged


def merge_cov_maps_pre_post(cov_map_pre: Dict, cov_map_post: Dict) -> Dict:
    """
    Merge pre-fix (deleted lines) and post-fix (added lines) coverage maps.
    This combines coverage from both the buggy code (deleted) and fixed code (added).
    
    Args:
        cov_map_pre: Coverage map from pre-fix version (deleted lines)
        cov_map_post: Coverage map from post-fix version (added lines)
    
    Returns:
        Merged coverage map with combined changed and missed lines
    """
    merged = {}
    
    # Get all filenames from both maps
    all_files = set(cov_map_pre.keys()) | set(cov_map_post.keys())
    
    for filename in all_files:
        # Get data from both maps
        data_pre = cov_map_pre.get(filename, {'changed_line': [], 'missed_line': []})
        data_post = cov_map_post.get(filename, {'changed_line': [], 'missed_line': []})
        
        # Convert to sets
        changed_pre = set(data_pre.get('changed_line', []))
        changed_post = set(data_post.get('changed_line', []))
        missed_pre = set(data_pre.get('missed_line', []))
        missed_post = set(data_post.get('missed_line', []))
        
        # Combine deleted and added lines
        all_changed = changed_pre | changed_post
        all_missed = missed_pre | missed_post
        
        # Store in merged map
        merged[filename] = {
            'changed_line': list(all_changed),
            'missed_line': list(all_missed)
        }
    
    return merged


def calculate_coverage_scores(instance_dir: Path) -> Dict[str, Optional[float]]:
    """
    Calculate all coverage scores for an instance.
    
    Args:
        instance_dir: Path to the instance directory
        
    Returns:
        Dictionary with coverage scores:
        {
            'regression_pre_cov': score,
            'regression_post_cov': score,
            'regression_combined_cov': score,
            'reproduction_pre_cov': score,
            'reproduction_post_cov': score,
            'reproduction_combined_cov': score,
            'union_pre_cov': score,
            'union_post_cov': score,
            'union_combined_cov': score
        }
    """
    import json
    
    scores = {
        'regression_pre_cov': None,
        'regression_post_cov': None,
        'regression_combined_cov': None,
        'reproduction_pre_cov': None,
        'reproduction_post_cov': None,
        'reproduction_combined_cov': None,
        'union_pre_cov': None,
        'union_post_cov': None,
        'union_combined_cov': None
    }
    
    # Define report file paths
    reports = {
        'regression_pre': instance_dir / "regression__report_test_1_code_0.json",
        'regression_post': instance_dir / "regression__report_test_1_code_1.json",
        'reproduction_pre': instance_dir / "reproduction__report_test_1_code_0.json",
        'reproduction_post': instance_dir / "reproduction__report_test_1_code_1.json",
    }
    
    # Load coverage maps
    cov_maps = {}
    for key, report_path in reports.items():
        if not report_path.exists():
            print(f"  Warning: {report_path.name} not found")
            continue
        
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Get the first (and should be only) instance data
                if not data:
                    print(f"  Warning: {report_path.name} is empty")
                    continue
                    
                instance_data = list(data.values())[0]
                cov_map = instance_data.get('cov_map', {})
                
                if not cov_map:
                    print(f"  Warning: No cov_map in {report_path.name}")
                    continue
                
                if not isinstance(cov_map, dict):
                    print(f"  Warning: cov_map in {report_path.name} is not a dict (got {type(cov_map).__name__}), skipping")
                    continue
                
                cov_maps[key] = cov_map
                
                # Calculate individual score - use _cov suffix for keys
                cov_key = f"{key}_cov"
                scores[cov_key] = calculate_cov_score_from_map(cov_map)
                print(f"  {key}: {scores[cov_key]:.4f}" if scores[cov_key] is not None else f"  {key}: N/A")
                
        except Exception as e:
            print(f"  Error reading {report_path.name}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # Calculate combined scores (pre + post for each test type)
    if 'regression_pre' in cov_maps and 'regression_post' in cov_maps:
        regression_combined_map = merge_cov_maps_pre_post(
            cov_maps['regression_pre'],
            cov_maps['regression_post']
        )
        scores['regression_combined_cov'] = calculate_cov_score_from_map(regression_combined_map)
        print(f"  regression_combined: {scores['regression_combined_cov']:.4f}" if scores['regression_combined_cov'] is not None else "  regression_combined: N/A")
    
    if 'reproduction_pre' in cov_maps and 'reproduction_post' in cov_maps:
        reproduction_combined_map = merge_cov_maps_pre_post(
            cov_maps['reproduction_pre'],
            cov_maps['reproduction_post']
        )
        scores['reproduction_combined_cov'] = calculate_cov_score_from_map(reproduction_combined_map)
        print(f"  reproduction_combined: {scores['reproduction_combined_cov']:.4f}" if scores['reproduction_combined_cov'] is not None else "  reproduction_combined: N/A")
    
    # Calculate union scores
    if 'regression_pre' in cov_maps and 'reproduction_pre' in cov_maps:
        union_pre_map = merge_cov_maps_disjunctive(
            cov_maps['regression_pre'],
            cov_maps['reproduction_pre']
        )
        scores['union_pre_cov'] = calculate_cov_score_from_map(union_pre_map)
        print(f"  union_pre: {scores['union_pre_cov']:.4f}" if scores['union_pre_cov'] is not None else "  union_pre: N/A")
    
    if 'regression_post' in cov_maps and 'reproduction_post' in cov_maps:
        union_post_map = merge_cov_maps_disjunctive(
            cov_maps['regression_post'],
            cov_maps['reproduction_post']
        )
        scores['union_post_cov'] = calculate_cov_score_from_map(union_post_map)
        print(f"  union_post: {scores['union_post_cov']:.4f}" if scores['union_post_cov'] is not None else "  union_post: N/A")
    
    # Calculate union combined score (union of both tests, covering both pre and post)
    if 'regression_pre' in cov_maps and 'regression_post' in cov_maps and 'reproduction_pre' in cov_maps and 'reproduction_post' in cov_maps:
        # First merge regression pre+post
        regression_combined_map = merge_cov_maps_pre_post(
            cov_maps['regression_pre'],
            cov_maps['regression_post']
        )
        # Then merge reproduction pre+post
        reproduction_combined_map = merge_cov_maps_pre_post(
            cov_maps['reproduction_pre'],
            cov_maps['reproduction_post']
        )
        # Finally merge the two combined maps
        union_combined_map = merge_cov_maps_disjunctive(
            regression_combined_map,
            reproduction_combined_map
        )
        scores['union_combined_cov'] = calculate_cov_score_from_map(union_combined_map)
        print(f"  union_combined: {scores['union_combined_cov']:.4f}" if scores['union_combined_cov'] is not None else "  union_combined: N/A")
    
    return scores


def process_instance(instance_dir: Path, run_number: int) -> Optional[Dict[str, str]]:
    """
    Process a single instance directory and extract test results.
    
    Args:
        instance_dir: Path to the instance directory
        run_number: The run iteration number
        
    Returns:
        Dictionary with test results or None if processing failed
    """
    instance_id = instance_dir.name
    
    # Define the four test output files
    files = {
        'regression_pre': 'regression__test_output_test_1_code_0.txt',
        'regression_post': 'regression__test_output_test_1_code_1.txt',
        'reproduction_pre': 'reproduction__test_output_test_1_code_0.txt',
        'reproduction_post': 'reproduction__test_output_test_1_code_1.txt',
    }
    
    # Extract status for each file
    results = {
        'instance_id': instance_id,
        'iter': run_number,
    }
    
    for key, filename in files.items():
        file_path = instance_dir / filename
        status = extract_test_status(str(file_path))
        results[key] = status if status else "unknown"

    # Calculate all coverage scores
    coverage_scores = calculate_coverage_scores(instance_dir)
    results.update(coverage_scores)

    test_status_keys = ['regression_pre', 'regression_post', 'reproduction_pre', 'reproduction_post']

    # Skip only if every status is truly unrecognisable (output files missing or unreadable)
    if all(results.get(k) == "unknown" for k in test_status_keys):
        print(f"  Skipping {instance_id}: No test output files found")
        return None

    # If no tests ran at all, coverage is meaningless — force all scores to 0
    if all(results.get(k) in ("notest", "error") for k in test_status_keys):
        for cov_key in list(coverage_scores.keys()):
            results[cov_key] = 0.0

    return results


def analyze_test_results(base_dir: str):
    """
    Analyze test results from run directories and generate CSV report.
    
    Args:
        base_dir: Path to the directory containing run subdirectories
    """
    base_path = Path(base_dir)
    
    if not base_path.exists():
        print(f"Error: Directory {base_dir} does not exist")
        sys.exit(1)
    
    # Find all run directories (run0, run1, run2, etc.)
    run_dirs = []
    for item in base_path.iterdir():
        if item.is_dir() and item.name.startswith('run'):
            try:
                # Extract the number from runN
                run_num = int(item.name[3:])
                run_dirs.append((run_num, item))
            except ValueError:
                continue
    
    if not run_dirs:
        print(f"Error: No run directories found in {base_dir}")
        sys.exit(1)
    
    # Sort in reverse order (highest to lowest)
    run_dirs.sort(reverse=True, key=lambda x: x[0])
    
    print(f"Found {len(run_dirs)} run directories: {[f'run{n}' for n, _ in run_dirs]}")
    
    # Track processed instances
    processed_instances: Set[str] = set()
    
    # Prepare CSV output
    output_csv = base_path / 'test_results_summary.csv'
    csv_headers = [
        'instance_id',
        'iter',
        'RegPre',
        'RegPost',
        'RepPre',
        'RepPost',
        'RegCov',
        'RepCov',
        'UnionCov',
    ]
    
    # Open CSV file for writing
    with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
        writer.writeheader()
        
        # Process each run directory in reverse order
        for run_num, run_dir in run_dirs:
            print(f"\nProcessing run{run_num}...")
            
            # Look for 'gold' subdirectory (common pattern in the logs)
            gold_dir = run_dir / 'gold'
            if not gold_dir.exists():
                # Try processing run_dir directly if no gold subdirectory
                gold_dir = run_dir
            
            # Find all instance directories
            instance_dirs = [d for d in gold_dir.iterdir() if d.is_dir()]
            
            for instance_dir in instance_dirs:
                instance_id = instance_dir.name
                
                # Skip if already processed
                if instance_id in processed_instances:
                    continue
                
                # Process the instance
                results = process_instance(instance_dir, run_num)
                
                if results:
                    # Format coverage scores
                    def format_score(score):
                        return f"{score:.4f}" if score is not None else "N/A"
                    
                    # Write to CSV
                    csv_row = {
                        'instance_id': results['instance_id'],
                        'iter': results['iter'],
                        # Regression test status (pass/fail/unknown) - from test output files
                        'RegPre': results.get('regression_pre', 'unknown'),
                        'RegPost': results.get('regression_post', 'unknown'),
                        # Reproduction test status (pass/fail/unknown) - from test output files
                        'RepPre': results.get('reproduction_pre', 'unknown'),
                        'RepPost': results.get('reproduction_post', 'unknown'),
                        # Combined coverage score (deleted + added lines)
                        'RegCov': format_score(results.get('regression_combined_cov')),
                        'RepCov': format_score(results.get('reproduction_combined_cov')),
                        'UnionCov': format_score(results.get('union_combined_cov')),
                    }
                    writer.writerow(csv_row)
                    csvfile.flush()  # Ensure data is written immediately
                    
                    # Mark as processed
                    processed_instances.add(instance_id)
                    print(f"  Processed: {instance_id} (iter={run_num})")
    
    print(f"\n✓ Analysis complete!")
    print(f"✓ Processed {len(processed_instances)} unique instances")
    print(f"✓ Results saved to: {output_csv}")


def main():
    """Main entry point for the script."""
    if len(sys.argv) != 2:
        print("Usage: python results_analyze_p2p_f2p.py <path_to_directory>")
        print("\nExample:")
        print("  python results_analyze_p2p_f2p.py Test_execution/logs/run_evaluation/epotter")
        sys.exit(1)
    
    base_dir = sys.argv[1]
    analyze_test_results(base_dir)


if __name__ == "__main__":
    main()

# Made with Bob
