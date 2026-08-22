from ast import arg
import json
import os
from argparse import ArgumentParser
import shutil
from tempfile import tempdir
import pandas as pd
import subprocess
from utility import generate_text
from preprocessing import preprocessing_instance
from generate_git_diff import generate_git_diff
import re
from PotterLogger import PotterLogger

from debug_client import DebugClient
import time
import sys
import docker
from typing import cast

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Test_execution'))
from Test_execution.tddbench.harness.extract_coverage import extract_coverage_feedback

from Test_execution.tddbench.harness.test_spec import make_test_spec
from Test_execution.tddbench.harness.test_spec_rebench import make_test_spec as make_test_spec_rebench
from Test_execution.tddbench.harness.utils import load_tddbench_dataset
from Test_execution.tddbench.harness.constants import SWEbenchInstance
from Test_execution.tddbench.harness.run_debugging import run_debugging


TOTAL_BUDGET = 10
REGRESSION_BUDGET = 5


def extract_breakpoint_locations(instance_id: str, json_path: str = "/Users/ishrakhayet/Documents/Work/Debugging/test-generation/focal_method_breakpoints.json"):
    """
    Extract breakpoint locations for a given instance_id from focal_method_breakpoints.json.
    
    Args:
        instance_id: The instance ID to look up (e.g., "django__django-12774")
        json_path: Path to the focal_method_breakpoints.json file
        
    Returns:
        Dictionary with:
        - 'breakpoint_locations': List of tuples (file_path, line_number) for setting breakpoints
        - 'function_info': Dictionary mapping (file_path, function_name) to function metadata
          including executable_body_lines for fallback when begin_line fails
    """
    breakpoint_locations = []
    function_info = {}
    
    try:
        # Read the JSON file
        with open(json_path, 'r') as f:
            data = json.load(f)
        
        # Find the instance
        instance_data = None
        for item in data:
            if item.get('instance_id') == instance_id:
                instance_data = item
                break
        
        if not instance_data:
            print(f"[extract_breakpoint_locations] Instance '{instance_id}' not found in {json_path}")
            return {'breakpoint_locations': breakpoint_locations, 'function_info': function_info}
        
        # Check if status is ok
        if instance_data.get('status') != 'ok':
            print(f"[extract_breakpoint_locations] Instance '{instance_id}' has status: {instance_data.get('status')}")
            return {'breakpoint_locations': breakpoint_locations, 'function_info': function_info}
        
        # Extract repo_root for constructing full paths
        repo_root = instance_data.get('repo_root', '')
        
        # Iterate through files
        files = instance_data.get('files', {})
        for file_path, file_info in files.items():
            if file_info.get('status') != 'ok':
                continue
            
            # Construct full file path (relative to /testbed in container)
            # The repo_root contains the local path, but in container it's /testbed
            full_file_path = f"/testbed/{file_path}"
            
            # Iterate through functions
            functions = file_info.get('functions', {})
            for func_name, func_info in functions.items():
                # Store function info for later use
                function_key = (full_file_path, func_name)
                function_info[function_key] = {
                    'begin_line': func_info.get('begin_line'),
                    'executable_body_lines': func_info.get('executable_body_lines', []),
                    'return_lines': func_info.get('return_lines', [])
                }
                
                # Add begin line as the first candidate
                begin_line = func_info.get('begin_line')
                if begin_line:
                    breakpoint_locations.append((full_file_path, begin_line, func_name, 'begin'))
                
                # Add all return lines
                return_lines = func_info.get('return_lines', [])
                for return_line in return_lines:
                    breakpoint_locations.append((full_file_path, return_line, func_name, 'return'))
        
        print(f"[extract_breakpoint_locations] Found {len(breakpoint_locations)} breakpoint locations for '{instance_id}':")
        for file_path, line_num, func_name, bp_type in breakpoint_locations:
            print(f"  - {file_path}:{line_num} ({func_name} - {bp_type})")
        
    except FileNotFoundError:
        print(f"[extract_breakpoint_locations] File not found: {json_path}")
    except json.JSONDecodeError as e:
        print(f"[extract_breakpoint_locations] JSON decode error: {e}")
    except Exception as e:
        print(f"[extract_breakpoint_locations] Unexpected error: {e}")
    
    return {'breakpoint_locations': breakpoint_locations, 'function_info': function_info}


def _expand_opaque_objects(dbgclient, output: str) -> str:
    """
    Parse output and replace opaque object representations with their __dict__.
    
    Args:
        dbgclient: DebugClient instance for executing commands
        output: String output from debugger command
        
    Returns:
        String with opaque objects replaced by their __dict__ representations
    """
    # Pattern to match variable names before opaque object representations
    # Matches both: 'query': <...object...> and query = <...object...>
    # Captures the variable name and the full opaque representation
    object_pattern = r"(?:^|['\"])(\w+)(?:['\"])?(?:\s*[:=]\s*)(<[^>]+\s+object\s+at\s+0x[0-9a-fA-F]+>)"
    
    matches = re.findall(object_pattern, output, re.MULTILINE)
    
    if not matches:
        return output
    
    modified_output = output
    
    for var_name, opaque_repr in matches:
        # Skip certain types that might not have useful __dict__ or cause issues
        skip_types = ['type', 'module', 'function', 'method', 'builtin_function_or_method']
        if any(skip_type in opaque_repr.lower() for skip_type in skip_types):
            continue
            
        try:
            # Execute command to get __dict__ of the object
            dict_cmd = f"p {var_name}.__dict__"
            print(f"[_expand_opaque_objects] Executing: {dict_cmd}")
            dict_output = dbgclient.exec(dict_cmd)
            
            # Check if we got a valid output (not an error)
            if dict_output and "AttributeError" not in dict_output and "NameError" not in dict_output:
                # Clean up the dict_output - remove the (Pdb) prompt if present
                dict_output_clean = dict_output.strip()
                if dict_output_clean.startswith("(Pdb)"):
                    dict_output_clean = dict_output_clean[5:].strip()
                
                # Replace the opaque representation with the __dict__ output
                modified_output = modified_output.replace(opaque_repr, dict_output_clean)
                print(f"[_expand_opaque_objects] Replaced {var_name} opaque representation with __dict__")
            else:
                print(f"[_expand_opaque_objects] Could not expand {var_name}: {dict_output}")
        except Exception as e:
            print(f"[_expand_opaque_objects] Error expanding {var_name}: {e}")
    
    return modified_output


def execute_debugger_commands(port: int, breakpoint_data: dict):
    """Execute debugger commands and collect debug info at each breakpoint.
    
    Args:
        port: Port number to connect to the debug server
        breakpoint_data: Dictionary containing 'breakpoint_locations' and 'function_info'
        
    Returns:
        List of dicts with breakpoint location and locals information
    """
    print("[execute_debugger_commands] Starting debugger integration...")
    debug_info_list = []
    
    # Wait a bit for the debug server to be fully ready
    print("[execute_debugger_commands] Waiting for debug server to be ready...")
    time.sleep(3)
    
    # Try to connect with retries
    max_retries = 5
    retry_delay = 2
    
    print(f"[execute_debugger_commands] Connecting to debug server on port {port}...")
    dbgclient = DebugClient(host='localhost', port=port)
    
    for attempt in range(1, max_retries + 1):
        print(f"[execute_debugger_commands] Connection attempt {attempt}/{max_retries}...")
        if dbgclient.connect():
            print("[execute_debugger_commands] Successfully connected to debug server")
            break
        
        if attempt < max_retries:
            print(f"[execute_debugger_commands] Connection failed, retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
        else:
            print("[execute_debugger_commands] Failed to connect to debug server after all retries")
            return debug_info_list
    
    try:
        breakpoint_locations = breakpoint_data.get('breakpoint_locations', [])
        function_info = breakpoint_data.get('function_info', {})
        
        print(f"[execute_debugger_commands] Setting {len(breakpoint_locations)} breakpoints...")
        successfully_set_breakpoints = []
        
        for file_path, line_number, func_name, bp_type in breakpoint_locations:
            actual_line = line_number
            # For begin lines, try with fallback to executable_body_lines
            if bp_type == 'begin':
                success = False
                function_key = (file_path, func_name)
                func_data = function_info.get(function_key, {})
                executable_lines = func_data.get('executable_body_lines', [])
                
                # Try the begin line first
                breakpoint_cmd = f"break {file_path}:{line_number}"
                print(f"[execute_debugger_commands] Executing: {breakpoint_cmd}")
                dbgoutput = dbgclient.exec(breakpoint_cmd)
                print(f"[execute_debugger_commands] Breakpoint output:\n{dbgoutput}")


                # Check if it's a blank or comment
                if "Blank or comment" in dbgoutput:
                    print(f"[execute_debugger_commands] Begin line {line_number} is blank/comment, trying executable_body_lines...")
                    # Try each executable line until we get a successful breakpoint
                    for exec_line in executable_lines:
                        if exec_line == line_number:
                            continue  # Skip the one we already tried
                        breakpoint_cmd = f"break {file_path}:{exec_line}"
                        print(f"[execute_debugger_commands] Trying: {breakpoint_cmd}")
                        dbgoutput = dbgclient.exec(breakpoint_cmd)
                        print(f"[execute_debugger_commands] Breakpoint output:\n{dbgoutput}")
                        
                        if "Blank or comment" not in dbgoutput and "Breakpoint" in dbgoutput:
                            print(f"[execute_debugger_commands] Successfully set breakpoint at line {exec_line}")
                            actual_line = exec_line
                            success = True
                            break
                    
                    if not success:
                        print(f"[execute_debugger_commands] Warning: Could not set breakpoint for {func_name} in {file_path}")
                        continue
                else:
                    success = True
                
                if success:
                    successfully_set_breakpoints.append((file_path, actual_line, func_name, bp_type))
            else:
                # For return lines, just set the breakpoint directly
                breakpoint_cmd = f"break {file_path}:{line_number}"
                print(f"[execute_debugger_commands] Executing: {breakpoint_cmd}")
                dbgoutput = dbgclient.exec(breakpoint_cmd)
                print(f"[execute_debugger_commands] Breakpoint output:\n{dbgoutput}")
                
                if "Breakpoint" in dbgoutput:
                    successfully_set_breakpoints.append((file_path, line_number, func_name, bp_type))
        
        # Hit each breakpoint and collect locals
        if successfully_set_breakpoints:
            print(f"[execute_debugger_commands] Collecting debug info at {len(successfully_set_breakpoints)} breakpoints...")
            
            for file_path, line_num, func_name, bp_type in successfully_set_breakpoints:
                # Continue to next breakpoint
                continue_output = dbgclient.exec("continue")
                print(f"[execute_debugger_commands] Continue output:\n{continue_output}")
                
                # # Get the current stack frame info
                where_output = dbgclient.exec("where")
                
                # Get function arguments (this works correctly)
                args_output = dbgclient.exec("a")
                
                # Expand opaque objects in args output
                args_output = _expand_opaque_objects(dbgclient, args_output)
                
                # Use inspect.currentframe() to get the current frame's locals
                # This should give us the frame where the breakpoint is hit
                locals_output = dbgclient.exec("p locals()")
                
                # Filter out pytest-related locals
                if "'__file__': '/opt/miniconda3/envs/testbed/bin/pytest'" in locals_output:
                    locals_output = ""
                else:
                    # Parse locals() output and expand opaque objects with __dict__ in-place
                    locals_output = _expand_opaque_objects(dbgclient, locals_output)
                
                # Combine all outputs for better context
                combined_output = f"Stack Trace:\n{where_output}\n\nArguments:\n{args_output}\n\nFrame Locals:\n{locals_output}"
                # print(f"[execute_debugger_commands] Locals at {file_path}:{line_num}:\n{combined_output}")
                
                # Store debug info
                debug_info_list.append({
                    'location': f"{file_path}:{line_num}",
                    'function': func_name,
                    'type': bp_type,
                    'locals': combined_output
                })
        
    finally:
        # Close the connection
        dbgclient.close()
        print("[execute_debugger_commands] Debugger integration completed.")
    
    return debug_info_list


def set_container_is_regression(container_name: str, is_regression: bool):
    """
    Set the IS_REGRESSION flag in the container and restart the debug server.
    
    Args:
        container_name: Name of the container
        is_regression: True for regression mode, False for reproduction mode
    """
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        
        # Set the is_regression flag
        value_str = "true" if is_regression else "false"
        cmd = f'bash -c "echo \\"{value_str}\\" > /tmp/is_regression.txt"'
        result = container.exec_run(cmd, user="root")
        
        if result.exit_code == 0:
            print(f"[set_container_is_regression] Set IS_REGRESSION={value_str} in container {container_name}")
            
            # Restart the debug server to pick up the new value
            # Kill existing debug server
            kill_cmd = "ps aux | grep '[d]ebug_server.py' | awk '{print $2}' | xargs -r kill -9 || true"
            container.exec_run(["/bin/bash", "-c", kill_cmd], user="root")
            print(f"[set_container_is_regression] Killed existing debug server")
            
            # Wait a moment for cleanup
            time.sleep(2)
            
            # Start new debug server
            start_cmd = [
                "/bin/bash",
                "-c",
                "nohup python3.10 /debug_server.py /testbed/pbt/test.py 9999 > /tmp/debug_server.log 2>&1 &",
            ]
            container.exec_run(start_cmd, detach=True, user="root")
            print(f"[set_container_is_regression] Started new debug server with IS_REGRESSION={value_str}")
            
            # Wait for server to start
            time.sleep(5)
            return True
        else:
            print(f"[set_container_is_regression] Failed to set IS_REGRESSION: {result.output.decode('utf-8')}")
            return False
    except Exception as e:
        print(f"[set_container_is_regression] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def get_debug_info(dataset_name: str, instance_id: str, predictions_path: str, run_id: str, host_port: int):
    """
    Get debug information by running debugging setup and collecting locals at breakpoints.
    
    Args:
        dataset_name: Path to the dataset JSON file
        instance_id: Instance ID to debug
        predictions_path: Path to predictions JSON file
        run_id: Run ID for the debugging session
        host_port: Host port for the debug server
        
    Returns:
        Formatted string with breakpoint locations and corresponding locals values
    """
    import sys
    import os
    
    print("[get_debug_info] Starting debug info collection...")
    
    # Step 1: Run the debugging setup
    print("[get_debug_info] Step 1: Running debugging setup...")
    run_debugging(
        dataset_name=dataset_name,
        instance_id=instance_id,
        test_patch_index=0,
        predictions_path=predictions_path,
        run_id=run_id,
    )
    
    # Step 2: Extract breakpoint locations
    print("[get_debug_info] Step 2: Extracting breakpoint locations...")
    breakpoint_data = extract_breakpoint_locations(instance_id)
    breakpoint_locations = breakpoint_data.get('breakpoint_locations', [])
    print(f"[get_debug_info] Extracted {len(breakpoint_locations)} breakpoint locations")
    
    if not breakpoint_locations:
        return "No breakpoint locations found for this instance."
    
    # Step 3: Execute debugger commands and collect info
    print("[get_debug_info] Step 3: Executing debugger commands...")
    debug_info_list = execute_debugger_commands(host_port, breakpoint_data)
    
    # Step 4: Format the output
    print("[get_debug_info] Step 4: Formatting debug info...")
    formatted_output = "=== DEBUG INFORMATION ===\n\n"
    
    for idx, debug_info in enumerate(debug_info_list, 1):
        formatted_output += f"Breakpoint {idx}: {debug_info['location']}\n"
        formatted_output += f"Function: {debug_info['function']} ({debug_info['type']})\n"
        formatted_output += f"Locals:\n{debug_info['locals']}\n"
        formatted_output += "-" * 80 + "\n\n"
    
    print("[get_debug_info] Debug info collection completed.")
    # print("\n" + formatted_output)
    
    return formatted_output


def get_host_port(dataset_name: str, instance_id: str, run_id: str = "epotter", is_rebench: bool = False):
    """
    Get the host port mapping for the debug server.
    
    Args:
        dataset_name: Path to the dataset JSON file
        instance_id: Instance ID
        run_id: Run ID for the container (default: "epotter")
        
    Returns:
        Host port number (int)
    """
    try:
        import sys
        import os
        import docker
        
        # Add Test_execution to path
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Test_execution'))
        from tddbench.harness.test_spec import make_test_spec
        from tddbench.harness.test_spec_rebench import make_test_spec as make_test_spec_rebench
        from tddbench.harness.utils import load_tddbench_dataset
        from tddbench.harness.constants import SWEbenchInstance
        from tddbench.harness.run_debugging import get_container_port_mapping
        from typing import cast
        
        print(f"[get_host_port] Looking up container port mapping for {instance_id}...")
        
        dataset = load_tddbench_dataset(dataset_name, instance_ids=[instance_id])
        instance_obj = cast(SWEbenchInstance, dataset[0])
        test_spec = (make_test_spec_rebench if is_rebench else make_test_spec)(instance_obj)
        container_name = test_spec.get_instance_container_name(run_id)
        
        client = docker.from_env()
        container = client.containers.get(container_name)
        host_port = get_container_port_mapping(container, container_port=9999)
        
        if host_port:
            print(f"[get_host_port] Found port mapping: {host_port} -> 9999")
            return host_port
        else:
            print("[get_host_port] No port mapping found, using default port 9999")
            return 9999
    except Exception as e:
        print(f"[get_host_port] Error: {e}, using default port 9999")
        return 9999


# Simple test for get_debug_info function
def test_get_debug_info():
    """Simple test for the get_debug_info function"""
    print("[test_get_debug_info] Starting test...")
    
    # Hard-coded test parameters
    dataset_name = "Test_execution/../TDD_Bench.json"
    instance_id = "django__django-12774"
    run_id = "test_debugging_django-12774"
    predictions_path = "/Users/ishrakhayet/Documents/Work/Debugging/test-generation/Test_execution/tddbench/harness/test_run_debugging_pred.json"
    
    print(f"[test_get_debug_info] instance_id={instance_id}")
    
    # Get host port using the get_host_port function
    host_port = get_host_port(dataset_name, instance_id, run_id)
    
    # Call get_debug_info
    print("\n[test_get_debug_info] Calling get_debug_info...")
    debug_output = get_debug_info(
        dataset_name=dataset_name,
        instance_id=instance_id,
        predictions_path=predictions_path,
        run_id=run_id,
        host_port=host_port
    )
    
    print("\n[test_get_debug_info] Test completed.")
    print("\n" + "="*80)
    print("FINAL DEBUG OUTPUT:")
    print("="*80)
    print(debug_output)


def run_final_cleanup(args, instance_id: str, test_file: str, attempt: int, plog: PotterLogger):
    """
    Re-runs run_evaluation for the last attempt as a safety net in case the
    subprocess during that iteration crashed before writing its report files.
    In the normal case (reports already exist) run_evaluation skips everything
    immediately, so this is a no-op.

    NOTE: This function is intentionally NOT called by default. Enable by
    uncommenting the call at the bottom of __main__ if the safety net is needed.
    """
    plog.start_attempt("final_cleanup")

    _cleanup_command = None
    _cleanup_stdout = ""
    _cleanup_stderr = ""
    _cleanup_returncode = None

    _cleanup_command = (
        "python -m tddbench.harness.run_evaluation"
        f" --dataset_name ../{args.dataset_name}"
        f" --test_patch ../{test_file}"
        f" --max_workers 1"
        f" --instance_ids {instance_id}"
        f" --run_id epotter"
        f" --keep_container true"
        f" --output_subdir run{attempt}"
        f" --pbt_mode {args.pbt_mode}"
        f" --is_rebench {str(args.is_rebench).lower()}"
    )
    result = subprocess.run(_cleanup_command, shell=True, capture_output=True, text=True, cwd="Test_execution")
    _cleanup_stdout = result.stdout
    _cleanup_stderr = result.stderr
    _cleanup_returncode = result.returncode

    plog.log_step(
        "test_execution",
        command=_cleanup_command,
        stdout=_cleanup_stdout,
        stderr=_cleanup_stderr,
        return_code=_cleanup_returncode,
    )


# # To run the test, uncomment the following lines:
# if __name__ == "__main__":
#     test_get_debug_info()


#python e-otter.py --dataset_name TDD_Bench.json --instance_id astropy__astropy-13579
if __name__ == "__main__":
    parser = ArgumentParser()

    parser.add_argument("--dataset_name", default="TDD_Bench.json", type=str, help="Path to json/csv file.")
    parser.add_argument("--initial_test", default="otter_test.json", type=str, help="Path to json/csv file.")
    parser.add_argument("--instance_id", type=str, help="Instance ID to run")
    parser.add_argument("--model", default="claude", type=str, help="model to generate test")
    parser.add_argument("--version", default="base", type=str, help="version name of the test")
    parser.add_argument("--pbt_mode", type=str, default="reproduction", choices=["combined", "regression", "reproduction"], help="Determines which files to write the test logs to and which logs to use for determining pass/fail.")
    parser.add_argument("--do_debug", action="store_true", help="Enable debug mode to collect runtime information at breakpoints")
    parser.add_argument("--cov_feedback", action="store_true", help="Enable coverage feedback")
    parser.add_argument("--filter_by", type=str, default="function", choices=["file", "function"], help="Determines whether coverage guidance should be from the entire focal file or only from the focal functions of the buggy repository.")
    parser.add_argument("--output_file", default="e_otter_test.json", type=str, help="Path to the output JSON file.")
    parser.add_argument("--is_rebench", action="store_true", help="Use [`test_spec_rebench.py`](test-generation/Test_execution/tddbench/harness/test_spec_rebench.py) instead of [`test_spec.py`](test-generation/Test_execution/tddbench/harness/test_spec.py).")

    args = parser.parse_args()

    # =====================================================
    # Dynamic prompts for regression/reproduction/combined
    # =====================================================
    if args.pbt_mode == "regression":
        from prompts_regression import PROMPT_DECISION as PROMPT_DECISION_REGRESSION, PROMPT_REWRITE
    elif args.pbt_mode == "reproduction":
        from prompts_reproduction import PROMPT_DECISION as PROMPT_DECISION_REPRODUCTION, PROMPT_REWRITE
    elif args.pbt_mode == "combined":
        from prompts_regression import PROMPT_DECISION as PROMPT_DECISION_REGRESSION
        from prompts_reproduction import PROMPT_DECISION as PROMPT_DECISION_REPRODUCTION
        from prompts_combined import PROMPT_REWRITE
    # =====================================================

    output_file = args.output_file

    instance_id = args.instance_id
    model=args.model
    version=args.version

    # Detect file format and load data accordingly
    file_extension = os.path.splitext(args.dataset_name)[1].lower()
    
    if file_extension == '.json':
        # Load JSON file
        with open(args.dataset_name, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Find the instance with matching instance_id
        instance = None
        for item in data:
            if item.get("instance_id") == instance_id:
                instance = item
                break
        
        if instance is None:
            raise ValueError(f"Instance ID '{instance_id}' not found in {args.dataset_name}")
    
    elif file_extension == '.csv':
        # Load CSV file
        df = pd.read_csv(args.dataset_name)
        instance = df[df["instance_id"] == instance_id]
        
        if instance.empty:
            raise ValueError(f"Instance ID '{instance_id}' not found in {args.dataset_name}")
        
        instance = instance.to_dict(orient="records")[0]
    
    else:
        raise ValueError(f"Unsupported file format: {file_extension}. Please use .csv or .json files.")

    # Extract issue description based on version
    issue_des=""
    if args.version == "base":
        issue_des = instance['problem_statement']

    output_dir="model_output"
    if os.path.exists(output_dir) == False:
        os.mkdir("model_output")
    
    
    # Copy the original file to temp_<instance_id>_0.json file
    shutil.copyfile(args.initial_test, f'temp_{instance_id}_0.json')

    # =====================================================================================================================================
    # Get container port mapping once before iterations (for debug mode)
    # =====================================================================================================================================
    host_port = None
    debug_info = ""
    current_mode = "regression"  # Track current mode (starts with regression for combined mode)

    # container_name is always derived — the container is always created by run_evaluation.py
    # regardless of --do_debug / --cov_feedback
    dataset = load_tddbench_dataset(args.dataset_name, instance_ids=[instance_id])
    instance_obj = cast(SWEbenchInstance, dataset[0])
    test_spec = (make_test_spec_rebench if args.is_rebench else make_test_spec)(instance_obj)
    container_name = test_spec.get_instance_container_name("epotter")

    if args.do_debug or args.cov_feedback:
        host_port = get_host_port(args.dataset_name, instance_id, run_id="epotter", is_rebench=args.is_rebench)
    # =====================================================================================================================================

    # Initialise the structured JSON logger for this instance
    plog = PotterLogger(instance_id)
    plog.set_container_name(container_name)

    success=False
    attempt=0

    buggy_collect=""
    retrieve_collect=""
    look_collect=""
    look_visited=[]

    test_file=""

    for attempt in range(TOTAL_BUDGET):
        try:
            test_file = f"temp_{instance_id}_" + str(attempt) + ".json"
            docker_option="true"

            plog.start_attempt(attempt)
            
            # Save current directory
            _run_command = None
            _run_stdout = ""
            _run_stderr = ""
            _run_returncode = None

            # run the test
            _run_command="python -m tddbench.harness.run_evaluation --dataset_name ../"+ args.dataset_name +" --test_patch ../"+ test_file + " --max_workers 1 --instance_ids " + instance_id + " --run_id epotter --keep_container " +docker_option+ " --output_subdir "+"run"+str(attempt) + " --pbt_mode " + args.pbt_mode + " --is_rebench " + str(args.is_rebench).lower()

            # Execute the command
            result = subprocess.run(_run_command, shell=True, capture_output=True, text=True, cwd="Test_execution")
            _run_stdout = result.stdout
            _run_stderr = result.stderr
            _run_returncode = result.returncode

            plog.log_step(
                "test_execution",
                command=_run_command,
                stdout=_run_stdout,
                stderr=_run_stderr,
                return_code=_run_returncode,
            )


            prompt=""


            # read logs from executing tests on the buggy versions
            # try:
            log=""
            cov_feedback=""

            if args.pbt_mode == "regression":
                prompt = PROMPT_DECISION_REGRESSION
                with open("Test_execution/logs/run_evaluation/epotter/run"+str(attempt)+"/gold/"+instance_id+"/regression__test_output_test_1_code_0.txt","r") as f_regression:
                    log=f_regression.read().split("+ : '>>>>> Start Test Output'")[1]
                    cov_feedback = extract_coverage_feedback(instance_id, attempt, container_name, run_id="epotter", branch_type="regression", filter_by=args.filter_by)

            elif args.pbt_mode == "reproduction":
                prompt = PROMPT_DECISION_REPRODUCTION
                with open("Test_execution/logs/run_evaluation/epotter/run"+str(attempt)+"/gold/"+instance_id+"/reproduction__test_output_test_1_code_0.txt","r") as f_reproduction:
                    log=f_reproduction.read().split("+ : '>>>>> Start Test Output'")[1]
                    cov_feedback = extract_coverage_feedback(instance_id, attempt, container_name, run_id="epotter", branch_type="reproduction", filter_by=args.filter_by)

            elif args.pbt_mode == "combined":
                prompt = PROMPT_DECISION_REGRESSION
                with open("Test_execution/logs/run_evaluation/epotter/run"+str(attempt)+"/gold/"+instance_id+"/regression__test_output_test_1_code_0.txt","r") as f_regression:
                    log=f_regression.read().split("+ : '>>>>> Start Test Output'")[1]
                    cov_feedback = extract_coverage_feedback(instance_id, attempt, container_name, run_id="epotter", branch_type="regression", filter_by=args.filter_by)

                    # If regression test passes, switch to repairing reproduction branch OR if regression budget is reached, deterministically switch to repairing reproduction branch
                    if bool(re.search(r"=+ 1 passed", log)) or attempt >= REGRESSION_BUDGET:
                        prompt = PROMPT_DECISION_REPRODUCTION
                        # print("\n<[< REGRESSION PASSED -- CHECKING REPRODUCTION LOGS >]>\n")
                        
                        # Switch from regression to reproduction mode
                        if args.do_debug and current_mode == "regression" and container_name:
                            # print("[e-otter] Switching from regression to reproduction mode...")
                            if set_container_is_regression(container_name, False):
                                current_mode = "reproduction"
                                # print("[e-otter] Successfully switched to reproduction mode")
                            else:
                                pass
                                # print("[e-otter] Warning: Failed to switch mode, continuing anyway")

                        # regression passed - check reproduction
                        with open("Test_execution/logs/run_evaluation/epotter/run"+str(attempt)+"/gold/"+instance_id+"/reproduction__test_output_test_1_code_0.txt","r") as f_reproduction:
                            log=f_reproduction.read().split("+ : '>>>>> Start Test Output'")[1]
                            cov_feedback = extract_coverage_feedback(instance_id, attempt, container_name, run_id="epotter", branch_type="reproduction", filter_by=args.filter_by)

            else:
                raise ValueError("Invalid pbt_mode")
                    
            # except Exception as e:
            #     print(f"Error reading log: {e}", flush=True)
            #     log="Error reading log"


            with open(test_file, "r") as f:
                test_candidates=json.load(f)


            diff=""
            for item in test_candidates:
                if item['instance_id']==instance_id:
                    diff=item['model_patch']
                    break


            prompt=prompt.replace("<ISSUE DESCRIPTION>", issue_des)
            prompt=prompt.replace("<LOGBEFORE>",log)
            prompt=prompt.replace("<TEST>",diff)

            # ==============================================================================================================
            # FIXED DEBUG INFO
            # ==============================================================================================================
            if args.do_debug:
                # print(f"[e-otter] Collecting debug info for iteration {attempt}...")
                # Get the current predictions path (the temp file being used in this iteration)
                current_predictions_path = os.path.abspath(test_file)
                run_id = "epotter"
                
                try:
                    debug_info = get_debug_info(
                        dataset_name=args.dataset_name,
                        instance_id=instance_id,
                        predictions_path=current_predictions_path,
                        run_id=run_id,
                        host_port=host_port
                    )
                    prompt = prompt.replace("<DEBUGINFO>", f"\nHere are some relevant debug information to help with the property-based test generation:\n{debug_info}\n")

                except Exception as e:
                    # print(f"[e-otter] Error collecting debug info: {e}")
                    debug_info = "Debug info collection failed."
            else:
                prompt = prompt.replace("<DEBUGINFO>", "")
                
            # ==============================================================================================================

            # print(f"\n\n===========================\nPROMPT_DECISION:\n===========================\n{prompt}")

            generated_patch=generate_text(prompt, model=model)

            # print(generated_patch)

            decision=re.findall(r"<DECISION>(.*?)</DECISION>",generated_patch, re.DOTALL)
            explain=re.findall(r"<Explain>(.*?)</Explain>",generated_patch, re.DOTALL)
            buggy=re.findall(r"<Buggy>(.*?)</Buggy>",generated_patch, re.DOTALL)
            retrieve=re.findall(r"<Retrieve>(.*?)</Retrieve>",generated_patch, re.DOTALL)
            looks=re.findall(r"<Look>(.*?)</Look>",generated_patch, re.DOTALL)

            exp=""
            if len(explain)>0:
                exp=explain[0]

        
            bug=""
            if len(buggy)>0:
                bug=buggy[0]


            retrieve_text=""
            if len(retrieve)>0:
                retrieve_text=retrieve[0]

            # Log the critic phase
            plog.log_step(
                "critic",
                prompt=prompt,
                coverage_feedback=cov_feedback,
                debug_info=debug_info,
                llm_output=generated_patch,
                decision=decision[0].strip() if decision else "",
                explain=exp,
                buggy=bug,
                retrieve=retrieve_text,
                look=looks,
            )


            buggy_collect=buggy_collect+bug+"\n\n...............................\n\n"
            retrieve_collect=retrieve_collect+retrieve_text+"\n\n...............................\n\n"  


            if len(looks)>0:

                if os.path.exists("preprocessed_data") == False:
                    os.mkdir("preprocessed_data")
                
                if os.path.exists("preprocessed_data/" + instance_id) == False:
                    os.mkdir("preprocessed_data/" + instance_id)
                    # print("preprocessing the data")
                    preprocessing_instance(instance)
                # else:
                #     print("Data is already preprocessed ...")

                with open("preprocessed_data/"+instance_id+"/"+"method_bodies.json","r") as f:
                    function_body_preprocess=json.load(f)   

                for lfunc in looks:

                    if lfunc.find("(")!=-1:
                        lfunc=lfunc.split("(")[0]

                    if lfunc.find(".")!=-1:
                        lfunc=lfunc.split(".")[1]


                    if len(look_visited)<10:
                        for key1 in function_body_preprocess:
                            for key2 in function_body_preprocess[key1]:
                                if key2==lfunc:
                                    look_visited.append(key2)
                                    look_collect=look_collect+"File : "+key1+"\n\n"+function_body_preprocess[key1.strip()][key2.strip()]+"\n\n"


            if decision[0].strip().lower()=="yes":

                success=True

                #extract diff from curent temp file
                with open(test_file, "r") as file:
                    test_candidates=json.load(file)


                diff=""
                for item in test_candidates:
                    if item['instance_id']==instance_id:
                        diff=item['model_patch']
                        break

                if os.path.isfile(output_file):
                    with open(output_file, 'r') as file:
                        data = json.load(file)

                    temp_dict={}
                    temp_dict["instance_id"]=instance_id
                    temp_dict["instance_id"]=instance_id
                    temp_dict['decision']="Yes"
                    temp_dict['model_patch']=diff
                    temp_dict["attempt"]=attempt

                    data.append(temp_dict)    

                else:

                    data=[]
                    temp_dict={}
                    temp_dict["instance_id"]=instance_id
                    temp_dict["instance_id"]=instance_id
                    temp_dict['decision']="Yes"
                    temp_dict['model_patch']=diff
                    temp_dict["attempt"]=attempt

                    data.append(temp_dict)    

                with open(output_file, 'w') as file:
                    json.dump(data, file, indent=4)   

                break        
            

            prompt=PROMPT_REWRITE

            if look_collect.strip()=="":
                look_collect="No need to see any function."

            if buggy_collect.strip()=="":
                buggy_collect="No buggy lines yet."

            if retrieve_collect.strip()=="":
                retrieve_collect="No line retrieved yet."


            prompt=prompt.replace("<ISSUE DESCRIPTION>",instance["problem_statement"])
            prompt=prompt.replace("<LOGBEFORE>",log)
            prompt=prompt.replace("<BUGGY>",buggy_collect)
            prompt=prompt.replace("<LOOK>",look_collect)
            prompt=prompt.replace("<RETR>", retrieve_collect)
            prompt=prompt.replace("<TEST>",diff)

            # ==============================================================================================================
            # FOCAL COVERAGE FEEDBACK
            # ==============================================================================================================
            # print("COV FEEDBACK:\n")
            # print(cov_feedback)
            # print("\n")
            # Keep the raw value for logging before injecting the formatted block into the prompt
            _raw_cov_feedback = cov_feedback
            if len(cov_feedback) > 0 and args.cov_feedback:
                cov_feedback = "\n<COVERAGE_FEEDBACK>\nSome lines and edges from the focal files were not covered by the generated property-based test. " \
                                    + "From the list given below, identify the missed lines and edges that are the most relevant to the issue. " \
                                    + "Your next version of the property-based test MUST cover these missed relevant lines and edges.\n\n" \
                                    + cov_feedback \
                                    + "\n</COVERAGE_FEEDBACK>\n"

                prompt=prompt.replace("<COVERAGE FEEDBACK>", cov_feedback)
            else:
                prompt=prompt.replace("<COVERAGE FEEDBACK>","")

            # ==============================================================================================================
            # FIXED DEBUG INFO
            # ==============================================================================================================
            if args.do_debug:
                prompt = prompt.replace("<DEBUGINFO>", f"\nHere are some relevant debug information to help with the property-based test generation:\n{debug_info}\n")
            else:
                prompt = prompt.replace("<DEBUGINFO>", "")
                
            # ==============================================================================================================

            # print(prompt)

            generated_patch=generate_text(prompt, model=model)

            # print(generated_patch)

            test=re.findall(r"<COMPLETE_TEST>(.*?)</COMPLETE_TEST>", generated_patch, flags=re.DOTALL)[0].strip()
            classname=re.findall(r"<FILE>(.*?)</FILE>", generated_patch, flags=re.DOTALL)[0].strip()

            # Log the refinement phase
            plog.log_step(
                "refinement",
                prompt=prompt,
                coverage_feedback=_raw_cov_feedback,
                debug_info=debug_info,
                llm_output=generated_patch,
                generated_test=test,
                test_file=classname,
            )

            temp1={}
            temp1['function']=test+"\n"
            temp1['act']="NewFile"
            temp1['classname']=classname


            if os.path.exists(output_dir+"/"+"version_5_"+model+"_"+version)==False:
                os.mkdir(output_dir+"/"+"version_5_"+model+"_"+version)

            with open(output_dir+"/"+"version_5_"+model+"_"+version+"/"+instance_id+"_temp.json", "w") as file:
                json.dump(temp1, file, indent=4) 

            test_diff = generate_git_diff(instance, args.model, args.version, "model_output", True)    

            temp_output_file=f"temp_{instance_id}_" + str(attempt+1) + ".json"

            # Always write fresh — never append to a stale file from a prior run
            entry = {
                "instance_id": instance_id,
                "model_patch": test_diff
            }

            with open(temp_output_file, 'w', encoding='utf-8') as f:
                json.dump([entry], f, indent=4)
        
            # print(f"Git diff saved to {temp_output_file}")

        except Exception as e:
            # print(f"[e-otter] Error in iteration {attempt}: {e}")
            import traceback
            traceback.print_exc()
            # print(f"[e-otter] Continuing to next iteration...")
            continue


    if success==False:
        #extract diff from curent temp file
        with open(args.initial_test, "r") as f:
            test_candidates=json.load(f)


        diff=""
        for item in test_candidates:
            if item['instance_id']==instance_id:
                diff=item['model_patch']
                break

        if os.path.isfile(output_file):
            with open(output_file, 'r') as file:
                data = json.load(file)

            temp_dict={}
            temp_dict["instance_id"]=instance_id
            temp_dict["instance_id"]=instance_id
            temp_dict['decision']="No"
            temp_dict['model_patch']=diff
            temp_dict["attempt"]=attempt

            data.append(temp_dict)    

        else:

            data=[]
            temp_dict={}
            temp_dict["instance_id"]=instance_id
            temp_dict["instance_id"]=instance_id
            temp_dict['decision']="No"
            temp_dict['model_patch']=diff
            temp_dict["attempt"]=attempt

            data.append(temp_dict)    

        with open(output_file, 'w') as file:
            json.dump(data, file, indent=4)  





    # run_final_cleanup(args, instance_id, test_file, attempt, plog)
