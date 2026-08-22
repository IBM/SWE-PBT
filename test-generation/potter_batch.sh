#!/bin/bash

# Run potter.py in parallel across 4 tmux windows, then merge results.
# Usage: ./potter_batch.sh [--is_rebench] [--workers N] [--api-keys key1,key2,...] <json_file>

IS_REBENCH=0
NUM_WORKERS=4
API_KEYS=""
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --is_rebench)
            IS_REBENCH=1
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

if [ $# -eq 0 ]; then
    echo "Usage: $0 [--is_rebench] [--workers N] [--api-keys key1,key2,...] <json_file>"
    echo "Example: $0 Some.json"
    echo "         $0 --is_rebench Some.json"
    echo "         $0 --workers 8 Some.json"
    echo "         $0 --api-keys keyA,keyB,keyC,keyD Some.json"
    exit 1
fi

JSON_FILE="$1"

if [ "$IS_REBENCH" -eq 1 ]; then
    DATASET_NAME="TDD_Rebench.json"
else
    DATASET_NAME="TDD_Bench.json"
fi

if [ ! -f "$JSON_FILE" ]; then
    echo "Error: File '$JSON_FILE' not found!"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed."
    exit 1
fi

# Read all instance IDs from the JSON array
mapfile -t instance_ids < <(jq -r '.[]' "$JSON_FILE")

total="${#instance_ids[@]}"
if [ "$total" -eq 0 ]; then
    echo "Error: No instance IDs found in $JSON_FILE"
    exit 1
fi

echo "Total instance_ids: $total"

num_workers=$NUM_WORKERS
session="potter_batch"

# Temporary per-worker output files
worker_outputs=()
for (( w=0; w<num_workers; w++ )); do
    worker_outputs+=("/tmp/potter_worker_out_$((w+1)).json")
done

# Create tmux session with 4 named windows
tmux new-session -d -s "$session" -n "potter1"
for (( w=1; w<num_workers; w++ )); do
    tmux new-window -t "$session" -n "potter$((w+1))"
done

# Build and dispatch each worker inside its tmux window.
# Each worker signals completion via `tmux wait-for -S`, so the main shell
# can block with `tmux wait-for` without running anything twice.
# Split API_KEYS string into an array (if provided)
IFS=',' read -r -a api_keys_array <<< "$API_KEYS"
num_keys="${#api_keys_array[@]}"

for (( w=0; w<num_workers; w++ )); do
    worker_script=$(mktemp /tmp/potter_worker_XXXXXX.sh)
    chmod +x "$worker_script"
    out_file="${worker_outputs[$w]}"
    signal="potter_worker_done_$((w+1))"

    # Pick the key for this worker (round-robin if fewer keys than workers)
    if [ "$num_keys" -gt 0 ]; then
        assigned_key="${api_keys_array[$((w % num_keys))]}"
    else
        assigned_key="${CLAUDE_API:-}"
    fi

    cat > "$worker_script" <<HEADER
#!/bin/bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate potter
cd "$(pwd)"
export CLAUDE_API="$assigned_key"
HEADER

    for (( i=w; i<total; i+=num_workers )); do
        instance_id="${instance_ids[$i]}"
        cat >> "$worker_script" <<BLOCK
echo "=========================================="
echo "Processing: $instance_id"
echo "=========================================="
python3 potter.py --dataset_name $DATASET_NAME --instance_id "$instance_id" --pbt_mode combined --output_file "$out_file"
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

    window="potter$((w+1))"
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
    tmux wait-for "potter_worker_done_$((w+1))"
done

# Merge all worker output files into potter_test.json using Python (safe JSON concat, no jq array-add quirks)
NUM_WORKERS=$num_workers python3 - <<'PYMERGE'
import json, sys, os

num_workers = int(os.environ["NUM_WORKERS"])
worker_files = [f"/tmp/potter_worker_out_{i}.json" for i in range(1, num_workers + 1)]
merged = []
for path in worker_files:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            merged.extend(data)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: could not read {path}: {e}", file=sys.stderr)

with open("potter_test.json", "w", encoding="utf-8") as f:
    json.dump(merged, f, indent=4)

print(f"Merged {len(merged)} entries into potter_test.json")
PYMERGE

rm -f "${worker_outputs[@]}" /tmp/potter_worker_log_*.txt
