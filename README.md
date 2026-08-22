# SWE-PBT

SWE-PBT is a pipeline for generating property-based tests (PBTs) to help resolve real-world software engineering issues. Given a GitHub issue from the [SWE-bench](https://swebench.com) benchmark, the pipeline generates tests that capture the intended behaviour described in the issue, uses those tests to guide an automated software engineering agent toward a fix, and then evaluates the resulting patch against the SWE-bench harness.

## Project Tree

```
SWE-PBT/
├── test-generation/          # Potter & ePotter — PBT generation and execution feedback
├── TDD-Bench-Verified/       # Evaluation harness for measuring test fail-to-pass rates
├── Rebench-execution-harness/ # Execution harness for running tests on the ReBench subset
├── mini-swe-agent/           # Lightweight SWE agent used to apply patches guided by PBTs
└── SWE-bench/                # SWE-bench evaluation framework for scoring final patches
```

### Directory responsibilities

| Directory | Responsibility |
|---|---|
| `test-generation` | Contains **potter** (LLM-based PBT generator) and **epotter** (execution-feedback refinement loop). Batch scripts drive parallel generation across many instances; analysis scripts summarise pass/fail results. |
| `TDD-Bench-Verified` | Evaluation harness that measures how many generated tests transition from failing (pre-patch) to passing (post-patch), providing the primary quality signal for generated tests. |
| `Rebench-execution-harness` | Execution harness specialised for the ReBench dataset subset, used when running potter/epotter in `--is_rebench` mode. |
| `mini-swe-agent` | A minimal AI software engineering agent that takes an issue and (optionally) a set of PBTs, then iteratively produces a patch. Supports `potter` mode to exploit the generated tests as a guidance signal. |
| `SWE-bench` | The official SWE-bench evaluation framework. Runs the generated patches inside Docker containers and reports resolve rates against the benchmark dataset. |

---

## Requirements

### Conda environment

Create and activate a conda environment, then install the required Python packages:

```bash
conda create --name potter python=3.12
conda activate potter
conda install pip -y
python -m pip install datasets
python -m pip install litellm
python -m pip install gitpython
python -m pip install tree-sitter tree-sitter-python
```

### LLM API key

The pipeline is configured for **Claude Sonnet** by default. Export your API key before running:

```bash
export CLAUDE_API="your_api_key_here"
```

To use a different model or LLM provider, see [`test-generation/utility.py`](test-generation/utility.py).

### Per-directory setup

Within the same `potter` conda environment, install each sub-project's requirements:

```bash
# Prepare the dataset for test-generation
cd test-generation
python dataset_preparation.py

# Install the execution harness (epotter dependency)
cp TDD_Bench.json Test_execution/
cd Test_execution
pip install -e .
cd ../..

# Install TDD-Bench-Verified
cd TDD-Bench-Verified
pip install -e .
cd ..

# Install mini-swe-agent
cd mini-swe-agent
pip install -e .
cd ..

# Install SWE-bench
cd SWE-bench
pip install -e .
cd ..
```

> **Note:** SWE-bench evaluation requires Docker. Follow the [Docker setup guide](https://docs.docker.com/engine/install/) and, on Linux, the [post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/). We recommend an `x86_64` machine with at least 120 GB of free storage, 16 GB of RAM, and 8 CPU cores.

---

## Running the Pipeline

### Step 1 — Generate PBTs with potter

Run potter in batch mode across a set of instance IDs. The instance IDs are supplied as a JSON array in a file (e.g., `id_list.json`).

```bash
cd test-generation
bash potter_batch.sh [--is_rebench] [--workers N] [--api-keys key1,key2,...] <json_file>
```

**Example:**
```bash
bash potter_batch.sh --workers 4 id_list.json
```

Output is merged into `test-generation/potter_test.json`.

---

### Step 2 — Refine tests with epotter

Run epotter to iteratively improve the generated tests using execution feedback. Requires the `potter_test.json` produced in Step 1.

```bash
cd test-generation
bash epotter_batch.sh [--is_rebench] [--workers N] [--api-keys key1,key2,...] <path_to_initial_test_json>
```

**Example:**
```bash
bash epotter_batch.sh --workers 4 potter_test.json
```

Output is merged into `test-generation/e_otter_test.json`.

---

### Step 3 — Analyze test results

Analyze the generated test outputs and produce a CSV report with pass/fail/coverage statistics.

```bash
cd test-generation
python potter_results_analyze.py
```

---

### Step 4 — Run the SWE agent with potter mode

Use mini-swe-agent in `potter` mode to attempt patches on each instance, guided by the generated PBTs.

```bash
cd mini-swe-agent
bash run.sh \
    --mode potter \
    --subset verified \
    --instances <path_to_instance_ids.json> \
    --tests <path_to_tests.json>
```

**Example:**
```bash
bash run.sh \
    --mode potter \
    --subset verified \
    --instances ../test-generation/id_list.json \
    --tests ../test-generation/e_otter_test.json
```

The script distributes work across 4 parallel tmux windows. Attach with `tmux attach -t run_potter_<pid>`.

---

### Step 5 — Evaluate with SWE-bench

Evaluate the patches produced by mini-swe-agent against the SWE-bench benchmark using the official Docker-based harness.

```bash
cd SWE-bench
python -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --predictions_path <path_to_predictions> \
    --max_workers <num_workers> \
    --run_id <run_id>
```

**Example:**
```bash
python -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --predictions_path ../mini-swe-agent/output \
    --max_workers 4 \
    --run_id potter_eval_run
```

Evaluation logs are written to `logs/` and final results to `evaluation_results/`.
