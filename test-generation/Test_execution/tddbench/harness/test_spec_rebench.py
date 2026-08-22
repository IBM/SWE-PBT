import hashlib
import json
import platform
import re

from dataclasses import dataclass
from typing import Any, Union, cast

from tddbench.harness.constants import (
    SWEbenchInstance,
    KEY_INSTANCE_ID,
    MAP_REPO_TO_INSTALL,
    MAP_REPO_VERSION_TO_SPECS,
    USE_X86,
)
from tddbench.harness.dockerfiles import (
    get_dockerfile_base,
    get_dockerfile_env,
    get_dockerfile_instance,
)
from tddbench.harness.utils import (
    get_requirements,
    get_environment_yml,
)
START_TEST_OUTPUT = ">>>>> Start Test Output"
DIFF_MODIFIED_FILE_REGEX = r"--- a/(.*)"


TEST_CMD_MAPPING = {
    "sktime__sktime-8723"                           : "cd /testbed/pbt && env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 IS_REGRESSION=$IS_REGRESSION coverage run --branch -m pytest --hypothesis-log-all test.py -vs --no-header -W ignore::DeprecationWarning",
    "sktime__sktime-8921"                           : "cd /testbed/pbt && env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 IS_REGRESSION=$IS_REGRESSION coverage run --branch -m pytest --hypothesis-log-all test.py -vs --no-header -W ignore::DeprecationWarning",
    "sktime__sktime-8937"                           : "cd /testbed/pbt && env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 IS_REGRESSION=$IS_REGRESSION coverage run --branch -m pytest --hypothesis-log-all test.py -vs --no-header -W ignore::DeprecationWarning",
    "astronomer__astronomer-cosmos-2332"            : "cd /testbed/pbt && export AIRFLOW__CORE__LOAD_EXAMPLES=False && export AIRFLOW__CORE__DAGS_FOLDER=/tmp/airflow/dags && export AIRFLOW_HOME=/tmp/airflow && mkdir -p /tmp/airflow/dags && IS_REGRESSION=$IS_REGRESSION coverage run --branch -m pytest --hypothesis-log-all test.py -vs --no-header -W ignore::DeprecationWarning",
}


@dataclass
class TestSpec:
    """
    A dataclass that represents a test specification for a single instance of SWE-bench.
    """
    instance_id: str
    repo: str
    version: str
    repo_script_list: list[str]
    eval_script_list: list[str]
    env_script_list: list[str]
    arch: str
    test_patch: str
    benchmark_image: str | None = None

    @property
    def setup_env_script(self):
        return "\n".join(["#!/bin/bash", "set -euo pipefail"] + self.env_script_list) + "\n"

    @property
    def eval_script(self):
        return "\n".join(["#!/bin/bash", "set -uxo pipefail"] + self.eval_script_list) + "\n"
        # Don't exit early because we need to revert tests at the end

    @property
    def install_repo_script(self):
        return "\n".join(["#!/bin/bash", "set -euo pipefail"] + self.repo_script_list) + "\n"

    @property
    def base_image_key(self):
        return f"sweb.base.{self.arch}:latest"

    @property
    def env_image_key(self):
        """
        Prefer the benchmark-provided environment image when available.
        Otherwise derive a deterministic environment image key from the env script list.

        Note that old images are not automatically deleted, so consider cleaning up old images periodically.
        """
        if self.benchmark_image:
            return self.benchmark_image
        hash_object = hashlib.sha256()
        hash_object.update(str(self.env_script_list).encode("utf-8"))
        hash_value = hash_object.hexdigest()
        val = hash_value[:22]  # 22 characters is still very likely to be unique
        return f"sweb.env.{self.arch}.{val}:latest"

    @property
    def instance_image_key(self):
        return f"sweb.eval.{self.arch}.{self.instance_id}:latest"

    def get_instance_container_name(self, run_id=None):
        if not run_id:
            return f"sweb.eval.{self.instance_id}"
        return f"sweb.eval.{self.instance_id}.{run_id}"

    @property
    def base_dockerfile(self):
        return get_dockerfile_base(self.platform, self.arch)

    @property
    def env_dockerfile(self):
        return get_dockerfile_env(self.platform, self.arch)

    @property
    def instance_dockerfile(self):
        return get_dockerfile_instance(self.platform, self.env_image_key)

    @property
    def platform(self):
        if self.arch == "x86_64":
            return "linux/x86_64"
        elif self.arch == "arm64":
            return "linux/arm64/v8"
        else:
            raise ValueError(f"Invalid architecture: {self.arch}")


def get_test_specs_from_dataset(dataset: Union[list[SWEbenchInstance], list[TestSpec]]) -> list[TestSpec]:
    """
    Idempotent function that converts a list of SWEbenchInstance objects to a list of TestSpec objects.
    """
    if isinstance(dataset[0], TestSpec):
        return cast(list[TestSpec], dataset)
    return list(map(make_test_spec, cast(list[SWEbenchInstance], dataset)))


def resolve_specs(instance):
    repo = instance["repo"]
    version = instance["version"]

    if "install_config" in instance and instance["install_config"]:
        install_config = instance["install_config"]
        return {
            "python": install_config.get("python", "3.10"),
            "install": install_config.get("install", "python -m pip install -e ."),
            "test_cmd": install_config.get("test_cmd", "pytest -xvs"),
            "packages": install_config.get("packages", ""),
            "pip_packages": install_config.get("pip_packages", []),
            "pre_install": install_config.get("pre_install", []),
        }
    if repo not in MAP_REPO_VERSION_TO_SPECS or version not in MAP_REPO_VERSION_TO_SPECS[repo]:
        return {
            "python": "3.10",
            "install": "python -m pip install -e .",
            "test_cmd": "pytest -xvs",
        }
    return MAP_REPO_VERSION_TO_SPECS[repo][version]



def make_repo_script_list(specs, repo, repo_directory, base_commit, env_name):
    """
    Create a list of bash commands to set up the repository for testing.
    This is the setup script for the instance image.
    """
    setup_commands = [
        f"git clone -o origin https://github.com/{repo} {repo_directory}",
        f"chmod -R 777 {repo_directory}",  # So nonroot user can run tests
        f"cd {repo_directory}",
        f"git reset --hard {base_commit}",
        # Remove the remote so the agent won't see newer commits.
        f"git remote remove origin",
        # Make sure conda is available for later use
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
        f'echo "Current environment: $CONDA_DEFAULT_ENV"',
    ]
    if repo in MAP_REPO_TO_INSTALL:
        setup_commands.append(MAP_REPO_TO_INSTALL[repo])

    # Run pre-install set up if provided
    if "pre_install" in specs:
        for pre_install in specs["pre_install"]:
            setup_commands.append(pre_install)

    if "install" in specs:
        setup_commands.append(specs["install"])
    return setup_commands


def make_env_script_list(instance, specs, env_name):
    """
    Creates the list of commands to set up the conda environment for testing.
    This is the setup script for the environment image.
    
    CALLED ONCE per instance_id (not per test-code pair):
    - Called during TestSpec creation in make_test_spec() (line 350)
    - Generates bash commands to create conda environment and install dependencies
    
    EXECUTED ONCE per instance_id when building the Docker environment image:
    - The env_script_list is converted to setup_env_script (property at line 46)
    - Executed during Docker image build in docker_build.py (line 252)
    - The resulting Docker image is cached and reused for all test-code pairs
    
    NOT executed again for each test-code pair:
    - When evaluating different test/code patch combinations (run_evaluation.py line 419-428),
      the same env_script_list is copied to test_version_spec (line 425)
    - The Docker container uses the pre-built environment image
    - Only eval_script_list is executed for each test-code pair
    """
    HEREDOC_DELIMITER = "EOF_59812759871"
    reqs_commands = [
        "source /opt/miniconda3/bin/activate",
    ]
    # Create conda environment according to install instructinos
    pkgs = specs.get("packages", "")
    if pkgs == "requirements.txt":
        # Create environment
        cmd = f"conda create -n {env_name} python={specs['python']} -y"
        reqs_commands.append(cmd)

        # Install dependencies
        reqs = get_requirements(instance)
        path_to_reqs = "$HOME/requirements.txt"
        reqs_commands.append(
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs}\n{HEREDOC_DELIMITER}"
        )
        cmd = f"conda activate {env_name} && python -m pip install -r {path_to_reqs}"
        reqs_commands.append(cmd)
        reqs_commands.append(f"rm {path_to_reqs}")
    elif pkgs == "environment.yml":
        # Create environment from yml
        reqs = get_environment_yml(instance, env_name)
        path_to_reqs = "environment.yml"
        reqs_commands.append(
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs}\n{HEREDOC_DELIMITER}"
        )
        if "no_use_env" in specs and specs["no_use_env"]:
            # `conda create` based installation
            cmd = f"conda create -c conda-forge -n {env_name} python={specs['python']} -y"
            reqs_commands.append(cmd)

            # Install dependencies
            cmd = f"conda env update -f {path_to_reqs}"
            reqs_commands.append(cmd)
        else:
            # `conda env create` based installation
            cmd = f"conda env create --file {path_to_reqs}"
            reqs_commands.append(cmd)

            cmd = f"conda activate {env_name} && conda install python={specs['python']} -y"
            reqs_commands.append(cmd)

        # Remove environment.yml
        reqs_commands.append(f"rm {path_to_reqs}")
    else:
        # Create environment + install dependencies
        cmd = f"conda create -n {env_name} python={specs['python']} {pkgs} -y"
        reqs_commands.append(cmd)

    reqs_commands.append(f"conda activate {env_name}")

    # Install additional packages if specified
    if "pip_packages" in specs and specs["pip_packages"]:
        pip_packages = " ".join(specs["pip_packages"])
        cmd = f"python -m pip install {pip_packages}"
        reqs_commands.append(cmd)
    
    cmd = f"python -m pip install 'hypothesis>=6,<6.100' pytest 'coverage>=3,<8'"
    reqs_commands.append(cmd)
    
    return reqs_commands


def make_eval_script_list(instance, specs, env_name, repo_directory, base_commit, test_patch):
    """
    Applies the test patch and runs the tests.
    """
    if isinstance(test_patch, list):
        test_patch = test_patch[0] if test_patch else ""
    elif test_patch is None:
        test_patch = ""

    HEREDOC_DELIMITER = "EOF_114329324912"
    test_files = re.findall(DIFF_MODIFIED_FILE_REGEX, test_patch)
    # Reset test files to the state they should be in before the patch.
    reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
    clean_command="git clean -fd"
    apply_test_patch_command = (
        f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{test_patch}\n{HEREDOC_DELIMITER}"
    )
    copy_conftest_command = "cp /tmp/conftest.py /testbed/pbt/"
    test_command = ""
    if instance["instance_id"] in TEST_CMD_MAPPING:
        test_command = TEST_CMD_MAPPING[instance["instance_id"]]
    else:
        test_command = "cd /testbed/pbt && IS_REGRESSION=$IS_REGRESSION coverage run --branch -m pytest --hypothesis-log-all test.py -vs --no-header -W ignore::DeprecationWarning"
    copy_hypothesis_summary_command = "cp /testbed/pbt/hypothesis_summary.json /tmp/"
    
    eval_commands = [
        f"source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
        f"cd {repo_directory}",
    ]
    if "eval_commands" in specs:
        eval_commands += specs["eval_commands"]
    eval_commands += [
        f"git config --global --add safe.directory {repo_directory}",  # for nonroot user
        f"cd {repo_directory}",
        # This is just informational, so we have a record
        f"git status",
        f"git show",
        f"git diff {base_commit}",
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
    ]
    
    coverage_command="echo \"+ coverage report\" ; coverage report --show-missing"

    if "install" in specs:
        eval_commands.append(specs["install"])

    eval_commands += [
        clean_command,
        reset_tests_command,
        apply_test_patch_command,
        f"echo \"+ : '{START_TEST_OUTPUT}'\"",
        copy_conftest_command,
        test_command,
        coverage_command,
        copy_hypothesis_summary_command,
        clean_command,
        reset_tests_command,  # Revert tests after done, leave the repo in the same state as before
    ]


    return eval_commands


def make_test_spec(instance: SWEbenchInstance) -> TestSpec:
    """
    Creates a TestSpec object for a single instance.
    
    This function is called once per instance_id to generate all the necessary
    scripts for setting up the environment, repository, and evaluation.
    The generated TestSpec is then reused across multiple test-code pairs for the same instance.
    """
    if isinstance(instance, TestSpec):
        return instance
    instance_id = instance[KEY_INSTANCE_ID]
    repo = instance["repo"]
    version = instance["version"]
    base_commit = instance["base_commit"]
    problem_statement = instance["problem_statement"]
    hints_text = instance["hints_text"]  # Unused
    test_patch = instance["test_patch"]
    if isinstance(test_patch, list):
        test_patch = test_patch[0] if test_patch else ""
    elif test_patch is None:
        test_patch = ""

    def _from_json_or_obj(key: str) -> Any:
        """If key points to string, load with json"""
        if isinstance(instance[key], str):
            return json.loads(instance[key])
        return instance[key]

    #pass_to_pass = _from_json_or_obj(PASS_TO_PASS)
    #fail_to_pass = _from_json_or_obj(FAIL_TO_PASS)

    env_name = "testbed"
    repo_directory = f"/{env_name}"
    
    specs = resolve_specs(instance)


    
    # Generate script lists for this instance_id (called once, reused for all test-code pairs):
    # 1. repo_script_list: Commands to clone and set up the repository
    repo_script_list = make_repo_script_list(specs, repo, repo_directory, base_commit, env_name)
    
    # 2. env_script_list: Commands to create and configure the conda environment
    #    Called ONCE per instance_id (not per test-code pair) to set up the Python environment
    #    with all required dependencies. This env_script_list is reused across all test-code
    #    pairs for the same instance since the environment setup remains constant.
    env_script_list = make_env_script_list(instance, specs, env_name)
    
    # 3. eval_script_list: Commands to apply test patch and run tests
    #    This may be regenerated for different test patches within the same instance
    eval_script_list = make_eval_script_list(
        instance, specs, env_name, repo_directory, base_commit, test_patch
    )
    if platform.machine() in {"aarch64", "arm64"}:
        # use arm64 unless explicitly specified
        arch = "arm64" if instance_id not in USE_X86 else "x86_64"
    else:
        arch = "x86_64"
    # Don't use benchmark images - always build our own with conda
    benchmark_image = None

    return TestSpec(
        instance_id=instance_id,
        repo=repo,
        env_script_list=env_script_list,
        repo_script_list=repo_script_list,
        eval_script_list=eval_script_list,
        version=version,
        arch=arch,
        test_patch=test_patch,
        benchmark_image=benchmark_image,
    )
