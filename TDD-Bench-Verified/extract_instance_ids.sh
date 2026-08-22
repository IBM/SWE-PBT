#!/bin/bash

# Script to extract instance_id values from a JSON file
# Usage: ./extract_instance_ids.sh <json_file>

# Check if filename argument is provided
if [ $# -eq 0 ]; then
    echo "Usage: $0 <json_file>"
    echo "Example: $0 Some.json"
    exit 1
fi

JSON_FILE="$1"

# Check if file exists
if [ ! -f "$JSON_FILE" ]; then
    echo "Error: File '$JSON_FILE' not found!"
    exit 1
fi

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed. Please install it first."
    echo "  macOS: brew install jq"
    echo "  Linux: sudo apt-get install jq or sudo yum install jq"
    exit 1
fi

# Extract instance_id values from the JSON file into a bash array
echo "Extracting instance_id values from: $JSON_FILE"

# Read JSON array into bash array (compatible with older bash versions)
instance_ids=()
while IFS= read -r line; do
    instance_ids+=("$line")
done < <(jq -r '.[]' "$JSON_FILE")

echo "----------------------------------------"
echo "Total instance_ids: ${#instance_ids[@]}"
echo "----------------------------------------"

# Print all instance_ids
for id in "${instance_ids[@]}"; do
    echo "$id"
done

echo "----------------------------------------"
echo "Array variable 'instance_ids' contains all instance_id values"
echo "Access with: \${instance_ids[@]} or \${instance_ids[index]}"

# Made with Bob
