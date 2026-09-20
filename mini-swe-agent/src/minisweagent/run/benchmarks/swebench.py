#!/usr/bin/env python3

"""Run mini-SWE-agent on SWE-bench instances in batch mode."""
# Read this first: https://mini-swe-agent.com/latest/usage/swebench/  (usage docs)

import concurrent.futures
import json
import random
import re
import subprocess
import threading
import time
import traceback
from pathlib import Path

import typer
from jinja2 import StrictUndefined, Template
from rich.live import Live

from minisweagent import Environment
from minisweagent.config import builtin_config_dir, get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model
from minisweagent.run.benchmarks.utils.batch_progress import RunBatchProgressManager
from minisweagent.run.benchmarks.utils.common import ProgressTrackingAgent
from minisweagent.utils.log import add_file_handler, logger
from minisweagent.utils.serialize import UNSET, recursive_merge

_HELP_TEXT = """Run mini-SWE-agent on SWEBench instances.

[not dim]
More information about the usage: [bold green]https://mini-swe-agent.com/latest/usage/swebench/[/bold green]
[/not dim]
"""

CONFIG_FILE_VANILLA = builtin_config_dir / "benchmarks" / "swebench_vanilla.yaml"
CONFIG_FILE_EOTTER  = builtin_config_dir / "benchmarks" / "swebench_eotter.yaml"
CONFIG_FILE_POTTER  = builtin_config_dir / "benchmarks" / "swebench_potter.yaml"
CONFIG_FILE_MERGED  = builtin_config_dir / "benchmarks" / "swebench_merged.yaml"
CONFIG_FILE_MERGED_STATIC = builtin_config_dir / "benchmarks" / "swebench_vanilla_tests.yaml"

DATASET_MAPPING = {
    "full": "princeton-nlp/SWE-Bench",
    "verified": "princeton-nlp/SWE-Bench_Verified",
    "lite": "princeton-nlp/SWE-Bench_Lite",
    "multimodal": "princeton-nlp/SWE-Bench_Multimodal",
    "multilingual": "swe-bench/SWE-Bench_Multilingual",
    "smith": "SWE-bench/SWE-smith",
    "_test": "klieret/swe-bench-dummy-test-dataset",
    "rebench": "nebius/SWE-rebench-leaderboard",
}


def load_instances(subset: str, split: str) -> list[dict]:
    """Load instances from a HuggingFace dataset or a local JSON file.

    If `subset` resolves to a path ending in .json that exists on disk, the file
    is loaded directly (the `split` argument is ignored in that case).
    Otherwise the dataset is loaded from HuggingFace via load_dataset().
    """
    import json as _json

    dataset_path = DATASET_MAPPING.get(subset, subset)
    local = Path(dataset_path)
    if local.suffix == ".json" and local.exists():
        logger.info(f"Loading dataset from local file {local} (split ignored)")
        return _json.loads(local.read_text())
    from datasets import load_dataset
    logger.info(f"Loading dataset from {dataset_path}, split {split}...")
    return list(load_dataset(dataset_path, split=split))

app = typer.Typer(rich_markup_mode="rich", add_completion=False)
_OUTPUT_FILE_LOCK = threading.Lock()


def get_swebench_docker_image_name(instance: dict) -> str:
    """Get the image name for a SWEBench instance."""
    image_name = instance.get("image_name", None) or instance.get("docker_image", None)
    if image_name is None:
        # Docker doesn't allow double underscore, so we replace them with a magic token
        iid = instance["instance_id"]
        id_docker_compatible = iid.replace("__", "_1776_")
        image_name = f"docker.io/swebench/sweb.eval.x86_64.{id_docker_compatible}:latest".lower()
    return image_name


def get_sb_environment(config: dict, instance: dict) -> Environment:
    env_config = {**config.get("environment", {})}
    env_config["environment_class"] = env_config.get("environment_class", "docker")
    image_name = get_swebench_docker_image_name(instance)
    if env_config["environment_class"] in ["docker", "swerex_modal"]:
        env_config["image"] = image_name
    elif env_config["environment_class"] in ["singularity", "contree"]:
        env_config["image"] = "docker://" + image_name

    env = get_environment(env_config)
    if startup_command := config.get("run", {}).get("env_startup_command"):
        startup_command = Template(startup_command, undefined=StrictUndefined).render(**instance)
        out = env.execute({"command": startup_command})
        if out["returncode"] != 0:
            raise RuntimeError(f"Error executing startup command: {out}")
    return env


def update_preds_file(output_path: Path, instance_id: str, model_name: str, result: str):
    """Update the output JSON file with results from a single instance."""
    with _OUTPUT_FILE_LOCK:
        output_data = {}
        if output_path.exists():
            output_data = json.loads(output_path.read_text())
        output_data[instance_id] = {
            "model_name_or_path": model_name,
            "instance_id": instance_id,
            "model_patch": result,
        }
        output_path.write_text(json.dumps(output_data, indent=2))


def remove_from_preds_file(output_path: Path, instance_id: str):
    """Remove an instance from the predictions file."""
    if not output_path.exists():
        return
    with _OUTPUT_FILE_LOCK:
        output_data = json.loads(output_path.read_text())
        if instance_id in output_data:
            del output_data[instance_id]
            output_path.write_text(json.dumps(output_data, indent=2))


# conftest.py shipped alongside this repo, copied into every container
_CONFTEST_SRC = Path(__file__).parents[5] / "test-generation" / "conftest.py"


def _setup_pbt_in_container(
    env,
    instance_id: str,
    pbt_patch: str,
    conftest_src: Path,
) -> None:
    """Install PBT dependencies, create /testbed/pbt, copy conftest.py, and apply the test patch."""
    docker_exe = env.config.executable
    container_id = env.container_id

    # 1. Install required packages
    out = env.execute({"command": "pip install pytest coverage hypothesis -q"})
    if out["returncode"] != 0:
        raise RuntimeError(f"[{instance_id}] Failed to install PBT deps: {out['output']}")

    # 2. Create /testbed/pbt directory
    out = env.execute({"command": "mkdir -p /testbed/pbt"})
    if out["returncode"] != 0:
        raise RuntimeError(f"[{instance_id}] Failed to create /testbed/pbt: {out['output']}")

    # 3. Copy conftest.py from the host into the container
    subprocess.run(
        [docker_exe, "cp", str(conftest_src), f"{container_id}:/testbed/pbt/conftest.py"],
        check=True,
    )

    # 4. Apply the PBT patch to produce /testbed/pbt/test.py
    apply_cmd = "git apply -v -"
    out = env.execute({"command": f"echo {json.dumps(pbt_patch)!r} | {apply_cmd}"})
    # git apply writes files relative to the repo root (/testbed), so the patch path
    # determines where the file lands. If the patch targets a different path we copy it.
    if out["returncode"] != 0:
        # Fallback: write the patch via heredoc and apply
        escaped = pbt_patch.replace("'", "'\\''")
        heredoc_cmd = f"git -C /testbed apply -v - <<'__PATCH__'\n{pbt_patch}\n__PATCH__"
        out = env.execute({"command": heredoc_cmd})
        if out["returncode"] != 0:
            raise RuntimeError(f"[{instance_id}] Failed to apply PBT patch: {out['output']}")

    logger.info(f"[{instance_id}] PBT setup complete")


def _apply_patch_in_container(env, instance_id: str, patch: str) -> None:
    """Apply a git patch inside the container (pipe then heredoc fallback)."""
    apply_cmd = "git apply -v -"
    out = env.execute({"command": f"echo {json.dumps(patch)!r} | {apply_cmd}"})
    if out["returncode"] != 0:
        heredoc_cmd = f"git -C /testbed apply -v - <<'__PATCH__'\n{patch}\n__PATCH__"
        out = env.execute({"command": heredoc_cmd})
        if out["returncode"] != 0:
            raise RuntimeError(f"[{instance_id}] Failed to apply patch: {out['output']}")


def _setup_eotter_in_container(env, instance_id: str, patch: str) -> None:
    """Apply the eotter test patch into the container."""
    out = env.execute({"command": "mkdir -p /testbed/eotter_test"})
    if out["returncode"] != 0:
        raise RuntimeError(f"[{instance_id}] Failed to create /testbed/eotter_test: {out['output']}")
    _apply_patch_in_container(env, instance_id, patch)
    logger.info(f"[{instance_id}] Eotter test setup complete")


def _setup_merged_in_container(
    env,
    instance_id: str,
    eotter_patch: str,
    pbt_patch: str,
    conftest_src: Path,
) -> None:
    """Apply both the eotter test patch and the PBT test patch into the container."""
    _setup_eotter_in_container(env, instance_id, eotter_patch)
    _setup_pbt_in_container(env, instance_id, pbt_patch, conftest_src)
    logger.info(f"[{instance_id}] Merged (eotter + PBT) setup complete")


def process_instance(
    instance: dict,
    output_dir: Path,
    config: dict,
    progress_manager: RunBatchProgressManager,
    mode: str = "vanilla",
    eotter_tests: dict[str, str] | None = None,
    potter_tests: dict[str, str] | None = None,
) -> None:
    """Process a single SWEBench instance."""
    instance_id = instance["instance_id"]
    instance_dir = output_dir / instance_id
    # avoid inconsistent state if something here fails and there's leftover previous files
    remove_from_preds_file(output_dir / "preds.json", instance_id)
    (instance_dir / f"{instance_id}.traj.json").unlink(missing_ok=True)
    model = get_model(config=config.get("model", {}))
    task = instance["problem_statement"]

    progress_manager.on_instance_start(instance_id)
    progress_manager.update_instance_status(instance_id, "Pulling/starting environment")

    agent = None
    exit_status = None
    result = None
    extra_info = {}

    try:
        env = get_sb_environment(config, instance)
        extra_run_kwargs: dict = {}
        if mode == "potter":
            if potter_tests and (patch := potter_tests.get(instance_id)):
                progress_manager.update_instance_status(instance_id, "Setting up PBT")
                _setup_pbt_in_container(env, instance_id, patch, _CONFTEST_SRC)
        elif mode == "eotter":
            if eotter_tests and (patch := eotter_tests.get(instance_id)):
                progress_manager.update_instance_status(instance_id, "Setting up eotter test")
                _setup_eotter_in_container(env, instance_id, patch)
                extra_run_kwargs["eotter_test"] = patch
        elif mode == "merged":
            eotter_patch = eotter_tests.get(instance_id) if eotter_tests else None
            pbt_patch = potter_tests.get(instance_id) if potter_tests else None
            if eotter_patch and pbt_patch:
                progress_manager.update_instance_status(instance_id, "Setting up merged tests")
                _setup_merged_in_container(env, instance_id, eotter_patch, pbt_patch, _CONFTEST_SRC)
                extra_run_kwargs["eotter_test"] = eotter_patch
        elif mode == "merged_static":
            # Static mode: tests are only exposed to the prompt, nothing is installed in the container.
            eotter_patch = eotter_tests.get(instance_id) if eotter_tests else None
            pbt_patch = potter_tests.get(instance_id) if potter_tests else None
            if eotter_patch and pbt_patch:
                extra_run_kwargs["eotter_test"] = eotter_patch
                extra_run_kwargs["potter_test"] = pbt_patch
            else:
                missing = "eotter" if not eotter_patch else "potter"
                logger.warning(f"[{instance_id}] No {missing} test found for merged_static mode")
        agent = ProgressTrackingAgent(
            model,
            env,
            progress_manager=progress_manager,
            instance_id=instance_id,
            **config.get("agent", {}),
        )
        info = agent.run(task, **extra_run_kwargs)
        exit_status = info.get("exit_status")
        result = info.get("submission")
    except Exception as e:
        logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
        exit_status, result = type(e).__name__, ""
        extra_info = {"traceback": traceback.format_exc(), "exception_str": str(e)}
    finally:
        if agent is not None:
            traj_path = instance_dir / f"{instance_id}.traj.json"
            agent.save(
                traj_path,
                {
                    "info": {
                        "exit_status": exit_status,
                        "submission": result,
                        **extra_info,
                    },
                    "instance_id": instance_id,
                },
            )
            logger.info(f"Saved trajectory to '{traj_path}'")
        update_preds_file(output_dir / "preds.json", instance_id, model.config.model_name, result)
        progress_manager.on_instance_end(instance_id, exit_status)


def filter_instances(
    instances: list[dict], *, filter_spec: str, slice_spec: str = "", shuffle: bool = False
) -> list[dict]:
    """Filter and slice a list of SWEBench instances."""
    if shuffle:
        instances = sorted(instances.copy(), key=lambda x: x["instance_id"])
        random.seed(42)
        random.shuffle(instances)
    before_filter = len(instances)
    instances = [instance for instance in instances if re.match(filter_spec, instance["instance_id"])]
    if (after_filter := len(instances)) != before_filter:
        logger.info(f"Instance filter: {before_filter} -> {after_filter} instances")
    if slice_spec:
        values = [int(x) if x else None for x in slice_spec.split(":")]
        instances = instances[slice(*values)]
        if (after_slice := len(instances)) != before_filter:
            logger.info(f"Instance slice: {before_filter} -> {after_slice} instances")
    return instances


# fmt: off
@app.command(help=_HELP_TEXT)
def main(
    subset: str = typer.Option("lite", "--subset", help="SWEBench subset to use or path to a dataset", rich_help_panel="Data selection"),
    split: str = typer.Option("dev", "--split", help="Dataset split", rich_help_panel="Data selection"),
    slice_spec: str = typer.Option("", "--slice", help="Slice specification (e.g., '0:5' for first 5 instances)", rich_help_panel="Data selection"),
    filter_spec: str = typer.Option("", "--filter", help="Filter instance IDs by regex", rich_help_panel="Data selection"),
    shuffle: bool = typer.Option(False, "--shuffle", help="Shuffle instances", rich_help_panel="Data selection"),
    output: str = typer.Option("", "-o", "--output", help="Output directory", rich_help_panel="Basic"),
    workers: int = typer.Option(1, "-w", "--workers", help="Number of worker threads for parallel processing", rich_help_panel="Basic"),
    model: str | None = typer.Option(None, "-m", "--model", help="Model to use", rich_help_panel="Basic"),
    model_class: str | None = typer.Option(None, "--model-class", help="Model class to use (e.g., 'anthropic' or 'minisweagent.models.anthropic.AnthropicModel')", rich_help_panel="Advanced"),
    redo_existing: bool = typer.Option(False, "--redo-existing", help="Redo existing instances", rich_help_panel="Data selection"),
    environment_class: str | None = typer.Option(None, "--environment-class", help="Environment type to use. Recommended are docker or singularity", rich_help_panel="Advanced"),
    vanilla: bool = typer.Option(False, "--vanilla", help="Vanilla mode: no test injection (uses swebench_vanilla.yaml)", rich_help_panel="Mode"),
    eotter: bool = typer.Option(False, "--eotter", help="E-Otter mode: inject eotter test into prompt (uses swebench_eotter.yaml)", rich_help_panel="Mode"),
    potter: bool = typer.Option(False, "--potter", help="Potter mode: install PBT in container (uses swebench_potter.yaml)", rich_help_panel="Mode"),
    merged: bool = typer.Option(False, "--merged", help="Merged mode: inject eotter test into prompt AND install PBT in container (uses swebench_merged.yaml)", rich_help_panel="Mode"),
    merged_static: bool = typer.Option(False, "--merged-static", help="Merged-static mode: inject eotter and PBT tests into prompt only, nothing installed in container (uses swebench_vanilla_tests.yaml)", rich_help_panel="Mode"),
    tests_file: Path | None = typer.Option(None, "--tests", help="Path to tests JSON file (list of {instance_id, model_patch} objects); required for --eotter and --potter", rich_help_panel="Mode"),
    eotter_tests_file: Path | None = typer.Option(None, "--eotter-tests", help="Path to eotter tests JSON file (list of {instance_id, model_patch} objects); required for --merged and --merged-static", rich_help_panel="Mode"),
    potter_tests_file: Path | None = typer.Option(None, "--potter-tests", help="Path to potter/PBT tests JSON file (list of {instance_id, model_patch} objects); required for --merged and --merged-static", rich_help_panel="Mode"),
) -> None:
    # fmt: on
    # Validate mode flags
    mode_flags = [vanilla, eotter, potter, merged, merged_static]
    if sum(mode_flags) != 1:
        raise typer.BadParameter("Exactly one of --vanilla, --eotter, --potter, --merged, or --merged-static must be specified.")
    if (eotter or potter) and tests_file is None:
        raise typer.BadParameter("--tests is required when using --eotter or --potter.")
    if (merged or merged_static) and (eotter_tests_file is None or potter_tests_file is None):
        raise typer.BadParameter("--eotter-tests and --potter-tests are both required when using --merged or --merged-static.")

    mode = (
        "vanilla" if vanilla else
        "eotter" if eotter else
        "potter" if potter else
        "merged" if merged else
        "merged_static"
    )
    config_file = {
        "vanilla": CONFIG_FILE_VANILLA,
        "eotter":  CONFIG_FILE_EOTTER,
        "potter":  CONFIG_FILE_POTTER,
        "merged":  CONFIG_FILE_MERGED,
        "merged_static": CONFIG_FILE_MERGED_STATIC,
    }[mode]

    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Results will be saved to {output_path}")
    add_file_handler(output_path / "minisweagent.log")

    instances = load_instances(subset, split)

    instances = filter_instances(instances, filter_spec=filter_spec, slice_spec=slice_spec, shuffle=shuffle)
    if not redo_existing and (output_path / "preds.json").exists():
        existing_instances = list(json.loads((output_path / "preds.json").read_text()).keys())
        logger.info(f"Skipping {len(existing_instances)} existing instances")
        instances = [instance for instance in instances if instance["instance_id"] not in existing_instances]
    logger.info(f"Running on {len(instances)} instances...")

    logger.info(f"Building agent config from: {config_file}")
    config = get_config_from_spec(str(config_file))
    if environment_class or model or model_class:
        config = recursive_merge(config, {
            "environment": {"environment_class": environment_class or UNSET},
            "model": {"model_name": model or UNSET, "model_class": model_class or UNSET},
        })

    # Load tests if provided: build {instance_id -> patch_str} lookups
    def _load_tests(path: Path) -> dict[str, str]:
        entries = json.loads(path.read_text())
        return {e["instance_id"]: e["model_patch"] for e in entries}

    eotter_tests: dict[str, str] | None = None
    potter_tests: dict[str, str] | None = None
    if mode == "eotter" and tests_file is not None:
        eotter_tests = _load_tests(tests_file)
        logger.info(f"Loaded eotter tests for {len(eotter_tests)} instances from '{tests_file}'")
    elif mode == "potter" and tests_file is not None:
        potter_tests = _load_tests(tests_file)
        logger.info(f"Loaded potter tests for {len(potter_tests)} instances from '{tests_file}'")
    elif mode in ("merged", "merged_static"):
        eotter_tests = _load_tests(eotter_tests_file)  # type: ignore[arg-type]
        logger.info(f"Loaded eotter tests for {len(eotter_tests)} instances from '{eotter_tests_file}'")
        potter_tests = _load_tests(potter_tests_file)  # type: ignore[arg-type]
        logger.info(f"Loaded potter tests for {len(potter_tests)} instances from '{potter_tests_file}'")

    progress_manager = RunBatchProgressManager(len(instances), output_path / f"exit_statuses_{time.time()}.yaml")

    def process_futures(futures: dict[concurrent.futures.Future, str]):
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except concurrent.futures.CancelledError:
                pass
            except Exception as e:
                instance_id = futures[future]
                logger.error(f"Error in future for instance {instance_id}: {e}", exc_info=True)
                progress_manager.on_uncaught_exception(instance_id, e)

    with Live(progress_manager.render_group, refresh_per_second=4):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_instance, instance, output_path, config, progress_manager, mode, eotter_tests, potter_tests): instance[
                    "instance_id"
                ]
                for instance in instances
            }
            try:
                process_futures(futures)
            except KeyboardInterrupt:
                logger.info("Cancelling all pending jobs. Press ^C again to exit immediately.")
                for future in futures:
                    if not future.running() and not future.done():
                        future.cancel()
                process_futures(futures)


if __name__ == "__main__":
    app()
