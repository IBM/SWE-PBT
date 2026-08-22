#!/usr/bin/env python3
"""
Extract PBT diff patches from a logs directory.

Usage:
    python potter_extract_pbt_patches.py <logs_dir>

The logs directory is expected to contain subdirectories named run* (e.g. run0,
run1, ...).  Each run directory has the layout:

    <logs_dir>/run<N>/gold/<instance_id>/test_patch_test_1_discovery.diff

For every instance_id the patch from the highest-numbered run directory is
selected.  The result is written to <logs_dir_basename>.json in the current
working directory.
"""

import json
import os
import re
import sys


DIFF_FILENAME = "test_patch_test_1_discovery.diff"


def collect_patches(logs_dir: str) -> list[dict]:
    """Return a list of {instance_id, model_patch} dicts, one per instance."""

    # Discover all run* subdirectories and sort by run number descending.
    run_dirs = []
    for entry in os.scandir(logs_dir):
        if entry.is_dir():
            m = re.fullmatch(r"run(\d+)", entry.name)
            if m:
                run_dirs.append((int(m.group(1)), entry.path))

    run_dirs.sort(key=lambda t: t[0], reverse=True)  # highest run first

    seen: set[str] = set()
    patches: list[dict] = []

    for _run_num, run_path in run_dirs:
        gold_path = os.path.join(run_path, "gold")
        if not os.path.isdir(gold_path):
            continue

        for instance_entry in sorted(os.scandir(gold_path), key=lambda e: e.name):
            if not instance_entry.is_dir():
                continue
            instance_id = instance_entry.name
            if instance_id in seen:
                continue

            diff_file = os.path.join(instance_entry.path, DIFF_FILENAME)
            if not os.path.isfile(diff_file):
                continue

            with open(diff_file, "r", encoding="utf-8") as fh:
                patch_content = fh.read()

            patches.append({"instance_id": instance_id, "model_patch": patch_content})
            seen.add(instance_id)

    return patches


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <logs_dir>", file=sys.stderr)
        sys.exit(1)

    logs_dir = sys.argv[1].rstrip(os.sep)
    if not os.path.isdir(logs_dir):
        print(f"Error: '{logs_dir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    patches = collect_patches(logs_dir)

    basename = os.path.basename(logs_dir)
    output_file = f"{basename}.json"

    with open(output_file, "w", encoding="utf-8") as fh:
        json.dump(patches, fh, indent=2)

    print(f"Wrote {len(patches)} entries to {output_file}")


if __name__ == "__main__":
    main()
