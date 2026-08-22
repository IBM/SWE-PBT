#!/bin/bash

# Run e-otter.py in parallel across N tmux windows, then merge results.
# Usage: ./epotter_batch.sh [--is_rebench] [--workers N] [--api-keys key1,key2,...] <path_to_initial_test_json>

IS_REBENCH=false
NUM_WORKERS=4
API_KEYS=""
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --is_rebench)
            IS_REBENCH=true
            shift
            ;;
        --workers)
            NUM_WORKERS="$2"
            shift 2
            ;;
        --api-keys)
            API_KEYS="$2"
            shift 2
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done
set -- "${POSITIONAL_ARGS[@]}"

if [ $# -lt 1 ]; then
    echo "Usage: $0 [--is_rebench] [--workers N] [--api-keys key1,key2,...] <path_to_initial_test_json>"
    echo "Example: $0 Some.json"
    echo "         $0 --is_rebench Some.json"
    echo "         $0 --workers 8 Some.json"
    echo "         $0 --api-keys keyA,keyB,keyC,keyD Some.json"
    exit 1
fi

initial_test="$1"
is_rebench=$IS_REBENCH

if [ ! -f "$initial_test" ]; then
    echo "Error: File not found: $initial_test"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed."
    exit 1
fi

# Collect all instance IDs
mapfile -t instance_ids < <(jq -r '.[].instance_id' "$initial_test" | grep -v '^$')

total="${#instance_ids[@]}"
if [ "$total" -eq 0 ]; then
    echo "Error: No instance_id entries found in $initial_test"
    exit 1
fi

echo "Total instance_ids: $total"

num_workers=$NUM_WORKERS
session="epotter_batch"
base_dir="$(pwd)"
abs_initial_test="$(realpath "$initial_test")"

# Temporary per-worker output files
worker_outputs=()
for (( w=0; w<num_workers; w++ )); do
    worker_outputs+=("/tmp/epotter_worker_out_$((w+1)).json")
done

# Create a detached tmux session with 4 named windows
tmux new-session -d -s "$session" -n "epotter1"
for (( w=1; w<num_workers; w++ )); do
    tmux new-window -t "$session" -n "epotter$((w+1))"
done

# Build and dispatch each worker inside its tmux window.
# Each worker signals completion via `tmux wait-for -S`, so the main shell
# can block with `tmux wait-for` without running anything twice.
# Split API_KEYS string into an array (if provided)
IFS=',' read -r -a api_keys_array <<< "$API_KEYS"
num_keys="${#api_keys_array[@]}"

for (( w=0; w<num_workers; w++ )); do
    worker_script=$(mktemp /tmp/epotter_worker_XXXXXX.sh)
    chmod +x "$worker_script"
    out_file="${worker_outputs[$w]}"
    signal="epotter_worker_done_$((w+1))"

    # Pick the key for this worker (round-robin if fewer keys than workers)
    if [ "$num_keys" -gt 0 ]; then
        assigned_key="${api_keys_array[$((w % num_keys))]}"
    else
        assigned_key="${CLAUDE_API:-}"
    fi

    cat > "$worker_script" <<HEADER
#!/bin/bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate otter
cd "$base_dir"
export CLAUDE_API="$assigned_key"
HEADER

    for (( i=w; i<total; i+=num_workers )); do
        instance_id="${instance_ids[$i]}"
        cat >> "$worker_script" <<BLOCK
echo "=========================================="
echo "Processing: $instance_id"
echo "=========================================="
if [ "$is_rebench" = true ]; then
    python3 "$base_dir/epotter.py" --dataset_name TDD_Rebench.json --initial_test "$abs_initial_test" --model claude --instance_id "$instance_id" --pbt_mode combined --cov_feedback --filter_by file --output_file "$out_file" --is_rebench
else
    python3 "$base_dir/epotter.py" --dataset_name TDD_Bench.json --initial_test "$abs_initial_test" --model claude --instance_id "$instance_id" --pbt_mode combined --cov_feedback --filter_by file --output_file "$out_file"
fi
if [ \$? -eq 0 ]; then
    echo "[OK] Successfully completed: $instance_id"
else
    echo "[FAIL] Failed: $instance_id"
fi
echo ""
BLOCK
    done

    cat >> "$worker_script" <<FOOTER
echo "=========================================="
echo "Worker $((w+1)) complete!"
echo "=========================================="
tmux wait-for -S "$signal"
FOOTER

    window="epotter$((w+1))"
    echo "Dispatching to tmux window: $window"
    # Execute only inside tmux — no background bash copy.
    tmux send-keys -t "${session}:${window}" "bash $worker_script" Enter
done

echo "=========================================="
echo "All workers dispatched. Attach with: tmux attach -t $session"
echo "Waiting for all workers to finish..."
echo "=========================================="

# Block until every worker signals completion
for (( w=0; w<num_workers; w++ )); do
    tmux wait-for "epotter_worker_done_$((w+1))"
done

# Merge all worker output files into e_otter_test.json using Python (safe JSON concat, no jq array-add quirks)
NUM_WORKERS=$num_workers python3 - <<'PYMERGE'
import json, sys, os

num_workers = int(os.environ["NUM_WORKERS"])
worker_files = [f"/tmp/epotter_worker_out_{i}.json" for i in range(1, num_workers + 1)]
merged = []
for path in worker_files:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            merged.extend(data)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: could not read {path}: {e}", file=sys.stderr)

with open("e_otter_test.json", "w", encoding="utf-8") as f:
    json.dump(merged, f, indent=4)

print(f"Merged {len(merged)} entries into e_otter_test.json")
PYMERGE

rm -f "${worker_outputs[@]}" /tmp/epotter_worker_log_*.txt
