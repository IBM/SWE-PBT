#!/usr/bin/env python3
"""
Extract PBT test patches from a potter run_evaluation directory.

Usage:
    python potter_extract_pbts.py <path/to/run_evaluation/potter_xxx>

For each instance_id found under any runN/gold/, the script picks the patch
from the highest-numbered run that contains that instance (i.e. iterates
run9 → run0 and takes the first occurrence).

Output: <basename>.json next to the script's working directory.
"""

import json
import os
import sys


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <directory>", file=sys.stderr)
        sys.exit(1)

    base_dir = os.path.abspath(sys.argv[1])
    if not os.path.isdir(base_dir):
        print(f"Error: '{base_dir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    # Collect all run directories that match the pattern runN (N is an integer)
    run_dirs = []
    for entry in os.listdir(base_dir):
        if entry.startswith("run"):
            suffix = entry[len("run"):]
            if suffix.isdigit():
                run_dirs.append((int(suffix), entry))

    # Sort descending: run9, run8, ..., run0
    run_dirs.sort(key=lambda x: x[0], reverse=True)

    if not run_dirs:
        print("No runN directories found.", file=sys.stderr)
        sys.exit(1)

    # For each instance_id, record the first (highest N) occurrence
    seen: dict[str, int] = {}   # instance_id -> run number
    results: list[dict] = []

    for run_num, run_name in run_dirs:
        gold_dir = os.path.join(base_dir, run_name, "gold")
        if not os.path.isdir(gold_dir):
            continue

        for instance_id in sorted(os.listdir(gold_dir)):
            if instance_id in seen:
                continue  # already captured from a higher run

            diff_path = os.path.join(gold_dir, instance_id, "test_patch_test_1_discovery.diff")
            if not os.path.isfile(diff_path):
                continue

            with open(diff_path, "r", encoding="utf-8") as fh:
                patch = fh.read()

            seen[instance_id] = run_num
            results.append({
                "instance_id": instance_id,
                "model_patch": patch,
                "attempt": run_num,
            })

    # Sort output by instance_id for determinism
    results.sort(key=lambda r: r["instance_id"])

    output_name = os.path.basename(base_dir.rstrip("/")) + ".json"
    output_path = os.path.join(os.getcwd(), output_name)

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    print(f"Wrote {len(results)} entries to {output_path}")


if __name__ == "__main__":
    main()
