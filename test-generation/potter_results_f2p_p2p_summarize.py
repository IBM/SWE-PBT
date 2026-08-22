#!/usr/bin/env python3
"""
Summarize potter test results into p2p / f2p columns.

Usage:
    python potter_results_f2p_p2p_summarize.py <instance_ids.txt> <summary.csv>

Arguments:
    instance_ids.txt  – plain-text file with one instance_id per line
    summary.csv       – path to a test_results_summary.csv produced by potter

Output:
    A new CSV written next to summary.csv named
    <summary_stem>_p2p_f2p.csv  with columns:
        instance_id, p2p, f2p

    p2p  combines RegPre + RegPost  (e.g. "PF", "PP", "FF", …)
    f2p  combines RepPre + RepPost  (e.g. "PF", "PP", "FF", …)

    Status letters:  P=pass  F=fail  E=error  N=notest
"""

import csv
import sys
from pathlib import Path

STATUS_MAP = {
    "pass":   "P",
    "fail":   "F",
    "error":  "E",
    "notest": "N",
}


def encode(value: str) -> str:
    """Return the single-letter code for a status string."""
    return STATUS_MAP.get(value.strip().lower(), value.strip())


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    ids_path = Path(sys.argv[1])
    summary_path = Path(sys.argv[2])

    # Read the ordered list of instance ids
    instance_ids = [line.strip() for line in ids_path.read_text().splitlines() if line.strip()]

    # Index the summary CSV by instance_id (keep last occurrence per id)
    summary: dict[str, dict] = {}
    with summary_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            summary[row["instance_id"]] = row

    # Build output rows in the requested order
    output_path = summary_path.parent / (summary_path.stem + "_p2p_f2p.csv")
    with output_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["instance_id", "p2p", "f2p"])
        for iid in instance_ids:
            if iid not in summary:
                # Instance not found in the summary – treat as notest/notest
                writer.writerow([iid, "NN", "NN"])
                continue
            row = summary[iid]
            p2p = encode(row["RegPre"]) + encode(row["RegPost"])
            f2p = encode(row["RepPre"]) + encode(row["RepPost"])
            writer.writerow([iid, p2p, f2p])

    print(f"Written: {output_path}")


if __name__ == "__main__":
    main()
