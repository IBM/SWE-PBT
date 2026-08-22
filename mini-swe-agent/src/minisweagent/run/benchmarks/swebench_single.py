"""Run on a single SWE-Bench instance."""

import json
from pathlib import Path

import typer

from minisweagent import global_config_dir
from minisweagent.agents import get_agent
from minisweagent.config import builtin_config_dir, get_config_from_spec
from minisweagent.models import get_model
from minisweagent.run.benchmarks.swebench import (
    DATASET_MAPPING,
    CONFIG_FILE_EOTTER,
    CONFIG_FILE_POTTER,
    CONFIG_FILE_VANILLA,
    _CONFTEST_SRC,
    _setup_eotter_in_container,
    _setup_pbt_in_container,
    get_sb_environment,
    load_instances,
)
from minisweagent.utils.log import logger
from minisweagent.utils.serialize import UNSET, recursive_merge

DEFAULT_OUTPUT_FILE = global_config_dir / "last_swebench_single_run.traj.json"

app = typer.Typer(rich_markup_mode="rich", add_completion=False)


# fmt: off
@app.command()
def main(
    subset: str = typer.Option("lite", "--subset", help="SWEBench subset to use or path to a dataset", rich_help_panel="Data selection"),
    split: str = typer.Option("dev", "--split", help="Dataset split", rich_help_panel="Data selection"),
    instance_spec: str = typer.Option(0, "-i", "--instance", help="SWE-Bench instance ID or index", rich_help_panel="Data selection"),
    model_name: str | None = typer.Option(None, "-m", "--model", help="Model to use", rich_help_panel="Basic"),
    model_class: str | None = typer.Option(None, "--model-class", help="Model class to use (e.g., 'anthropic' or 'minisweagent.models.anthropic.AnthropicModel')", rich_help_panel="Advanced"),
    agent_class: str | None = typer.Option(None, "--agent-class", help="Agent class to use (e.g., 'interactive' or 'minisweagent.agents.interactive.InteractiveAgent')", rich_help_panel="Advanced"),
    environment_class: str | None = typer.Option(None, "--environment-class", help="Environment class to use (e.g., 'docker' or 'minisweagent.environments.docker.DockerEnvironment')", rich_help_panel="Advanced"),
    yolo: bool = typer.Option(False, "-y", "--yolo", help="Run without confirmation"),
    cost_limit: float | None = typer.Option(None, "-l", "--cost-limit", help="Cost limit. Set to 0 to disable."),
    exit_immediately: bool = typer.Option(False, "--exit-immediately", help="Exit immediately when the agent wants to finish instead of prompting.", rich_help_panel="Advanced"),
    output: Path | None = typer.Option(DEFAULT_OUTPUT_FILE, "-o", "--output", help="Output trajectory file", rich_help_panel="Basic"),
    vanilla: bool = typer.Option(False, "--vanilla", help="Vanilla mode: no test injection (uses swebench_vanilla.yaml)", rich_help_panel="Mode"),
    eotter: bool = typer.Option(False, "--eotter", help="E-Otter mode: inject eotter test into prompt (uses swebench_eotter.yaml)", rich_help_panel="Mode"),
    potter: bool = typer.Option(False, "--potter", help="Potter mode: install PBT in container (uses swebench_potter.yaml)", rich_help_panel="Mode"),
    tests_file: Path | None = typer.Option(None, "--tests", help="Path to tests JSON file (list of {instance_id, model_patch} objects); required for --eotter and --potter", rich_help_panel="Mode"),
) -> None:
    # fmt: on
    """Run on a single SWE-Bench instance."""
    # Validate mode flags
    mode_flags = [vanilla, eotter, potter]
    if sum(mode_flags) != 1:
        raise typer.BadParameter("Exactly one of --vanilla, --eotter, or --potter must be specified.")
    if (eotter or potter) and tests_file is None:
        raise typer.BadParameter("--tests is required when using --eotter or --potter.")

    mode = "vanilla" if vanilla else ("eotter" if eotter else "potter")
    config_file = {
        "vanilla": CONFIG_FILE_VANILLA,
        "eotter":  CONFIG_FILE_EOTTER,
        "potter":  CONFIG_FILE_POTTER,
    }[mode]

    instances = {
        inst["instance_id"]: inst  # type: ignore
        for inst in load_instances(subset, split)
    }
    if instance_spec.isnumeric():
        instance_spec = sorted(instances.keys())[int(instance_spec)]
    instance: dict = instances[instance_spec]  # type: ignore

    logger.info(f"Building agent config from: {config_file}")
    config = get_config_from_spec(str(config_file))
    overrides: dict = {"agent": {}, "model": {}, "environment": {}}
    overrides["agent"]["agent_class"] = agent_class or UNSET
    overrides["agent"]["mode"] = "yolo" if yolo else UNSET
    overrides["agent"]["cost_limit"] = cost_limit if cost_limit is not None else UNSET
    overrides["agent"]["confirm_exit"] = False if exit_immediately else UNSET
    overrides["agent"]["output_path"] = output or UNSET
    overrides["model"]["model_class"] = model_class or UNSET
    overrides["model"]["model_name"] = model_name or UNSET
    overrides["environment"]["environment_class"] = environment_class or UNSET
    config = recursive_merge(config, overrides)

    env = get_sb_environment(config, instance)
    extra_run_kwargs: dict = {}

    if mode == "potter":
        if tests_file is not None:
            entries = json.loads(tests_file.read_text())
            tests = {e["instance_id"]: e["model_patch"] for e in entries}
            if patch := tests.get(instance["instance_id"]):
                _setup_pbt_in_container(env, instance["instance_id"], patch, _CONFTEST_SRC)
            else:
                logger.warning(f"No potter test found for instance '{instance['instance_id']}' in '{tests_file}'")
    elif mode == "eotter":
        if tests_file is not None:
            entries = json.loads(tests_file.read_text())
            tests = {e["instance_id"]: e["model_patch"] for e in entries}
            if patch := tests.get(instance["instance_id"]):
                _setup_eotter_in_container(env, instance["instance_id"], patch)
                extra_run_kwargs["eotter_test"] = patch
            else:
                logger.warning(f"No eotter test found for instance '{instance['instance_id']}' in '{tests_file}'")

    agent = get_agent(
        get_model(config=config.get("model", {})),
        env,
        config.get("agent", {}),
        default_type="interactive",
    )
    agent.run(instance["problem_statement"], **extra_run_kwargs)


if __name__ == "__main__":
    app()
