from __future__ import annotations

import docker
import json
import time

from argparse import ArgumentParser
from pathlib import Path
from typing import Any, cast

import socket

from tddbench.harness.constants import SWEbenchInstance
from tddbench.harness.docker_build import build_container, close_logger, setup_logger
from tddbench.harness.docker_utils import copy_to_container
from tddbench.harness.test_spec import make_test_spec
from tddbench.harness.utils import load_tddbench_dataset


def _load_prediction_file(predictions_path: str | None) -> dict:
    if not predictions_path:
        return {}
    with open(predictions_path, "r") as f:
        data = json.load(f)
        # If data is a list, convert it to a dict keyed by instance_id
        if isinstance(data, list):
            return {item["instance_id"]: item for item in data if "instance_id" in item}
        return data


def _select_test_patch(instance: SWEbenchInstance, prediction_data: dict[str, Any], test_patch_index: int) -> tuple[str, int]:
    instance_id = instance["instance_id"]

    pred = prediction_data.get(instance_id, {})
    test_patches = pred.get("test_patches", pred.get("test_patch_list", pred.get("model_patch")))

    if test_patches is None:
        test_patches = instance.get("test_patch", "")

    if not isinstance(test_patches, list):
        test_patches = [test_patches] if test_patches else []

    if not test_patches:
        raise ValueError(f"No test patches available for instance {instance_id}")

    if test_patch_index < 0 or test_patch_index >= len(test_patches):
        raise IndexError(
            f"test_patch_index={test_patch_index} out of range for instance {instance_id}; "
            f"available patches={len(test_patches)}"
        )

    return test_patches[test_patch_index] or "", test_patch_index + 1


def _reset_repo(container, base_commit: str):
    commands = [
        "git reset --hard",
        "git clean -fd",
        "find /testbed -name '*.rej' -delete",
        "find /testbed -name '*.orig' -delete",
        f"git checkout {base_commit}",
        "git reset --hard",
        "git clean -fd",
    ]
    for cmd in commands:
        result = container.exec_run(cmd, workdir="/testbed", user="root")
        if result.exit_code != 0:
            raise RuntimeError(
                f"Failed repo reset command: {cmd}\n{result.output.decode('utf-8', errors='replace')}"
            )


def _stop_existing_debug_server(container, port: int = 9999):
    """Stop existing debug server and ensure port is released."""
    # Find and kill debug_server.py processes using ps and grep
    kill_cmd = "ps aux | grep '[d]ebug_server.py' | awk '{print $2}' | xargs -r kill -9 || true"
    kill_result = container.exec_run(["/bin/bash", "-c", kill_cmd], user="root")
    print(f"[run_debugging] Killed debug_server processes")
    
    # Clean up log file
    container.exec_run("rm -f /tmp/debug_server.log", user="root")


def _apply_test_patch(container, instance_id: str, test_patch: str):
    local_patch_path = Path(f"/tmp/run_debugging_{instance_id}.diff")
    local_patch_path.write_text(test_patch)

    try:
        container_patch_path = Path(f"/tmp/run_debugging_{instance_id}.diff")
        copy_to_container(container, local_patch_path, container_patch_path)

        apply_result = container.exec_run(
            f"git apply --allow-empty -v {container_patch_path}",
            workdir="/testbed",
            user="root",
        )
        if apply_result.exit_code != 0:
            apply_result = container.exec_run(
                f"patch --batch --fuzz=5 -p1 -i {container_patch_path}",
                workdir="/testbed",
                user="root",
            )
        if apply_result.exit_code != 0:
            raise RuntimeError(
                "Failed to apply selected test patch:\n"
                + apply_result.output.decode("utf-8", errors="replace")
            )
    finally:
        local_patch_path.unlink(missing_ok=True)


def _post_process_test_file(container):
    """
    Post-process /testbed/pbt/test.py to ensure it has the correct entry point.
    Removes everything from 'if __name__ == "__main__"' onwards and replaces it
    with the standard pytest entry point.
    """
    # Read the current test file
    read_result = container.exec_run(
        "cat /testbed/pbt/test.py",
        workdir="/testbed",
        user="root",
    )
    
    if read_result.exit_code != 0:
        print(f"[run_debugging] Warning: Could not read /testbed/pbt/test.py for post-processing")
        return
    
    content = read_result.output.decode("utf-8", errors="replace")
    
    # Find the position of 'if __name__ == "__main__"' and remove everything after it
    # If not found (main_block_pos == -1), we'll just append the entry point to the end
    main_block_pos = content.find('if __name__ == "__main__"')
    
    if main_block_pos != -1:
        # Remove everything from the main block onwards
        content = content[:main_block_pos].rstrip()
    else:
        # No existing main block, just ensure content ends cleanly
        content = content.rstrip()
    
    # Add the standard entry point (works whether or not there was an existing main block)
    entry_point = '''
# Entry point:
if __name__ == "__main__":
    import pytest
    pytest.main([__file__, '--hypothesis-verbosity=verbose', '--hypothesis-seed=0', '-vs'])
'''
    
    content = content + entry_point
    
    # Write the modified content back to the file
    # We need to escape the content properly for the shell command
    import shlex
    
    # Write to a temporary file first, then move it
    write_cmd = f"cat > /tmp/test_py_temp << 'EOF'\n{content}\nEOF\n"
    write_result = container.exec_run(
        ["/bin/bash", "-c", write_cmd],
        workdir="/testbed",
        user="root",
    )
    
    if write_result.exit_code != 0:
        print(f"[run_debugging] Warning: Failed to write temporary test file")
        return
    
    # Move the temporary file to the actual location
    move_result = container.exec_run(
        "mv /tmp/test_py_temp /testbed/pbt/test.py",
        workdir="/testbed",
        user="root",
    )
    
    if move_result.exit_code != 0:
        print(f"[run_debugging] Warning: Failed to move test file to final location")
        return
    
    print(f"[run_debugging] Successfully post-processed /testbed/pbt/test.py")


def _get_debug_file_paths() -> tuple[Path, Path]:
    debug_files_dir = Path(__file__).parent.parent.parent.parent
    return debug_files_dir / "inspectware.py", debug_files_dir / "debug_server.py"


def _copy_debug_files(container):
    inspectware_path, debug_server_path = _get_debug_file_paths()

    print(f"[run_debugging] Inspectware path: {inspectware_path}, exists: {inspectware_path.exists()}")
    print(f"[run_debugging] Debug server path: {debug_server_path}, exists: {debug_server_path.exists()}")

    if not inspectware_path.exists() or not debug_server_path.exists():
        raise FileNotFoundError(
            f"Missing debug files. inspectware={inspectware_path}, debug_server={debug_server_path}"
        )

    copy_to_container(container, inspectware_path, Path("/inspectware.py"))
    copy_to_container(container, debug_server_path, Path("/debug_server.py"))

    verify_result = container.exec_run("ls -la /inspectware.py /debug_server.py", user="root")
    if verify_result.exit_code != 0:
        raise RuntimeError(
            "Failed to verify copied debug files:\n"
            + verify_result.output.decode("utf-8", errors="replace")
        )


def _install_debug_dependencies(container):
    install_result = container.exec_run(
        ["/bin/bash", "-c", "python3.10 -m pip install pandas -q"],
        user="root",
    )
    if install_result.exit_code != 0:
        print(
            "[run_debugging] Warning: Failed to install pandas: "
            + install_result.output.decode("utf-8", errors="replace")
        )


def _start_debug_server(container, target_file: str = "/testbed/pbt/test.py", port: int = 9999):
    start_cmd = [
        "/bin/bash",
        "-c",
        f"nohup python3.10 /debug_server.py {target_file} {port} > /tmp/debug_server.log 2>&1 &",
    ]
    container.exec_run(start_cmd, detach=True, user="root")
    time.sleep(5)


def _setup_debug_server(container, logger, port: int = 9999):
    print(f"[run_debugging] Setting up debug server in container on port {port}...")
    try:
        _copy_debug_files(container)
        _install_debug_dependencies(container)
        _start_debug_server(container, port=port)
    except Exception as e:
        print(f"[run_debugging] Warning: Failed to setup debug server: {e}")
        logger.warning(f"Failed to setup debug server: {e}")
        import traceback
        traceback.print_exc()
        raise


def get_container_port_mapping(container, container_port: int = 9999) -> int | None:
    """Get the host port mapped to a container port, if any."""
    try:
        container.reload()
        port_bindings = container.attrs.get('NetworkSettings', {}).get('Ports', {})
        container_port_key = f"{container_port}/tcp"
        
        if container_port_key in port_bindings and port_bindings[container_port_key]:
            host_binding = port_bindings[container_port_key][0]
            return int(host_binding['HostPort'])
    except (KeyError, TypeError, ValueError, IndexError):
        pass
    return None


def _ensure_port_mapping(container, container_port: int = 9999) -> int:
    """
    Ensure container has a port mapping for the debug server.
    Returns the host port to use.
    
    If container already has a port mapping, return that.
    Otherwise, this is a limitation - we can't add port mappings to existing containers.
    """
    existing_port = get_container_port_mapping(container, container_port)
    if existing_port:
        print(f"[run_debugging] Container already has port mapping: {existing_port} -> {container_port}")
        return existing_port
    
    # For existing containers without port mapping, we'll use the container port directly
    # This assumes the container network allows direct access
    print(f"[run_debugging] No existing port mapping found, using container port {container_port} directly")
    return container_port


def _wait_for_debug_server(container, timeout_seconds: int = 10):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = container.exec_run("pgrep -f debug_server.py", user="root")
        if result.exit_code == 0 and result.output.decode("utf-8").strip():
            return result.output.decode("utf-8").strip()
        time.sleep(1)
    return None


def run_debugging(
    dataset_name: str,
    instance_id: str,
    test_patch_index: int = 0,
    predictions_path: str | None = None,
    run_id: str = "debug",
):
    dataset = load_tddbench_dataset(dataset_name, instance_ids=[instance_id])
    if not dataset:
        raise ValueError(f"Instance {instance_id} not found in dataset {dataset_name}")

    instance = cast(SWEbenchInstance, dataset[0])
    prediction_data = _load_prediction_file(predictions_path)
    selected_test_patch, selected_test_version = _select_test_patch(
        instance, prediction_data, test_patch_index
    )

    instance_for_spec = cast(SWEbenchInstance, dict(instance))
    instance_for_spec["test_patch"] = selected_test_patch
    test_spec = make_test_spec(instance_for_spec)

    client = docker.from_env()
    log_dir = Path("Test_execution/logs/run_debugging") / run_id / instance_id
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(instance_id, log_dir / "run_debugging.log")

    try:
        container = build_container(
            test_spec=test_spec,
            client=client,
            run_id=run_id,
            logger=logger,
            nocache=False,
            force_rebuild=False,
            reuse_container=True,
        )

        container.reload()
        if container.status != "running":
            container.start()
            container.reload()

        # Determine which port to use (check existing mapping or use default)
        container_port = 9999
        host_port = _ensure_port_mapping(container, container_port)
        
        _stop_existing_debug_server(container, port=container_port)
        _reset_repo(container, instance["base_commit"])
        _apply_test_patch(container, instance_id, selected_test_patch)
        _post_process_test_file(container)
        
        # Initialize IS_REGRESSION flag file (default to true)
        init_cmd = 'bash -c "echo \\"true\\" > /tmp/is_regression.txt"'
        container.exec_run(init_cmd, user="root")
        print("[run_debugging] Initialized /tmp/is_regression.txt with true")
        
        _setup_debug_server(container, logger, port=container_port)

        server_pid = _wait_for_debug_server(container)
        if not server_pid:
            raise RuntimeError("Debug server did not start successfully")

        print(json.dumps({
            "instance_id": instance_id,
            "container_name": test_spec.get_instance_container_name(run_id),
            "container_id": container.id,
            "selected_test_patch_index": test_patch_index,
            "selected_test_version": selected_test_version,
            "base_commit": instance["base_commit"],
            "debug_server_port": container_port,
            "debug_server_host_port": host_port,
            "debug_server_pid": server_pid,
            "test_file": "/testbed/pbt/test.py",
            "repo_dir": "/testbed",
            "status": "ready"
        }, indent=2))
    finally:
        close_logger(logger)


def main():
    parser = ArgumentParser(description="Prepare a single TDDBench instance container for remote debugging.")
    parser.add_argument("--dataset_name", type=str, required=True, help="Path to TDDBench dataset JSON/JSONL")
    parser.add_argument("--instance_id", type=str, required=True, help="Target instance_id")
    parser.add_argument("--test_patch_index", type=int, default=0, help="0-based selected test patch index")
    parser.add_argument("--predictions_path", type=str, default=None, help="Optional predictions JSON with test_patches")
    parser.add_argument("--run_id", type=str, default="debug", help="Run id used in container naming/logging")
    args = parser.parse_args()

    run_debugging(
        dataset_name=args.dataset_name,
        instance_id=args.instance_id,
        test_patch_index=args.test_patch_index,
        predictions_path=args.predictions_path,
        run_id=args.run_id,
    )


if __name__ == "__main__":
    main()

# Made with Bob
