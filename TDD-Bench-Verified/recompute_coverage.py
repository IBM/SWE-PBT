#!/usr/bin/env python3
"""
Recompute coverage metrics from existing test results without rerunning tests.

This script reads existing coverage reports and patches from log directories,
recomputes the changed_line and missed_line metrics using the updated
calculate_coverage function, and updates the report JSON files.

Usage:
    python recompute_coverage.py --log-dir Test_execution/logs/run_evaluation/eotter/run0
"""

import json
import os
import sys
from pathlib import Path
from argparse import ArgumentParser


def calculate_coverage(filename, coverage, start_code_list, is_before):
    """
    Updated calculate_coverage function with dynamic field index detection.
    """
    # Normalize filename - remove leading slash if present, then add /testbed
    filename = filename.lstrip('/')

    coverage = coverage.split("\n")

    # Determine the field index for "Missing" column by checking the header
    # Header format with branch: "Name Stmts Miss Branch BrPart Cover Missing"
    # Header format without branch: "Name Stmts Miss Cover Missing"
    missing_field_index = 6  # Default for branch coverage
    for line in coverage:
        if line.strip().startswith("Name"):
            # Normalize spaces in header
            header = line.strip()
            while header.find("  ") != -1:
                header = header.replace("  ", " ")
            header_fields = header.split(" ")
            # Count fields: with branch=7 fields, without branch=5 fields
            if len(header_fields) == 5:
                missing_field_index = 4  # Without branch coverage
            elif len(header_fields) == 7:
                missing_field_index = 6  # With branch coverage
            break

    # Find the relevant line from the coverage file
    target_line = ""
    for line in coverage:
        if line.startswith(filename):
            target_line = line.strip()
            break
    
    if not target_line:
        return [], []
    
    while target_line.find("  ") != -1:
        target_line = target_line.replace("  ", " ")

    missinglines = target_line.split(" ")[missing_field_index:]

    # Making list of missing lines
    missing_lines = []
    for item in missinglines:
        if not item:
            continue
        item = item.replace(",", "")
        
        # Skip branch coverage items (containing "->")
        # These should be tracked separately as branch coverage, not line coverage
        if item.find("->") != -1:
            # For now, skip these entirely - they represent edges, not lines
            continue
        elif item.find("-") != -1 and item.find("exit") == -1:
            # Line range like "62-68"
            item = item.split('-')
            try:
                for i in range(int(item[0]), int(item[1]) + 1):
                    missing_lines.append(i)
            except (ValueError, IndexError):
                pass
        elif item.find("-") != -1 and item.find("exit") != -1:
            # Exit branch like "209-exit" - skip for now
            continue
        else:
            # Single line number
            try:
                missing_lines.append(int(item))
            except ValueError:
                pass

    # Making list of changed lines from patch
    changed_line = []
    for i in range(len(start_code_list)):
        start_code = start_code_list[i]
        start = start_code[0]
        code = start_code[1]
        code = code.split("\n")

        if is_before:
            j = 0
            while j < len(code):
                if code[j].strip().startswith("+"):
                    del code[j]
                    continue
                j = j + 1
        else:
            j = 0
            while j < len(code):
                if code[j].strip().startswith("-"):
                    del code[j]
                    continue
                j = j + 1

        for j in range(0, len(code)):
            if is_before:
                if code[j].strip().startswith("---"):
                    continue
                if code[j].strip().startswith("-"):
                    temp = code[j].replace("-", "")
                    if temp.strip() == "":
                        continue
                    if temp.strip().startswith("#"):
                        continue
                    changed_line.append(j + start)
            else:
                if code[j].strip().startswith("+++"):
                    continue
                if code[j].strip().startswith("+"):
                    temp = code[j].replace("+", "")
                    if temp.strip() == "":
                        continue
                    if temp.strip().startswith("#"):
                        continue
                    changed_line.append(j + start)

    # Find missed lines
    missed_line = []
    for item in changed_line:
        if item in missing_lines:
            missed_line.append(item)

    return changed_line, missed_line


def compute_patch_coverage(patch_text, coverage_report, is_before):
    """
    Compute coverage metrics for a code patch.
    """
    if not patch_text or not coverage_report:
        return {}

    patch_text_segments = patch_text.split("+++ b")
    cov_map = {}

    for j in range(1, len(patch_text_segments)):
        focus_text = patch_text_segments[j]
        filename = patch_text_segments[j].split("\n")[0].strip()
        segment_count = int(len(focus_text.split("@@")) / 2)
        start_code_list = []

        for i in range(0, segment_count):
            # Parse line numbers from hunk header
            line_index = 0 if is_before else 1
            lines = focus_text.split("@@")[2 * i + 1].strip().split(" ")[line_index]
            start = abs(int((lines.split(",")[0]))) - 1

            code = focus_text.split("@@")[2 * i + 2]
            start_code = (start, code)
            start_code_list.append(start_code)

        changed_line, missed_line = calculate_coverage(
            filename, coverage_report, start_code_list, is_before
        )
        cov_map[filename] = {
            'changed_line': changed_line,
            'missed_line': missed_line
        }

    return cov_map


def recompute_report(report_path, coverage_path, patch_text, is_before=False):
    """
    Recompute coverage for a single report file.
    """
    print(f"Processing: {report_path}")
    
    # Read existing report
    with open(report_path, 'r') as f:
        report = json.load(f)
    
    # Read coverage report
    with open(coverage_path, 'r') as f:
        coverage_report = f.read()
    
    # Recompute coverage
    new_cov_map = compute_patch_coverage(patch_text, coverage_report, is_before)
    
    # Update report with coverage map and ensure changed_lines/missed_lines exist
    for instance_id in report:
        report[instance_id]['cov_map'] = new_cov_map
        
        # Create changed_lines and missed_lines keys if they don't exist
        if 'changed_lines' not in report[instance_id]:
            report[instance_id]['changed_lines'] = {}
        if 'missed_lines' not in report[instance_id]:
            report[instance_id]['missed_lines'] = {}
        
        # Populate changed_lines and missed_lines from cov_map
        for filename, cov_data in new_cov_map.items():
            report[instance_id]['changed_lines'][filename] = cov_data['changed_line']
            report[instance_id]['missed_lines'][filename] = cov_data['missed_line']
    
    # Write updated report
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=4)
    
    print(f"  Updated: {report_path}")
    return new_cov_map


def process_instance_dir(instance_dir):
    """
    Process all reports in an instance directory.
    """
    instance_dir = Path(instance_dir)
    print(f"\nProcessing instance: {instance_dir.name}")
    
    # Check for simple structure (report.json + patch.diff + test_coverage.txt)
    simple_report = instance_dir / "report.json"
    simple_patch = instance_dir / "patch.diff"
    simple_coverage = instance_dir / "test_coverage.txt"
    
    if simple_report.exists() and simple_patch.exists() and simple_coverage.exists():
        print(f"  Found simple structure (report.json + patch.diff)")
        
        # Read patch
        with open(simple_patch, 'r') as f:
            patch_text = f.read()
        
        # Process the report (is_before=False for "after" patch state)
        recompute_report(simple_report, simple_coverage, patch_text, is_before=False)
        return
    
    # Otherwise, look for complex structure with multiple report files
    report_files = list(instance_dir.glob("*__report_*.json"))
    
    if not report_files:
        print(f"  No report files found")
        return
    
    for report_file in report_files:
        # Determine if this is regression or reproduction
        is_regression = "regression" in report_file.name
        is_before = is_regression  # regression tests the "before" state
        
        # Extract test and code versions from filename
        # Format: {type}__report_test_{test_ver}_code_{code_ver}.json
        parts = report_file.stem.split("_")
        test_ver = None
        code_ver = None
        for i, part in enumerate(parts):
            if part == "test" and i + 1 < len(parts):
                test_ver = parts[i + 1]
            if part == "code" and i + 1 < len(parts):
                code_ver = parts[i + 1]
        
        if test_ver is None or code_ver is None:
            print(f"  Skipping {report_file.name}: couldn't parse versions")
            continue
        
        # Find corresponding coverage file
        prefix = "regression" if is_regression else "reproduction"
        coverage_file = instance_dir / f"{prefix}__test_coverage_test_{test_ver}_code_{code_ver}.txt"
        
        if not coverage_file.exists():
            print(f"  Skipping {report_file.name}: coverage file not found")
            continue
        
        # Read the report to get the patch
        with open(report_file, 'r') as f:
            report = json.load(f)
        
        # Get patch from report
        instance_id = list(report.keys())[0]
        patch_text = report[instance_id].get('code_patch', '')
        
        if not patch_text:
            print(f"  Skipping {report_file.name}: no code patch")
            continue
        
        # Recompute coverage
        recompute_report(report_file, coverage_file, patch_text, is_before)


def find_instance_dirs(log_dir, instance_name=None):
    """
    Recursively find instance directories that contain report files.
    """
    instance_dirs = []
    
    def search_dir(directory, depth=0):
        if depth > 3:  # Prevent infinite recursion
            return
        
        # Check if this directory contains report files
        has_reports = any(directory.glob("report.json"))
        
        if has_reports:
            # Check if we're filtering by instance name
            if instance_name is None or directory.name == instance_name:
                instance_dirs.append(directory)
        else:
            # Recursively search subdirectories
            for subdir in directory.iterdir():
                if subdir.is_dir():
                    search_dir(subdir, depth + 1)
    
    search_dir(log_dir)
    return instance_dirs


def main():
    parser = ArgumentParser(description="Recompute coverage metrics from existing test results")
    parser.add_argument(
        "--log-dir",
        type=str,
        required=True,
        help="Path to log directory (e.g., Test_execution/logs/run_evaluation/eotter/run0)"
    )
    parser.add_argument(
        "--instance",
        type=str,
        help="Process only this specific instance (e.g., django__django-11138)"
    )
    
    args = parser.parse_args()
    
    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(f"Error: Log directory not found: {log_dir}")
        sys.exit(1)
    
    # Find all instance directories (recursively)
    instance_dirs = find_instance_dirs(log_dir, args.instance)
    
    if not instance_dirs:
        print(f"No instance directories found in {log_dir}")
        if args.instance:
            print(f"  (looking for instance: {args.instance})")
        sys.exit(1)
    
    print(f"Found {len(instance_dirs)} instance(s) to process")
    
    for instance_dir in instance_dirs:
        try:
            process_instance_dir(instance_dir)
        except Exception as e:
            print(f"Error processing {instance_dir.name}: {e}")
            import traceback
            traceback.print_exc()
    
    print("\nDone!")


if __name__ == "__main__":
    main()

# Made with Bob
