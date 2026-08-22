#!/bin/bash
# Usage: ./run.sh --mode <vanilla|eotter|potter> --subset <verified|rebench|/path/to/dataset.json> --instances <path_to_instance_ids.json> [--tests <path_to_tests.json>]
#
# --instances  JSON file containing a list of instance_id strings
# --mode       One of: vanilla, eotter, potter
# --subset     Dataset subset: verified, rebench, or a path to a local .json dataset file
# --tests      Path to tests JSON file ({instance_id, model_patch} list); required for eotter/potter
#
# Splits instance_ids across 4 parallel tmux windows.

set -euo pipefail

# ---------- argument parsing ----------
mode=""
instances_path=""
tests_path=""
subset=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)      mode="$2";           shift 2 ;;
        --instances) instances_path="$2"; shift 2 ;;
        --tests)     tests_path="$2";     shift 2 ;;
        --subset)    subset="$2";         shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ---------- validation ----------
if [[ -z "$mode" || -z "$subset" || -z "$instances_path" ]]; then
    echo "Usage: $0 --mode <vanilla|eotter|potter> --subset <verified|rebench|/path/to/dataset.json> --instances <path> [--tests <path>]"
    exit 1
fi

if [[ "$mode" != "vanilla" && "$mode" != "eotter" && "$mode" != "potter" ]]; then
    echo "Error: --mode must be one of: vanilla, eotter, potter"
    exit 1
fi

if [[ "$subset" != "verified" && "$subset" != "rebench" && ! -f "$subset" ]]; then
    echo "Error: --subset must be 'verified', 'rebench', or a path to an existing .json file"
    exit 1
fi

if [[ ("$mode" == "eotter" || "$mode" == "potter") && -z "$tests_path" ]]; then
    echo "Error: --tests is required when --mode is '$mode'"
    exit 1
fi

if [[ ! -f "$instances_path" ]]; then
    echo "Error: instances file not found: $instances_path"
    exit 1
fi

if [[ -n "$tests_path" && ! -f "$tests_path" ]]; then
    echo "Error: tests file not found: $tests_path"
    exit 1
fi

# Resolve absolute paths so tmux windows can find them regardless of cwd
instances_abs="$(realpath "$instances_path")"
tests_abs=""
if [[ -n "$tests_path" ]]; then
    tests_abs="$(realpath "$tests_path")"
fi
# Resolve subset path too if it's a local file
if [[ -f "$subset" ]]; then
    subset="$(realpath "$subset")"
fi

# Script directory so output paths are relative to the mini-swe-agent dir
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- build instance list, skipping already-completed trajectories ----------
mapfile -t all_ids < <(jq -r '.[]' "$instances_abs")

instance_ids=()
for iid in "${all_ids[@]}"; do
    if [[ -f "${script_dir}/output/${iid}.traj.json" ]]; then
        echo "Skipping (exists): ${iid}"
    else
        instance_ids+=("$iid")
    fi
done

total=${#instance_ids[@]}
if [[ "$total" -eq 0 ]]; then
    echo "All instances already have trajectory files. Nothing to do."
    exit 0
fi

echo "Found $total instance(s) to run (skipped $((${#all_ids[@]} - total)) existing). Splitting into 4 subsets for parallel execution."

# ---------- launch tmux workers ----------
NUM_WORKERS=4
session="run_${mode}_$$"

tmux new-session -d -s "$session" -x 220 -y 50

for (( worker=0; worker<NUM_WORKERS; worker++ )); do
    subset_ids=()
    for (( i=worker; i<total; i+=NUM_WORKERS )); do
        subset_ids+=("${instance_ids[$i]}")
    done

    if [[ ${#subset_ids[@]} -eq 0 ]]; then
        continue
    fi

    # Build the --tests flag string (empty for vanilla)
    tests_flag=""
    if [[ -n "$tests_abs" ]]; then
        tests_flag="--tests $(printf '%q' "$tests_abs")"
    fi

    # Write a temp script for this worker — avoids all quoting issues in tmux send-keys
    tmpscript="$(mktemp /tmp/run_worker_XXXXXX.sh)"
    {
        printf '#!/bin/bash\n'
        printf 'source "$(conda info --base)/etc/profile.d/conda.sh"\n'
        printf 'conda activate otter\n'
        printf 'cd %q\n' "${script_dir}"
        for iid in "${subset_ids[@]}"; do
            printf 'echo %q\n' "--- Running: ${iid} ---"
            printf 'mini-extra swebench-single --subset %q --split test -i %q -o %q --%s %s --exit-immediately\n' \
                "${subset}" \
                "${iid}" \
                "${script_dir}/output/${iid}.traj.json" \
                "${mode}" \
                "${tests_flag}"
        done
        printf 'echo %q\n' "Worker $((worker+1)) done."
    } > "$tmpscript"
    chmod +x "$tmpscript"

    if [[ "$worker" -eq 0 ]]; then
        tmux send-keys -t "${session}:0" "bash $tmpscript" Enter
    else
        tmux new-window -t "${session}" -n "worker$((worker+1))"
        tmux send-keys -t "${session}:$worker" "bash $tmpscript" Enter
    fi
done

echo "Tmux session '${session}' started with up to ${NUM_WORKERS} parallel workers."
echo "Attach with:  tmux attach -t ${session}"
echo "Kill with:    tmux kill-session -t ${session}"
