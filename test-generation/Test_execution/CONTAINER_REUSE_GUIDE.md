
#### Usage

```bash
# First run - creates and keeps the container
python -m tddbench.harness.run_evaluation \
  --test_patch test_patch.json \
  --code_patch code_patch.json \
  --max_workers 1 \
  --instance_ids sympy__sympy-13372 \
  --run_id bob \
  --keep_container true

# Subsequent runs - reuses the existing container (much faster!)
python -m tddbench.harness.run_evaluation \
  --test_patch test_patch.json \
  --code_patch code_patch.json \
  --max_workers 1 \
  --instance_ids sympy__sympy-13372 \
  --run_id bob \
  --keep_container true \
  --output_subdir run2

# Subsequent runs - reuses the existing container (much faster!)
python -m tddbench.harness.run_evaluation \
  --test_patch test_patch.json \
  --code_patch code_patch.json \
  --max_workers 1 \
  --instance_ids sympy__sympy-13372 \
  --run_id bob \
  --output_subdir run3

```

