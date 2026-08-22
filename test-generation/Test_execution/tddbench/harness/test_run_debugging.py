from __future__ import annotations

import sys
import os
import time
import docker

from tddbench.harness.run_debugging import run_debugging, get_container_port_mapping
from tddbench.harness.test_spec import make_test_spec
from tddbench.harness.utils import load_tddbench_dataset
from tddbench.harness.constants import SWEbenchInstance
from typing import cast, Optional

# Add parent directory to path to import debug_client
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from debug_client import DebugClient
import json


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


def execute_debugger_commands(port: int = 9999, breakpoint_data: Optional[dict] = None):
    """Execute debugger commands and collect output.
    
    Args:
        port: Port number to connect to the debug server
        breakpoint_data: Dictionary containing 'breakpoint_locations' and 'function_info'
    """
    print("[test_run_debugging] Starting debugger integration...")
    
    # Wait a bit for the debug server to be fully ready
    print("[test_run_debugging] Waiting for debug server to be ready...")
    time.sleep(3)
    
    # Try to connect with retries
    max_retries = 5
    retry_delay = 2
    
    print(f"[test_run_debugging] Connecting to debug server on port {port}...")
    dbgclient = DebugClient(host='localhost', port=port)
    
    for attempt in range(1, max_retries + 1):
        print(f"[test_run_debugging] Connection attempt {attempt}/{max_retries}...")
        if dbgclient.connect():
            print("[test_run_debugging] Successfully connected to debug server")
            break
        
        if attempt < max_retries:
            print(f"[test_run_debugging] Connection failed, retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)
        else:
            print("[test_run_debugging] Failed to connect to debug server after all retries")
            print("[test_run_debugging] Make sure:")
            print("[test_run_debugging]   1. The container is running")
            print("[test_run_debugging]   2. Port 9999 is exposed and mapped")
            print("[test_run_debugging]   3. The debug server is running inside the container")
            return
    
    try:
        # First, check the current session state
        print("[test_run_debugging] Checking debugger session state...")
        
        # Set breakpoints from the provided locations
        if breakpoint_data:
            breakpoint_locations = breakpoint_data.get('breakpoint_locations', [])
            function_info = breakpoint_data.get('function_info', {})
            
            print(f"[test_run_debugging] Setting {len(breakpoint_locations)} breakpoints...")
            for file_path, line_number, func_name, bp_type in breakpoint_locations:
                # For begin lines, try with fallback to executable_body_lines
                if bp_type == 'begin':
                    success = False
                    function_key = (file_path, func_name)
                    func_data = function_info.get(function_key, {})
                    executable_lines = func_data.get('executable_body_lines', [])
                    
                    # Try the begin line first
                    breakpoint_cmd = f"break {file_path}:{line_number}"
                    print(f"[test_run_debugging] Executing: {breakpoint_cmd}")
                    dbgoutput = dbgclient.exec(breakpoint_cmd)
                    print(f"[test_run_debugging] Breakpoint output:\n{dbgoutput}")
                    
                    # Check if it's a blank or comment
                    if "Blank or comment" in dbgoutput:
                        print(f"[test_run_debugging] Begin line {line_number} is blank/comment, trying executable_body_lines...")
                        # Try each executable line until we get a successful breakpoint
                        for exec_line in executable_lines:
                            if exec_line == line_number:
                                continue  # Skip the one we already tried
                            breakpoint_cmd = f"break {file_path}:{exec_line}"
                            print(f"[test_run_debugging] Trying: {breakpoint_cmd}")
                            dbgoutput = dbgclient.exec(breakpoint_cmd)
                            print(f"[test_run_debugging] Breakpoint output:\n{dbgoutput}")
                            
                            if "Blank or comment" not in dbgoutput and "Breakpoint" in dbgoutput:
                                print(f"[test_run_debugging] Successfully set breakpoint at line {exec_line}")
                                success = True
                                break
                        
                        if not success:
                            print(f"[test_run_debugging] Warning: Could not set breakpoint for {func_name} in {file_path}")
                else:
                    # For return lines, just set the breakpoint directly
                    breakpoint_cmd = f"break {file_path}:{line_number}"
                    print(f"[test_run_debugging] Executing: {breakpoint_cmd}")
                    dbgoutput = dbgclient.exec(breakpoint_cmd)
                    print(f"[test_run_debugging] Breakpoint output:\n{dbgoutput}")
        else:
            # Fallback to old hardcoded breakpoint for testing
            print("[test_run_debugging] No breakpoint locations provided, using default...")
            dbgoutput = dbgclient.exec("break /testbed/django/db/models/query.py:690")
            print(f"[test_run_debugging] Break command output:\n{dbgoutput}")
        
        # Continue execution
        dbgoutput2 = dbgclient.exec("continue")
        print(f"[test_run_debugging] Continue command output:\n{dbgoutput2}")
        
        # Print locals at the breakpoint
        dbgoutput3 = dbgclient.exec("p locals()")
        print(f"[test_run_debugging] Locals command output:\n{dbgoutput3}")
        
    finally:
        # Close the connection
        dbgclient.close()
        print("[test_run_debugging] Debugger integration completed.")


def main():
    print("[test_run_debugging] Starting smoke test...")
    
    # Hard-coded arguments
    dataset_name = "../TDD_Bench.json"
    instance_id = "django__django-12774"
    run_id = "test_debugging_django-12774"
    predictions_path = "/Users/ishrakhayet/Documents/Work/Debugging/test-generation/Test_execution/tddbench/harness/test_run_debugging_pred.json"
    
    print(f"[test_run_debugging] dataset_name={dataset_name}")
    print(f"[test_run_debugging] instance_id={instance_id}")
    print(f"[test_run_debugging] run_id={run_id}")
    
    # Test the extract_breakpoint_locations function
    print("\n[test_run_debugging] Testing extract_breakpoint_locations function...")
    breakpoint_data = extract_breakpoint_locations(instance_id)
    breakpoint_locations = breakpoint_data.get('breakpoint_locations', [])
    print(f"[test_run_debugging] Extracted {len(breakpoint_locations)} breakpoint locations")

    # Run the debugging setup
    run_debugging(
        dataset_name=dataset_name,
        instance_id=instance_id,
        test_patch_index=0,
        predictions_path=predictions_path,
        run_id=run_id,
    )
    
    # Look up the container's port mapping directly
    print("[test_run_debugging] Looking up container port mapping...")
    try:
        # Load dataset and create test spec to get container name
        dataset = load_tddbench_dataset(dataset_name, instance_ids=[instance_id])
        instance = cast(SWEbenchInstance, dataset[0])
        test_spec = make_test_spec(instance)
        container_name = test_spec.get_instance_container_name(run_id)
        
        # Get the container and query its port mapping
        client = docker.from_env()
        container = client.containers.get(container_name)
        host_port = get_container_port_mapping(container, container_port=9999)
        
        if host_port:
            print(f"[test_run_debugging] Found port mapping: {host_port} -> 9999")
        else:
            print("[test_run_debugging] No port mapping found, using default port 9999")
            host_port = 9999
    except Exception as e:
        print(f"[test_run_debugging] Error looking up port mapping: {e}, using default port 9999")
        host_port = 9999

    # Execute debugger commands with extracted breakpoint data
    execute_debugger_commands(port=host_port, breakpoint_data=breakpoint_data)

    print("[test_run_debugging] Smoke test completed.")


if __name__ == "__main__":
    main()

# Made with Bob
