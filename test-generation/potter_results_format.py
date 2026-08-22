#!/usr/bin/env python3
"""
Format a potter test_results_summary.csv into a cleaner output CSV.

Usage:
    python potter_results_format.py \\
        --summary   test_results_summary.csv \\
        --final_tests  peotter_*_tests.json \\
        --instances    TDD-Bench-Verified/TDD_Bench_Chunks/4.json

Output is written as test_results_summary_formatted.csv next to the input CSV.
Missing instances (present in --instances but absent from the CSV) are appended
with default values: p2p=--, f2p=--, p2p_f2p=FALSE, critic=--, cov=0, reg_cov=0,
rep_cov=0.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Format a potter test_results_summary.csv into a cleaner output CSV "
            "with p2p/f2p initials, critic decisions, and coverage columns. "
            "Instances listed in --instances that are absent from the summary are "
            "appended with default placeholder values."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--summary",
        required=True,
        metavar="CSV",
        help="Path to the potter test_results_summary.csv file.",
    )
    parser.add_argument(
        "--final_tests",
        required=True,
        metavar="JSON",
        help=(
            "Path to the peotter_*_tests.json file containing per-instance "
            "'decision' values used to populate the 'critic' column."
        ),
    )
    parser.add_argument(
        "--instances",
        required=True,
        metavar="JSON",
        help=(
            "Path to a JSON file containing a flat list of expected instance IDs "
            "(e.g. TDD-Bench-Verified/TDD_Bench_Chunks/4.json). "
            "Any instance listed here but absent from --summary is appended to "
            "the output with default values: "
            "p2p=--, f2p=--, p2p_f2p=FALSE, critic=--, cov=0, reg_cov=0, rep_cov=0."
        ),
    )
    return parser.parse_args()


def result_initials(pre: str, post: str) -> str:
    """Convert a (pre, post) pair of 'pass'/'fail' strings to initials like PP, PF, FP, FF."""
    def initial(v: str) -> str:
        return "P" if v.strip().lower() == "pass" else "F"
    return initial(pre) + initial(post)


def trailing_issue_number(instance_id: str) -> int:
    """Extract the trailing issue number from an instance_id for sort ordering."""
    match = re.search(r"-(\d+)$", instance_id)
    return int(match.group(1)) if match else 0


def main():
    args = parse_args()

    csv_path = Path(args.summary)
    json_path = Path(args.final_tests)
    instances_path = Path(args.instances)

    for path, label in [(csv_path, "--summary"), (json_path, "--final_tests"), (instances_path, "--instances")]:
        if not path.exists():
            print(f"Error: file not found for {label}: {path}", file=sys.stderr)
            sys.exit(1)

    # Load critic decisions from JSON: instance_id -> decision
    with open(json_path) as f:
        json_data = json.load(f)
    critic_map = {entry["instance_id"]: entry.get("decision", "") for entry in json_data}

    # Load expected instance list
    with open(instances_path) as f:
        expected_instances: list[str] = json.load(f)

    # Read CSV rows
    rows = []
    seen_instances: set[str] = set()
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            instance_id = row["instance_id"]
            p2p = result_initials(row["RegPre"], row["RegPost"])
            f2p = result_initials(row["RepPre"], row["RepPost"])
            p2p_f2p = "TRUE" if p2p == "PP" and f2p == "FP" else "FALSE"
            critic = critic_map.get(instance_id, "")
            cov = row["UnionCov"]
            reg_cov = row["RegCov"]
            rep_cov = row["RepCov"]
            rows.append({
                "instance_id": instance_id,
                "p2p": p2p,
                "f2p": f2p,
                "p2p_f2p": p2p_f2p,
                "critic": critic,
                "cov": cov,
                "reg_cov": reg_cov,
                "rep_cov": rep_cov,
            })
            seen_instances.add(instance_id)

    # Append missing instances with default values
    missing = [iid for iid in expected_instances if iid not in seen_instances]
    if missing:
        print(f"Warning: {len(missing)} instance(s) from --instances not found in --summary; appending with defaults:")
        for iid in missing:
            print(f"  {iid}")
            rows.append({
                "instance_id": iid,
                "p2p": "--",
                "f2p": "--",
                "p2p_f2p": "FALSE",
                "critic": "--",
                "cov": 0,
                "reg_cov": 0,
                "rep_cov": 0,
            })

    # Sort: alphabetically by instance_id prefix, then by trailing issue number
    rows.sort(key=lambda r: (re.sub(r"-\d+$", "", r["instance_id"]), trailing_issue_number(r["instance_id"])))

    # Write output CSV next to the input CSV
    out_path = csv_path.parent / "test_results_summary_formatted.csv"
    fieldnames = ["instance_id", "p2p", "f2p", "p2p_f2p", "critic", "cov", "reg_cov", "rep_cov"]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
