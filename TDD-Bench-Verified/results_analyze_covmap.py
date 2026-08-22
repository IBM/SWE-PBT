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
    Extract test status (pass/fail) from a test output file.
    
    Args:
        file_path: Path to the test output file
        
    Returns:
        "pass" if test passed, "fail" if test failed, None if file not found or status unclear
    """
    if not os.path.exists(file_path):
        return None
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        # Look for pass/fail patterns
        if re.search(r'=+\s*1\s+passed', content):
            return "pass"
        elif re.search(r'=+\s*1\s+failed', content):
            return "fail"
        else:
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


def get_instance_data(instance_dir: Path) -> Optional[Dict]:
    """
    Get instance data from report.json.
    
    Args:
        instance_dir: Path to the instance directory
        
    Returns:
        Dictionary with 'resolved' and 'cov_score' or None if not found
    """
    import json
    
    report_file = instance_dir / "report.json"
    if not report_file.exists():
        print(f"  Warning: report.json not found")
        return None
    
    try:
        with open(report_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not data:
                print(f"  Warning: report.json is empty")
                return None
            
            instance_data = list(data.values())[0]
            
            # Get resolved status
            resolved = instance_data.get('resolved', False)
            
            # Get coverage score
            cov_score = instance_data.get('cov_score')
            if cov_score is None:
                # Try to calculate from cov_map if cov_score not present
                cov_map = instance_data.get('cov_map', {})
                if cov_map:
                    cov_score = calculate_cov_score_from_map(cov_map)
            
            return {
                'resolved': resolved,
                'cov_score': cov_score
            }
            
    except Exception as e:
        print(f"  Error reading report.json: {e}")
        import traceback
        traceback.print_exc()
        return None


def process_instance(instance_dir: Path, run_number: int) -> Optional[Dict]:
    """
    Process a single instance directory and extract test results.
    
    Args:
        instance_dir: Path to the instance directory
        run_number: The run iteration number
        
    Returns:
        Dictionary with test results or None if processing failed
    """
    instance_id = instance_dir.name
    
    # Get instance data from report.json
    instance_data = get_instance_data(instance_dir)
    
    if instance_data is None:
        print(f"  Skipping {instance_id}: No valid report.json found")
        return None
    
    results = {
        'instance_id': instance_id,
        'iter': run_number,
        'resolved': instance_data.get('resolved', False),
        'cov_score': instance_data.get('cov_score')
    }
    
    return results


def analyze_test_results(base_dir: str):
    """
    Analyze test results from run directories and generate CSV report.
    
    Args:
        base_dir: Path to the directory containing run subdirectories or instance directories
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
    
    # If no run directories found, treat base_dir as a single run
    if not run_dirs:
        print(f"No run directories found, treating {base_dir} as a single run")
        run_dirs = [(0, base_path)]
    else:
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
        'resolved',
        'cov_score',
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
                    # Format coverage score
                    def format_score(score):
                        return f"{score:.4f}" if score is not None else "N/A"
                    
                    # Write to CSV
                    csv_row = {
                        'instance_id': results['instance_id'],
                        'iter': results['iter'],
                        'resolved': str(results.get('resolved', False)),
                        'cov_score': format_score(results.get('cov_score')),
                    }
                    writer.writerow(csv_row)
                    csvfile.flush()  # Ensure data is written immediately
                    
                    # Mark as processed
                    processed_instances.add(instance_id)
                    print(f"  Processed: {instance_id} (iter={run_num}, resolved={results.get('resolved')}, cov={format_score(results.get('cov_score'))})")
    
    print(f"\n✓ Analysis complete!")
    print(f"✓ Processed {len(processed_instances)} unique instances")
    print(f"✓ Results saved to: {output_csv}")


def main():
    """Main entry point for the script."""
    if len(sys.argv) != 2:
        print("Usage: python results_analyze_p2p_f2p.py <path_to_directory>")
        print("\nExample:")
        print("  python results_analyze_p2p_f2p.py Test_execution/logs/run_evaluation/eotter")
        sys.exit(1)
    
    base_dir = sys.argv[1]
    analyze_test_results(base_dir)


if __name__ == "__main__":
    main()

# Made with Bob
