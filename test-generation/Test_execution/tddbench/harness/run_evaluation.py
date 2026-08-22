from __future__ import annotations

import docker
import json
import resource
import traceback
import glob
import os

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm
import re
import ast
from cldk.analysis.commons.treesitter import TreesitterPython


cldk_python = TreesitterPython()

DIFF_MODIFIED_FILE_REGEX = r"--- a/(.*)"

from tddbench.harness.constants import (
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS,
    INSTANCE_IMAGE_BUILD_DIR,
    KEY_INSTANCE_ID,
    MAP_REPO_VERSION_TO_SPECS,
    RUN_EVALUATION_LOG_DIR,
)
from tddbench.harness.docker_utils import (
    remove_image,
    copy_to_container,
    copy_from_container,
    exec_run_with_timeout,
    cleanup_container,
    list_images,
    should_remove,
    clean_images,
)
from tddbench.harness.docker_build import (
    BuildImageError,
    build_container,
    build_env_images,
    close_logger,
    setup_logger,
)
from tddbench.harness.grading import get_eval_report, get_logs_eval
from tddbench.harness.test_spec import (
    make_eval_script_list,
    make_test_spec,
    resolve_specs,
)
from tddbench.harness.test_spec_rebench import (
    make_eval_script_list as make_eval_script_list_rebench,
    make_test_spec as make_test_spec_rebench,
    resolve_specs as resolve_specs_rebench,
)
from tddbench.harness.utils import load_tddbench_dataset, str2bool


def calculate_coverage(filename, coverage, start_code_list, is_before):
    filename = "/testbed" + filename

    # if filename.startswith("/"):
    #     filename = filename[1:]
    coverage = coverage.split("\n")

    # Determine the field index for "Missing" column by checking the header
    # Header format with branch: "Name Stmts Miss Branch BrPart Cover Missing"
    # Header format without branch: "Name Stmts Miss Cover Missing"
    missing_field_index = 6  # Default for branch coverage
    for line in coverage:
        if line.strip().startswith("Name"):
            # Normalize spaces in header
            header = line.strip()
            while header.find("  ") != -1:
                header = header.replace("  ", " ")
            header_fields = header.split(" ")
            # Count fields: with branch=7 fields, without branch=5 fields
            if len(header_fields) == 5:
                missing_field_index = 4  # Without branch coverage
            elif len(header_fields) == 7:
                missing_field_index = 6  # With branch coverage
            break

    # print("=" * 30)
    # print("Coverage Filename: ",filename)
    # print("=" * 30)


    #find the relevant line from the coverage file
    target_line = ""
    for line in coverage:
        if line.startswith(filename):
            target_line = line.strip()
            break
    while target_line.find("  ") != -1:
        target_line = target_line.replace("  "," ")

    missinglines = target_line.split(" ")[missing_field_index:]
    
    # print("=" * 30)   
    # print("Coverage Missing Lines: ", missinglines)
    # print("=" * 30)

    #making list of missingline and missed_edges:
    missing_lines = []
    missed_edges = []
    
    for item in missinglines:
        item = item.replace(",","")

        # Handle edges (branches) separately - format: "87->88" or "38->exit"
        if item.find("->") != -1:
            if item.find("exit") == -1:
                # Regular edge like "87->88"
                parts = item.split('->')
                from_line = int(parts[0])
                to_line = int(parts[1])
                missed_edges.append((from_line, to_line))
            # Skip edges to exit (like "38->exit")
            continue
        
        # Handle line ranges like "252-257"
        if item.find("-")!=-1:
            parts = item.split('-')
            for i in range(int(parts[0]), int(parts[1]) + 1):
                missing_lines.append(i)
        else:
            # Single line number
            missing_lines.append(int(item))


    #making list of changed line from patch
    changed_line=[]
    for i in range(len(start_code_list)): 
        start_code=start_code_list[i]
        start=start_code[0]
        code=start_code[1]
        code=code.split("\n")

        if is_before:
            j=0
            while j <len(code):
                if code[j].strip().startswith("+"):
                    del code[j]
                    continue
                j=j+1    
        else:
            j=0
            while j <len(code):   
                if code[j].strip().startswith("-"):
                    del code[j]
                    continue
                j=j+1 


        for j in range(0,len(code)):
            if is_before:
                if code[j].strip().startswith("---"):
                    continue
                if code[j].strip().startswith("-"):
                    temp=code[j].replace("-","")
                    if temp.strip()=="":
                        continue
                    if temp.strip().startswith("#"):
                        continue
                    changed_line.append(j+start)  
            else:
                if code[j].strip().startswith("+++"):
                    continue                
                if code[j].strip().startswith("+"):
                    temp=code[j].replace("+","")
                    if temp.strip()=="":
                        continue
                    if temp.strip().startswith("#"):
                        continue
                    changed_line.append(j+start)  

    # Filter to only include missed lines that are in changed_line
    missed_line = []
    for item in changed_line:
        if item in missing_lines:
            missed_line.append(item)
    
    # Filter missed_edges to include edges where either from_line or to_line is in changed_line
    filtered_missed_edges = []
    for from_line, to_line in missed_edges:
        if from_line in changed_line or to_line in changed_line:
            filtered_missed_edges.append((from_line, to_line))

    return changed_line, missed_line, filtered_missed_edges


def compute_patch_coverage(patch_text, coverage_report, is_before):
    """
    Compute coverage metrics for a code patch.
    
    Args:
        patch_text (str): The code patch in unified diff format
        coverage_report (str): The coverage report output
        is_before (bool): If True, compute for removed lines (baseline).
                         If False, compute for added lines (F2P).
    
    Returns:
        tuple: (total_change, total_miss)
            - total_change (int): Total number of changed lines
            - total_miss (int): Number of changed lines not covered by tests
    """
    if not patch_text or not coverage_report:
        return 0, 0
    
    patch_text_segments = patch_text.split("+++ b")
    total_change = 0
    total_miss = 0
    cov_map = {}
    
    for j in range(1, len(patch_text_segments)):
        focus_text = patch_text_segments[j]
        filename = patch_text_segments[j].split("\n")[0].strip()
        segment_count = int(len(focus_text.split("@@")) / 2)
        start_code_list = []
        
        for i in range(0, segment_count):
            # Parse line numbers from hunk header
            # Format: @@ -start,count +start,count @@
            # Index 0 is for "before" (removed lines), index 1 is for "after" (added lines)
            line_index = 0 if is_before else 1
            lines = focus_text.split("@@")[2*i+1].strip().split(" ")[line_index]
            start = abs(int((lines.split(",")[0]))) - 1
            
            code = focus_text.split("@@")[2*i+2]
            start_code = (start, code)
            start_code_list.append(start_code)
        
        # count_change, count_miss = calculate_coverage(filename, coverage_report, start_code_list, is_before)
        # total_change += count_change
        # total_miss += count_miss
    
    # return total_change, total_miss

        changed_line, missed_line, missed_edges = calculate_coverage(filename, coverage_report, start_code_list, is_before)
        cov_map[filename] = {
            'changed_line': changed_line,
            'missed_line': missed_line,
            'missed_edges': missed_edges
        }
    
    return cov_map


def get_class_functions(text):    
    classes = cldk_python.get_all_classes(module=text)
    class_and_method_names = [[klazz.class_name+'::'+method.method_name for method in klazz.methods] for klazz in classes]
    functions={}
    for sub_array in class_and_method_names:
        for element in sub_array:
            functions[element.split("::")[1]]=element.split("::")[0]
    return functions


def get_outer_functions(text):
    """
    Extracts the names of outer functions containing 'test' in their name.

    Args:
        text (str): The text to parse.

    Returns:
        list: A list of function names.
    """
    tree = ast.parse(text)

    functions = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node,ast.AsyncFunctionDef):
            if node.name.find("test") == -1:
                continue
            functions.append(node.name)

    return functions


def get_contributing_functions(test_patch):
    """
    Extracts the test functions that have been added or modified from the given test patch.

    Args:
        test_patch (str): The test patch to analyze.

    Returns:
        dict: A dictionary mapping file names to lists of modified test functions.
    """
    list_functions={}
    test_patch_segments=test_patch.split("+++ b")           

    for j in range(1,len(test_patch_segments)):
        focus_text=test_patch_segments[j]
        filename=test_patch_segments[j].split("\n")[0].strip()

        if filename.startswith("/"):
            filename=filename[1:]

        segments=focus_text.split("def test")[1:]
        for i in range(len(segments)):
            fbody=segments[i]
            fname="test"+segments[i].split("(")[0].strip()

            flines=fbody.split("\n")
            for ln in flines:
                if ln.strip().startswith("+"):
                    ln=ln.replace("+","")
                    ln=ln.replace("-","")
                    if ln.strip()=="":
                        continue
                    
                    if filename in list_functions:  
                        if fname not in list_functions[filename]:   
                            list_functions[filename].append(fname)
                            break
                    else:
                        list_functions[filename]=[]
                        list_functions[filename].append(fname)
                        break

    return list_functions 


def modify_eval(text,instance_id,fun2test):
    lines=text.split("\n")
    text=""
    for ln in lines:
        if (ln.find("coverage run")!=-1 or ln.find("tox --current-env -epy39 -v --")!=-1 or ln.find("./bin/test -C")!=-1) and len(fun2test)>0:
            segments=ln.split(" ")
            fcount=0
            for i in range(len(segments)-1,0,-1):
                fname=segments[i]
                if fname.find(".py")!=-1 or fname.find(".")!=-1: # . is for django
                    fcount=fcount+1 
                else:
                    break       
            remain=ln.split(" ")[0:len(segments)-fcount]
            command=""

            for item in remain:
                command=command+item+" "

            testcases=""

            if instance_id.find("django")!=-1:
                for item in fun2test:
                    if item.startswith("tests/"):
                        item=item[6:]
                    item=item.replace(".py","")
                    item=item.replace("::",".")
                    item=item.replace("/",".")
                    testcases=testcases+item+" "
        
                text=text+command+testcases.strip()+"\n" 
                
            elif instance_id.find("sympy")!=-1:
                funcnames={}
                for item in fun2test:
                    value=item.split(".py::")[0]+".py" 
                    
                    temp=item.split(".py::")[1]
                    if temp.find("::")!=-1:
                        key=temp.split("::")[-1].strip()
                    else:
                        key=temp

                    funcnames[key]=value
            
                for key in funcnames:
                    text=text+command.split(" --verbose ")[0]+" --verbose "+"-k "+"'"+key+"' "+command.split(" --verbose ")[1]+" "+funcnames[key]+"\n"   
            else:    
                for item in fun2test:
                    testcases=testcases+item+" "
                text=text+command+testcases.strip()+"\n" 
            
        else:
            text=text+ln+"\n"
    return text


class EvaluationError(Exception):
    def __init__(self, instance_id, message, logger):
        super().__init__(message)
        self.super_str = super().__str__()
        self.instance_id = instance_id
        self.log_file = logger.log_file
        self.logger = logger

    def __str__(self):
        return (
            f"Evaluation error for {self.instance_id}: {self.super_str}\n"
            f"Check ({self.log_file}) for more information."
        )


def log_baseline(instance_id, baseline_pair_prefix, baseline_test_output, baseline_timed_out, timeout, pair_log_dir, baseline_pair_suffix,
                 fun2test, test_version, baseline_code_version, test_patch_value, code_patch_value, baseline_report_path,
                 log_parser_name: str | None = None):
    
    baseline_combined_output_path = pair_log_dir / f"{baseline_pair_prefix}__test_coverage_combine{baseline_pair_suffix}.txt"
    with open(baseline_combined_output_path, "w") as f:
        f.write(baseline_test_output)
        if baseline_timed_out:
            f.write(f"\n\nTimeout error: {timeout} seconds exceeded.")
            print(f"[run_instance] baseline pair {baseline_pair_suffix} timed out after {timeout} seconds; recording and continuing")

    with open(baseline_combined_output_path, "r") as f:
        baseline_test_coverage = f.read().split("+ coverage report")
        baseline_test_after = baseline_test_coverage[0]
        baseline_coverage_after = baseline_test_coverage[-1] if len(baseline_test_coverage) > 1 else ""

    baseline_test_output_path = pair_log_dir / f"{baseline_pair_prefix}__test_output{baseline_pair_suffix}.txt"
    baseline_coverage_output_path = pair_log_dir / f"{baseline_pair_prefix}__test_coverage{baseline_pair_suffix}.txt"
    with open(baseline_test_output_path, "w") as f:
        f.write(baseline_test_after)
    with open(baseline_coverage_output_path, "w") as f:
        f.write(baseline_coverage_after)

    baseline_test_result, _ = get_logs_eval(str(baseline_test_output_path), log_parser_name)
    # cov_change_before, cov_miss_before = compute_patch_coverage(code_patch_value, baseline_coverage_after, is_before=True)
    cov_map = compute_patch_coverage(code_patch_value, baseline_coverage_after, is_before=True)
    print(f"[run_instance] completed baseline pair {baseline_pair_suffix}")

    baseline_report = {
        instance_id: {
            "contributing_functions": fun2test,
            "test_after_patch": baseline_test_result,
            "test_version": test_version,
            "code_patch_version": baseline_code_version,
            "test_patch": test_patch_value,
            "code_patch": "",  # Empty code patch
            "cov_map": cov_map,
        }
    }

    baseline_report_path = pair_log_dir / f"{baseline_pair_prefix}__report{baseline_pair_suffix}.json"
    with open(baseline_report_path, "w") as f:
        f.write(json.dumps(baseline_report, indent=4))
    print(f"[run_instance] wrote baseline report {baseline_report_path}")


def log_f2p(instance_id, pair_prefix, test_output, timed_out, timeout, pair_log_dir, pair_suffix,
            fun2test, test_version, code_version, test_patch_value, code_patch_value, report_path, baseline_pair_suffix,
            log_parser_name: str | None = None):
    combined_output_path = pair_log_dir / f"{pair_prefix}__test_coverage_combine{pair_suffix}.txt"
    with open(combined_output_path, "w") as f:
        f.write(test_output)
        if timed_out:
            f.write(f"\n\nTimeout error: {timeout} seconds exceeded.")
            print(f"[run_instance] pair {pair_suffix} timed out after {timeout} seconds; recording and continuing")

    with open(combined_output_path, "r") as f:
        test_coverage = f.read().split("+ coverage report")
        test_after = test_coverage[0]
        coverage_after = test_coverage[-1] if len(test_coverage) > 1 else ""

    test_output_path = pair_log_dir / f"{pair_prefix}__test_output{pair_suffix}.txt"
    coverage_output_path = pair_log_dir / f"{pair_prefix}__test_coverage{pair_suffix}.txt"
    with open(test_output_path, "w") as f:
        f.write(test_after)
    with open(coverage_output_path, "w") as f:
        f.write(coverage_after)

    test_result, _ = get_logs_eval(str(test_output_path), log_parser_name)
    # cov_change_after, cov_miss_after = compute_patch_coverage(code_patch_value, coverage_after, is_before=False)
    cov_map = compute_patch_coverage(code_patch_value, coverage_after, is_before=False)
    print(f"[run_instance] completed pair {pair_suffix}")

    # Determine if the code patch resolved the issue
    resolved = False
    baseline_report_path_check = pair_log_dir / f"report{baseline_pair_suffix}.json"
    if baseline_report_path_check.exists():
        try:
            with open(baseline_report_path_check, "r") as f:
                baseline_data = json.load(f)
                test_result_before = baseline_data.get(instance_id, {}).get("test_after_patch", {})
                
                # Check if there was a failure before
                fail_before = 0
                for item in test_result_before:
                    if test_result_before[item] == 'SKIP':
                        continue
                    if test_result_before[item] == 'FAILED' or test_result_before[item] == 'ERROR':
                        fail_before = 1
                        break
                
                # Check if all tests pass after (or at least one passes and none fail)
                pass_after = 0
                for item in test_result:
                    if test_result[item] == 'PASSED':
                        pass_after = 1
                    if test_result[item] == 'SKIP':
                        continue
                    if test_result[item] == 'FAILED' or test_result[item] == 'ERROR':
                        pass_after = 0
                        break
                
                if fail_before == 1 and pass_after == 1:
                    resolved = True
        except Exception as e:
            print(f"[run_instance] Failed to determine resolved status: {e}")

    report = {
        instance_id: {
            "contributing_functions": fun2test,
            "test_after_patch": test_result,
            "test_version": test_version,
            "code_patch_version": code_version,
            "test_patch": test_patch_value,
            "code_patch": code_patch_value,
            "resolved": resolved,
            "cov_map": cov_map,
        }
    }

    report_path = pair_log_dir / f"{pair_prefix}__report{pair_suffix}.json"
    with open(report_path, "w") as f:
        f.write(json.dumps(report, indent=4))
    print(f"[run_instance] wrote report {report_path}")


def run_instance(
        test_spec,
        pred: dict,
        rm_image: bool,
        force_rebuild: bool,
        client: docker.DockerClient,
        run_id: str,
        timeout: int | None = None,
        keep_container: bool = False,
        log_run_id: str | None = None,
        pbt_mode: str = "individual",
        is_rebench: bool = False,
    ):
    """
    Run a single instance with the given prediction.

    Args:
        test_spec: TestSpec instance
        pred (dict): Prediction w/ model_name_or_path, model_patch, instance_id
        rm_image (bool): Whether to remove the image after running
        force_rebuild (bool): Whether to force rebuild the image
        client (docker.DockerClient): Docker client
        run_id (str): Run ID (used for container names)
        timeout (int): Timeout for running tests
        keep_container (bool): If True, container will not be removed after evaluation
        log_run_id (str): Run ID for log paths (may include subdirectory)
    """
    # Use log_run_id for log paths if provided, otherwise use run_id
    if log_run_id is None:
        log_run_id = run_id

    instance_id = test_spec.instance_id
    model_name_or_path = pred.get("model_name_or_path", "None").replace("/", "__")
    code_patches = pred.get("code_patches", pred.get("model_patch", []))
    code_patch_versions = pred.get("code_patch_versions", [])
    test_patches = pred.get("test_patches", pred.get("test_patch_list", []))
    test_patch_versions = pred.get("patch_versions", [])


    # print("=" * 30)
    # print("Code Patches: ")
    # print("=" * 30)
    # print(code_patches)
    

    if not isinstance(test_patches, list):
        test_patches = [test_patches] if test_patches else []
    if not isinstance(code_patches, list):
        code_patches = [code_patches] if code_patches else []

    if not test_patches:
        test_patches = [test_spec.test_patch]
    if not code_patches:
        code_patches = [""]

    base_log_dir = RUN_EVALUATION_LOG_DIR / log_run_id / model_name_or_path / instance_id
    base_log_dir.mkdir(parents=True, exist_ok=True)

    build_dir = INSTANCE_IMAGE_BUILD_DIR / test_spec.instance_image_key.replace(":", "__")
    image_build_link = base_log_dir / "image_build_dir"
    if not image_build_link.exists():
        try:
            image_build_link.symlink_to(build_dir.absolute(), target_is_directory=True)
        except:
            pass

    log_file = base_log_dir / "run_instance.log"
    logger = setup_logger(instance_id, log_file)
    container = None

    try:
        print(f"[run_instance] starting base instance={instance_id}")
        print(f"[run_instance] discovered test_patches={len(test_patches)} code_patches={len(code_patches)}")
        print(f"[run_instance] keep_container={keep_container}, run_id={run_id}")
        container = build_container(test_spec, client, run_id, logger, rm_image, force_rebuild, keep_container)
        # Start container if it's not already running
        if container.status != 'running':
            try:
                container.start()
            except Exception as start_err:
                # The container's RW layer may be missing (e.g. after a Docker data
                # directory wipe) even though the container object still exists in
                # Docker's metadata. Remove it and create a fresh one.
                logger.warning(f"Failed to start existing container ({start_err}), removing and recreating...")
                print(f"[run_instance] container start failed ({start_err}), recreating...")
                try:
                    container.remove(force=True)
                except Exception:
                    pass
                container = build_container(test_spec, client, run_id, logger, rm_image, force_rebuild=True, reuse_container=False)
                container.start()
        else:
            print(f"[run_instance] container already running, reusing it")
        logger.info(f"Container for {instance_id} started: {container.id}")
        print(f"[run_instance] container started instance={instance_id} container_id={container.id}")
        

        # ========================================================================================
        # hypothesis and pytest are now installed during environment image build
        # No need to install at runtime - this avoids DNS resolution issues
        # ========================================================================================
        print(f"[run_instance] Using pre-installed hypothesis and pytest from environment image")
        # ========================================================================================


        print(f"[run_instance] evaluation loop counts instance={instance_id} test_patch_count={len(test_patches)} code_patch_count={len(code_patches)}")
        for test_idx, test_patch_value in enumerate(test_patches):


            # =============================================
            print("=" * 30)
            print("[run_instance] # DISCOVERY MODE: ")
            print("=" * 30)
            # =============================================


            test_version = test_patch_versions[test_idx] if test_idx < len(test_patch_versions) else test_idx + 1
            print(f"[run_instance] preparing test patch version={test_version} for instance={instance_id}")

            reset_command = "bash -c 'rm -rf /testbed/pbt && git reset --hard && git clean -fdx && find /testbed -name \"*.rej\" -delete && find /testbed -name \"*.orig\" -delete'"

            #print(f"[run_instance] preparing repository for contributing test discovery for test version={test_version}")
            reset_output, _, _ = exec_run_with_timeout(container, reset_command, timeout)
            print(f"\n[run_evaluation] Reset Output: {reset_output}\n")

            discover_log_dir = RUN_EVALUATION_LOG_DIR / log_run_id / model_name_or_path / instance_id
            discover_log_dir.mkdir(parents=True, exist_ok=True)
            discover_test_patch_file = Path(discover_log_dir / f"test_patch_test_{test_version}_discovery.diff")
            discover_test_patch_file.write_text(test_patch_value or "")
            copy_to_container(container, discover_test_patch_file, Path(f"/tmp/test_patch_test_{test_version}_discovery.diff"))

            print(f"[run_instance] applying test patch for contributing test discovery version={test_version}")
            test_discovery_apply = container.exec_run(
                f"git apply --allow-empty -v /tmp/test_patch_test_{test_version}_discovery.diff",
                workdir="/testbed",
                user="root",
            )
            if test_discovery_apply.exit_code != 0:
                test_discovery_apply = container.exec_run(
                    f"patch --batch --fuzz=5 -p1 -i /tmp/test_patch_test_{test_version}_discovery.diff",
                    workdir="/testbed",
                    user="root",
                )
                if test_discovery_apply.exit_code != 0:
                    raise EvaluationError(
                        instance_id,
                        f"{APPLY_PATCH_FAIL} while applying test patch for contributing test discovery:\n{test_discovery_apply.output.decode('utf-8')}",
                        logger,
                    )

            # =======================================================
            # Newly added: copy test fixture to log pbt test cases
            # Note: Only copy to /tmp/ here, the eval script will copy to the correct location
            # =======================================================
            conftest_dir = Path(__file__).parent.parent.parent.parent  # Go up to workspace root
            conftest_path = conftest_dir / "conftest.py"
            copy_to_container(container, conftest_path, Path("/tmp/conftest.py"))
            # =======================================================

            fun2test = []
            contributing_functions = get_contributing_functions(test_patch_value)
            print(f"[run_instance] extracted contributing test files={len(contributing_functions)} for test version={test_version}")
            for test_file in contributing_functions:
                # Use container.exec_run directly with a longer timeout for reading files
                result = container.exec_run(f"cat {test_file}", workdir="/testbed", user="root")
                if result.exit_code != 0:
                    print(f"[run_instance] Warning: Could not read test file {test_file}, exit_code={result.exit_code}")
                    # Add functions without class resolution
                    for item in contributing_functions[test_file]:
                        fun2test.append(test_file + "::" + item)
                    continue
                
                test_output = result.output.decode('utf-8')
                print(f"[run_instance] Read {len(test_output)} bytes from {test_file}")
                
                try:
                    class_func = get_class_functions(test_output)
                    outer_func = get_outer_functions(test_output)
                    for item in contributing_functions[test_file]:
                        if item in class_func:
                            fun2test.append(test_file + "::" + class_func[item] + "::" + item)
                        elif item in outer_func:
                            fun2test.append(test_file + "::" + item)
                except (SyntaxError, ValueError) as e:
                    print(f"[run_instance] Warning: Could not parse test file {test_file}: {e}")
                    print(f"[run_instance] File content preview (first 500 chars): {test_output[:500]}")
                    # Add functions without class resolution
                    for item in contributing_functions[test_file]:
                        fun2test.append(test_file + "::" + item)

            print(f"[run_instance] cleaning repository after contributing test discovery for test version={test_version}")
            exec_run_with_timeout(container, f"git apply -R /tmp/test_patch_test_{test_version}_discovery.diff || true", timeout)
            exec_run_with_timeout(container, "git clean -fd", timeout)
            exec_run_with_timeout(container, "git reset --hard", timeout)

            print(f"[run_instance] derived contributing functions={len(fun2test)} for test version={test_version}")
            print(f"[run_instance] fun2test for test version={test_version}: {fun2test}")
            
            pair_log_dir = RUN_EVALUATION_LOG_DIR / log_run_id / model_name_or_path / instance_id
            pair_log_dir.mkdir(parents=True, exist_ok=True)

            test_version_spec = test_spec.__class__(
                instance_id=test_spec.instance_id,
                repo=test_spec.repo,
                version=test_spec.version,
                repo_script_list=test_spec.repo_script_list,
                eval_script_list=test_spec.eval_script_list,
                env_script_list=test_spec.env_script_list,
                arch=test_spec.arch,
                test_patch=test_patch_value,
            )
            env_name = "testbed"
            repo_directory = f"/{env_name}"
            instance_payload = {
                "repo": test_spec.repo,
                "version": test_spec.version,
                "instance_id": test_spec.instance_id,
                "test_patch": test_patch_value,
            }
            if is_rebench and "install_config" in pred:
                instance_payload["install_config"] = pred["install_config"]
            specs = (resolve_specs_rebench if is_rebench else resolve_specs)(instance_payload)
            print(f"[run_instance] rebuilding eval_script_list for test version={test_version} payload_keys={sorted(instance_payload.keys())}")
            test_version_spec.eval_script_list = (make_eval_script_list_rebench if is_rebench else make_eval_script_list)(
                instance_payload,
                specs,
                env_name,
                repo_directory,
                test_spec.repo_script_list[3].split("git reset --hard ", 1)[1],
                instance_payload["test_patch"],
            )
            test_eval_file = Path(pair_log_dir / f"eval_test_{test_version}.sh")
            test_eval_file.write_text(test_version_spec.eval_script)
            with open(test_eval_file, "r") as f:
                test_eval_text = f.read()
            
            # # !!! Slight modification: no need to modify eval to append the instance_id related fully qualified test names (we only use /testbed/pbt/test.py now) 
            # test_eval_text = modify_eval(test_eval_text, instance_id, fun2test)
            
            with open(test_eval_file, "w") as f:
                f.write(test_eval_text)

            container_test_eval_path = Path(f"/tmp/eval_test_{test_version}.sh")
            copy_to_container(container, test_eval_file, container_test_eval_path)


            # =============================================
            print("=" * 30)
            print("[run_instance] # BASELINE MODE: ")
            print("=" * 30)
            # =============================================

            # First, run the test with an empty code patch (baseline)
            print(f"[run_instance] running baseline test with empty code patch for test version={test_version}")
            code_patch_value = code_patches[0]
            baseline_code_version = 0
            baseline_pair_suffix = f"_test_{test_version}_code_{baseline_code_version}"
            try:
                print(f"[run_instance] evaluating baseline pair instance={instance_id} test_version={test_version} code_version={baseline_code_version}")
                print(f"[run_instance] test patch content for {baseline_pair_suffix}:\n{test_patch_value}")
                print(f"[run_instance] code patch content for {baseline_pair_suffix}: (empty)")

                print(f"[run_instance] cleaning repository before baseline pair {baseline_pair_suffix}")
                exec_run_with_timeout(container, reset_command, timeout)

                baseline_report_path = pair_log_dir / f"report{baseline_pair_suffix}.json"
                
                if not baseline_report_path.exists():
                    baseline_test_patch_file = Path(pair_log_dir / f"test_patch{baseline_pair_suffix}.diff")
                    baseline_test_patch_file.write_text(test_patch_value or "")
                    baseline_code_patch_file = Path(pair_log_dir / f"patch{baseline_pair_suffix}.diff")
                    baseline_code_patch_file.write_text("")  # Empty code patch

                    copy_to_container(container, baseline_test_patch_file, Path(f"/tmp/test_patch{baseline_pair_suffix}.diff"))
                    copy_to_container(container, baseline_code_patch_file, Path(f"/tmp/patch{baseline_pair_suffix}.diff"))

                    print(f"[run_instance] attempting reverse apply for stale test patch before baseline pair {baseline_pair_suffix}")
                    exec_run_with_timeout(container, f"git apply -R /tmp/test_patch{baseline_pair_suffix}.diff || true", timeout)
                    print(f"[run_instance] attempting reverse apply for stale code patch before baseline pair {baseline_pair_suffix}")
                    exec_run_with_timeout(container, f"git apply -R /tmp/patch{baseline_pair_suffix}.diff || true", timeout)
                    print(f"[run_instance] cleaning repository after reverse attempts before baseline pair {baseline_pair_suffix}")
                    exec_run_with_timeout(container, reset_command, timeout)

                    # print(f"[run_instance] applying test patch for baseline pair {baseline_pair_suffix}")
                    # baseline_test_apply = container.exec_run(
                    #     f"git apply --allow-empty -v /tmp/test_patch{baseline_pair_suffix}.diff",
                    #     workdir="/testbed",
                    #     user="root",
                    # )
                    # if baseline_test_apply.exit_code != 0:
                    #     baseline_test_apply = container.exec_run(
                    #         f"patch --batch --fuzz=5 -p1 -i /tmp/test_patch{baseline_pair_suffix}.diff",
                    #         workdir="/testbed",
                    #         user="root",
                    #     )
                    #     if baseline_test_apply.exit_code != 0:
                    #         raise EvaluationError(
                    #             instance_id,
                    #             f"{APPLY_PATCH_FAIL} while applying test patch for baseline:\n{baseline_test_apply.output.decode('utf-8')}",
                    #             logger,
                    #         )

                    print(f"[run_instance] applying empty code patch for baseline pair {baseline_pair_suffix}")
                    baseline_code_apply = container.exec_run(
                        f"git apply --allow-empty -v /tmp/patch{baseline_pair_suffix}.diff",
                        workdir="/testbed",
                        user="root",
                    )

                    # =======================================================
                    # Newly added: copy test fixture to log pbt test cases
                    # =======================================================
                    conftest_dir = Path(__file__).parent.parent.parent.parent  # Go up to workspace root
                    conftest_path = conftest_dir / "conftest.py"
                    copy_to_container(container, conftest_path, Path("/tmp/conftest.py"))
                    # =======================================================

                    # =======================================================
                    # Test execution on buggy version
                    # =======================================================
                    regression_hypothesis_summary_path = base_log_dir / "regression_hypothesis_summary_0.json"
                    reproduction_hypothesis_summary_path = base_log_dir / "reproduction_hypothesis_summary_0.json"

                    print(f"[run_instance] running shared eval for baseline pair {baseline_pair_suffix}")
                    
                    if pbt_mode in ["regression", "combined"]:
                        baseline_test_output, baseline_timed_out, baseline_total_runtime = exec_run_with_timeout(container, f"/bin/bash -c 'export IS_REGRESSION=true ; /bin/bash {container_test_eval_path}'", timeout)

                        # print("=" * 30)
                        # print("Code patch value:")
                        # print("=" * 30)
                        # print(code_patch_value)

                        log_baseline(instance_id, "regression", baseline_test_output, baseline_timed_out, timeout, pair_log_dir, baseline_pair_suffix, fun2test, test_version, baseline_code_version, test_patch_value, code_patch_value, baseline_report_path, pred.get("log_parser"))
                        copy_from_container(container, Path("/tmp/hypothesis_summary.json"), regression_hypothesis_summary_path, raise_on_error=False)

                        _, _, _ = exec_run_with_timeout(container, "rm /tmp/hypothesis_summary.json", timeout)
                    
                    if pbt_mode in ["reproduction", "combined"]:
                        baseline_test_output, baseline_timed_out, baseline_total_runtime = exec_run_with_timeout(container, f"/bin/bash -c 'export IS_REGRESSION=false ; /bin/bash {container_test_eval_path}'", timeout)
                        log_baseline(instance_id, "reproduction", baseline_test_output, baseline_timed_out, timeout, pair_log_dir, baseline_pair_suffix, fun2test, test_version, baseline_code_version, test_patch_value, code_patch_value, baseline_report_path, pred.get("log_parser"))
                        copy_from_container(container, Path("/tmp/hypothesis_summary.json"), reproduction_hypothesis_summary_path, raise_on_error=False)

                        _, _, _ = exec_run_with_timeout(container, "rm /tmp/hypothesis_summary.json", timeout)
                    # =======================================================

                    print(f"[run_instance] cleaning repository after baseline pair {baseline_pair_suffix}")
                    exec_run_with_timeout(container, f"git apply -R /tmp/patch{baseline_pair_suffix}.diff || true", timeout)
                    exec_run_with_timeout(container, f"git apply -R /tmp/test_patch{baseline_pair_suffix}.diff || true", timeout)
                    exec_run_with_timeout(container, "git reset --hard", timeout)
                    exec_run_with_timeout(container, "git clean -fd", timeout)
                    
                    # Cleanup temporary files for baseline pair
                    cleanup_pair_temp_files(pair_log_dir, baseline_pair_suffix)
                else:
                    print(f"[run_instance] skipping existing baseline report for {baseline_pair_suffix}")
            except EvaluationError as baseline_error:
                error_msg = traceback.format_exc()
                logger.info(error_msg)
                print(baseline_error)
                print(f"[run_instance] baseline pair failure for {baseline_pair_suffix}; continuing to actual code patches")
                exec_run_with_timeout(container, f"git apply -R /tmp/patch{baseline_pair_suffix}.diff || true", timeout)
                exec_run_with_timeout(container, f"git apply -R /tmp/test_patch{baseline_pair_suffix}.diff || true", timeout)
                exec_run_with_timeout(container, "git reset --hard", timeout)
                exec_run_with_timeout(container, "git clean -fd", timeout)
            

            # =============================================
            print("=" * 30)
            print("[run_instance] # FAIL-TO-PASS MODE: ")
            print("=" * 30)
            # =============================================


            # Now run the test with each actual code patch
            for code_idx, code_patch_value in enumerate(code_patches):
                code_version = code_patch_versions[code_idx] if code_idx < len(code_patch_versions) else code_idx + 1
                pair_suffix = f"_test_{test_version}_code_{code_version}"
                try:
                    print(f"[run_instance] evaluating pair instance={instance_id} test_version={test_version} code_version={code_version}")
                    print(f"[run_instance] test patch content for {pair_suffix}:\n{test_patch_value}")
                    print(f"[run_instance] code patch content for {pair_suffix}:\n{code_patch_value}")

                    print(f"[run_instance] cleaning repository before pair {pair_suffix}")
                    exec_run_with_timeout(container, reset_command, timeout)

                    report_path = pair_log_dir / f"report{pair_suffix}.json"
                    if report_path.exists():
                        print(f"[run_instance] skipping existing report for pair {pair_suffix}")
                        continue

                    test_patch_file = Path(pair_log_dir / f"test_patch{pair_suffix}.diff")
                    test_patch_file.write_text(test_patch_value or "")
                    code_patch_file = Path(pair_log_dir / f"patch{pair_suffix}.diff")
                    code_patch_file.write_text(code_patch_value or "")

                    copy_to_container(container, test_patch_file, Path(f"/tmp/test_patch{pair_suffix}.diff"))
                    copy_to_container(container, code_patch_file, Path(f"/tmp/patch{pair_suffix}.diff"))

                    print(f"[run_instance] attempting reverse apply for stale test patch before pair {pair_suffix}")
                    exec_run_with_timeout(container, f"git apply -R /tmp/test_patch{pair_suffix}.diff || true", timeout)
                    print(f"[run_instance] attempting reverse apply for stale code patch before pair {pair_suffix}")
                    exec_run_with_timeout(container, f"git apply -R /tmp/patch{pair_suffix}.diff || true", timeout)
                    print(f"[run_instance] cleaning repository after reverse attempts before pair {pair_suffix}")
                    exec_run_with_timeout(container, reset_command, timeout)

                    # print(f"[run_instance] applying test patch for pair {pair_suffix}")
                    # test_apply = container.exec_run(
                    #     f"git apply --allow-empty -v /tmp/test_patch{pair_suffix}.diff",
                    #     workdir="/testbed",
                    #     user="root",
                    # )
                    # if test_apply.exit_code != 0:
                    #     test_apply = container.exec_run(
                    #         f"patch --batch --fuzz=5 -p1 -i /tmp/test_patch{pair_suffix}.diff",
                    #         workdir="/testbed",
                    #         user="root",
                    #     )
                    #     if test_apply.exit_code != 0:
                    #         raise EvaluationError(
                    #             instance_id,
                    #             f"{APPLY_PATCH_FAIL} while applying test patch:\n{test_apply.output.decode('utf-8')}",
                    #             logger,
                    #         )

                    print(f"[run_instance] applying code patch for pair {pair_suffix}")
                    code_apply = container.exec_run(
                        f"git apply --allow-empty -v /tmp/patch{pair_suffix}.diff",
                        workdir="/testbed",
                        user="root",
                    )
                    if code_apply.exit_code != 0:
                        code_apply = container.exec_run(
                            f"patch --batch --fuzz=5 -p1 -i /tmp/patch{pair_suffix}.diff",
                            workdir="/testbed",
                            user="root",
                        )
                        if code_apply.exit_code != 0:
                            raise EvaluationError(
                                instance_id,
                                f"{APPLY_PATCH_FAIL}:\n{code_apply.output.decode('utf-8')}",
                                logger,
                            )

                    # =======================================================
                    # Newly added: copy test fixture to log pbt test cases
                    # =======================================================
                    conftest_dir = Path(__file__).parent.parent.parent.parent  # Go up to workspace root
                    conftest_path = conftest_dir / "conftest.py"
                    copy_to_container(container, conftest_path, Path("/tmp/conftest.py"))
                    # =======================================================

                    # =======================================================
                    # Test execution on buggy version
                    # =======================================================
                    regression_hypothesis_summary_path = base_log_dir / "regression_hypothesis_summary_1.json"
                    reproduction_hypothesis_summary_path = base_log_dir / "reproduction_hypothesis_summary_1.json"

                    print(f"[run_instance] running shared eval for pair {pair_suffix}")
                    
                    if pbt_mode in ["regression", "combined"]:
                        test_output, timed_out, total_runtime = exec_run_with_timeout(container, f"/bin/bash -c 'export IS_REGRESSION=true ; /bin/bash {container_test_eval_path}'", timeout)
                        log_f2p(instance_id, "regression", test_output, timed_out, timeout, pair_log_dir, pair_suffix, fun2test, test_version, code_version, test_patch_value, code_patch_value, report_path, baseline_pair_suffix, pred.get("log_parser"))
                        copy_from_container(container, Path("/tmp/hypothesis_summary.json"), regression_hypothesis_summary_path, raise_on_error=False)

                        _, _, _ = exec_run_with_timeout(container, "rm /tmp/hypothesis_summary.json", timeout)

                    if pbt_mode in ["reproduction", "combined"]:
                        test_output, timed_out, total_runtime = exec_run_with_timeout(container, f"/bin/bash -c 'export IS_REGRESSION=false ; /bin/bash {container_test_eval_path}'", timeout)
                        log_f2p(instance_id, "reproduction", test_output, timed_out, timeout, pair_log_dir, pair_suffix, fun2test, test_version, code_version, test_patch_value, code_patch_value, report_path, baseline_pair_suffix, pred.get("log_parser"))
                        copy_from_container(container, Path("/tmp/hypothesis_summary.json"), reproduction_hypothesis_summary_path, raise_on_error=False)
                    
                        _, _, _ = exec_run_with_timeout(container, "rm /tmp/hypothesis_summary.json", timeout)
                    # =======================================================

                    print(f"[run_instance] cleaning repository after pair {pair_suffix}")
                    exec_run_with_timeout(container, f"git apply -R /tmp/patch{pair_suffix}.diff || true", timeout)
                    exec_run_with_timeout(container, f"git apply -R /tmp/test_patch{pair_suffix}.diff || true", timeout)
                    exec_run_with_timeout(container, "git reset --hard", timeout)
                    exec_run_with_timeout(container, "git clean -fd", timeout)
                    
                    # Cleanup temporary files for this pair
                    cleanup_pair_temp_files(pair_log_dir, pair_suffix)
                except EvaluationError as pair_error:
                    error_msg = traceback.format_exc()
                    logger.info(error_msg)
                    print(pair_error)
                    print(f"[run_instance] pair failure for {pair_suffix}; continuing to next pair")
                    exec_run_with_timeout(container, f"git apply -R /tmp/patch{pair_suffix}.diff || true", timeout)
                    exec_run_with_timeout(container, f"git apply -R /tmp/test_patch{pair_suffix}.diff || true", timeout)
                    exec_run_with_timeout(container, "git clean -fd", timeout)
                    exec_run_with_timeout(container, "git reset --hard", timeout)
                    continue

    except EvaluationError as e:
        error_msg = traceback.format_exc()
        logger.info(error_msg)
        print(e)
        print(f"[run_instance] evaluation error encountered for instance={instance_id}; continuing outer cleanup")
        print(f"[run_instance] unhandled EvaluationError escaped loop processing for instance={instance_id}")
    except BuildImageError as e:
        error_msg = traceback.format_exc()
        logger.info(error_msg)
        print(e)
    except Exception as e:
        error_msg = (f"Error in evaluating model for {instance_id}: {e}\n"
                     f"{traceback.format_exc()}\n"
                     f"Check ({logger.log_file}) for more information.")
        logger.error(error_msg)
    finally:
        if keep_container:
            print(f"[run_instance] keeping container for instance={instance_id} (--keep_container flag set)")
            # Stop the container but don't remove it
            if container:
                try:
                    # ============================================================
                    # Keeping the container running for persistent debugging
                    # ============================================================
                    # container.stop()
                    # print(f"[run_instance] container stopped: {container.id}")
                    # ============================================================
                    pass
                except Exception as e:
                    print(f"[run_instance] error stopping container: {e}")
        else:
            print(f"[run_instance] cleaning up container for instance={instance_id}")
            # Remove instance container + image, close logger
            cleanup_container(client, container, logger)
            if rm_image:
                remove_image(client, test_spec.instance_image_key, logger)
        close_logger(logger)

    return


def run_instances(
        predictions: dict,
        instances: list,
        cache_level: str,
        clean: bool,
        force_rebuild: bool,
        max_workers: int,
        run_id: str,
        timeout: int,
        keep_container: bool = False,
        container_run_id: str | None = None,
        pbt_mode: str = "reproduction",
        is_rebench: bool = False,
    ):
    """
    Run all instances for the given predictions in parallel.

    Args:
        predictions (dict): Predictions dict generated by the model
        instances (list): List of instances
        cache_level (str): Cache level
        clean (bool): Clean images above cache level
        force_rebuild (bool): Force rebuild images
        max_workers (int): Maximum number of workers
        run_id (str): Run ID (for logging paths)
        timeout (int): Timeout for running tests
        keep_container (bool): If True, containers will not be removed after evaluation
        container_run_id (str): Run ID for container names (must be Docker-safe, no slashes)
    """
    # Use container_run_id for container names if provided, otherwise use run_id
    if container_run_id is None:
        container_run_id = run_id
    client = docker.from_env()
    test_specs = list(map(make_test_spec_rebench if is_rebench else make_test_spec, instances))
    for test_spec in test_specs:
        pred = predictions[test_spec.instance_id]
        print(
            f"[run_instances] instance={test_spec.instance_id} "
            f"pred_keys={sorted(pred.keys())} "
            f"pred_test_patches={len(pred.get('test_patches', [])) if isinstance(pred.get('test_patches', []), list) else 1} "
            f"pred_test_patch_list={len(pred.get('test_patch_list', [])) if isinstance(pred.get('test_patch_list', []), list) else 1} "
            f"pred_code_patches={len(pred.get('code_patches', pred.get('model_patch', []))) if isinstance(pred.get('code_patches', pred.get('model_patch', [])), list) else 1} "
            f"spec_test_patch_type={type(test_spec.test_patch).__name__}"
        )

    # print number of existing instance images
    instance_image_ids = {x.instance_image_key for x in test_specs}
    existing_images = {
        tag for i in client.images.list(all=True)
        for tag in i.tags if tag in instance_image_ids
    }
    if not force_rebuild and len(existing_images):
        print(f"Found {len(existing_images)} existing instance images. Will reuse them.")

    # run instances in parallel
    print(f"Running {len(instances)} instances...")
    with tqdm(total=len(instances), smoothing=0) as pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Create a future for running each instance
            futures = {
                executor.submit(
                    run_instance,
                    test_spec,
                    predictions[test_spec.instance_id],
                    should_remove(
                        test_spec.instance_image_key,
                        cache_level,
                        clean,
                        existing_images,
                    ),
                    force_rebuild,
                    client,
                    container_run_id,  # Use container_run_id for container names
                    timeout,
                    keep_container,
                    run_id,  # Use run_id (log_run_id) for log paths
                    pbt_mode,
                    is_rebench,
                ): None
                for test_spec in test_specs
            }
            # Wait for each future to complete
            for future in as_completed(futures):
                pbar.update(1)
                try:
                    # Update progress bar, check if instance ran successfully
                    future.result()
                except Exception as e:
                    traceback.print_exc()
                    continue
    print("All instances run.")


def get_dataset_from_preds(
        dataset_name: str,
        split: str,
        instance_ids: list,
        predictions: dict,
        patch_model_name: str,
        run_id: str,
        exclude_completed: bool = True
    ):
    """
    Return only instances that have predictions and are in the dataset.
    If instance_ids is provided, only return instances with those IDs.
    If exclude_completed is True, only return instances that have not been run yet.
    """
    # load dataset
    dataset = load_tddbench_dataset(dataset_name, split)
    dataset_ids = {i[KEY_INSTANCE_ID] for i in dataset}

    if instance_ids:
        # check that all instance IDs have predictions
        missing_preds = set(instance_ids) - set(predictions.keys())
        if missing_preds:
            print(f"Warning: Missing predictions for {len(missing_preds)} instance IDs.")
    
    # check that all prediction IDs are in the dataset
    prediction_ids = set(predictions.keys())
    missing_prediction_ids = prediction_ids - dataset_ids
    if missing_prediction_ids:
        if len(dataset_ids) < len(prediction_ids):
            print(
                f"Filtering predictions to dataset instances only: "
                f"{len(prediction_ids) - len(missing_prediction_ids)} matched, "
                f"{len(missing_prediction_ids)} ignored."
            )
            predictions = {k: v for k, v in predictions.items() if k in dataset_ids}
            prediction_ids = set(predictions.keys())
        else:
            raise ValueError(
                (
                    "Some prediction IDs not found in dataset!"
                    f"\nMissing IDs:\n{' '.join(missing_prediction_ids)}"
                )
            )
    if instance_ids:
        dataset = [i for i in dataset if i[KEY_INSTANCE_ID] in instance_ids]

    # check which instance IDs have already been run
    completed_ids = set()
    for instance in dataset:
        if instance[KEY_INSTANCE_ID] not in prediction_ids:
            # skip instances without predictions
            continue
        prediction = predictions[instance[KEY_INSTANCE_ID]]
        report_file = (
            RUN_EVALUATION_LOG_DIR
            / run_id
            / patch_model_name
            / prediction[KEY_INSTANCE_ID]
            / "report.json"
        )
        if report_file.exists():
            completed_ids.add(instance[KEY_INSTANCE_ID])

    if completed_ids and exclude_completed:
        # filter dataset to only instances that have not been run
        print(f"{len(completed_ids)} instances already run, skipping...")
        dataset = [i for i in dataset if i[KEY_INSTANCE_ID] not in completed_ids]

    empty_patch_ids = {
        k for k, v in predictions.items()
        if (
            ("test_patches" in v and (not v["test_patches"]))
            or ("test_patches" not in v and (v.get("model_patch") == "" or v.get("model_patch") is None))
        )
    }

    # filter dataset to only instances with predictions
    dataset = [i for i in dataset if i[KEY_INSTANCE_ID] in prediction_ids and i[KEY_INSTANCE_ID] not in empty_patch_ids]
    return dataset


def make_run_report(
        patch_model_name,
        predictions: dict,
        full_dataset: list,
        client: docker.DockerClient,
        run_id: str
    ) -> Path:
    """
    Make a final evaluation and run report of the instances that have been run.
    Also reports on images and containers that may still running!
    Args:
        predictions (dict): Predictions dict generated by the model
        full_dataset (list): List of all instances
        client (docker.DockerClient): Docker client
        run_id (str): Run ID
    
    Returns:
        Path to report file
    """
    # instantiate sets to store IDs of different outcomes
    completed_ids = set()
    resolved_ids = set()
    error_ids = set()
    unstopped_containers = set()
    unremoved_images = set()
    unresolved_ids = set()
    incomplete_ids = set()
    # get instances with empty patches
    empty_patch_ids = set()

    score = 0.0
    score_count = 0

    # iterate through dataset and check if the instance has been run
    for instance in full_dataset:
        instance_id = instance[KEY_INSTANCE_ID]
        if instance_id not in predictions:
            # skip instances without 
            incomplete_ids.add(instance_id)
            continue
        prediction = predictions[instance_id]
        has_test_patches = "test_patches" in prediction
        test_patches_value = prediction.get("test_patches", prediction.get("model_patch", None))
        if has_test_patches:
            if isinstance(test_patches_value, list):
                if not any(isinstance(p, str) and p.strip() for p in test_patches_value):
                    empty_patch_ids.add(instance_id)
                    continue
            elif test_patches_value in ["", None]:
                empty_patch_ids.add(instance_id)
                continue
        elif prediction.get("model_patch", None) in ["", None]:
            empty_patch_ids.add(instance_id)
            continue
        report_file = (
            RUN_EVALUATION_LOG_DIR
            / run_id
            / patch_model_name
            / prediction[KEY_INSTANCE_ID]
            / "report.json"
        )
        
        if report_file.exists():
            # If report file exists, then the instance has been run
            completed_ids.add(instance_id)
            report = json.loads(report_file.read_text())
            if report[instance_id]["resolved"]:
                # Record if the instance was resolved
                resolved_ids.add(instance_id)
            else:
                unresolved_ids.add(instance_id)
            if "final_score" in report[instance_id]:
                score += float(report[instance_id]["final_score"])
                score_count += 1
            else:
                score += float(report[instance_id].get("resolved", False))
                score_count += 1

        else:
            # Otherwise, the instance was not run successfully
            error_ids.add(instance_id)

    # get remaining images and containers
    images = list_images(client)
    test_specs = list(map(make_test_spec, full_dataset))
    for spec in test_specs:
        image_name = spec.instance_image_key
        if image_name in images:
            unremoved_images.add(image_name)
    containers = client.containers.list(all=True)
    for container in containers:
        if run_id in container.name:
            unstopped_containers.add(container.name)

    # print final report
    dataset_ids = {i[KEY_INSTANCE_ID] for i in full_dataset}
    print(f"Total instances: {len(full_dataset)}")
    print(f"Instances submitted: {len(set(predictions.keys()) & dataset_ids)}")
    print(f"Instances completed: {len(completed_ids)}")
    print(f"Instances incomplete: {len(incomplete_ids)}")
    print(f"Instances resolved: {len(resolved_ids)}")
    print(f"Instances unresolved: {len(unresolved_ids)}")
    print(f"Instances with empty patches: {len(empty_patch_ids)}")
    print(f"Instances with errors: {len(error_ids)}")
    print(f"Unstopped containers: {len(unstopped_containers)}")
    print(f"Unremoved images: {len(unremoved_images)}")
    final_score = score / score_count if score_count else 0.0
    print(f"Final score: {final_score}")

    # write report to file
    report = {
        "total_instances": len(full_dataset),
        "submitted_instances": len(predictions),
        "completed_instances": len(completed_ids),
        "resolved_instances": len(resolved_ids),
        "unresolved_instances": len(unresolved_ids),
        "empty_patch_instances": len(empty_patch_ids),
        "error_instances": len(error_ids),
        "unstopped_instances": len(unstopped_containers),
        "completed_ids": list(sorted(completed_ids)),
        "incomplete_ids": list(sorted(incomplete_ids)),
        "empty_patch_ids": list(sorted(empty_patch_ids)),
        "submitted_ids": list(sorted(predictions.keys())),
        "resolved_ids": list(sorted(resolved_ids)),
        "unresolved_ids": list(sorted(unresolved_ids)),
        "error_ids": list(sorted(error_ids)),
        "unstopped_containers": list(sorted(unstopped_containers)),
        "unremoved_images": list(sorted(unremoved_images)),
        "final_score": final_score,
        "schema_version": 2,
    }
    report_file = Path(
        list(predictions.values())[0]["model_name_or_path"].replace("/", "__")
        + f".{run_id.replace('/', '_')}"
        + ".json"
    )
    with open(report_file, "w") as f:
        print(json.dumps(report, indent=4), file=f)
    print(f"Report written to {report_file}")
    return report_file


def get_golden_patch(dataset_name: str, split: str):
    """
    Get golden patch for the given dataset and split.
    """
    dataset = load_tddbench_dataset(dataset_name, split)
    return [
        {
            KEY_INSTANCE_ID: datum[KEY_INSTANCE_ID],
            "model_patch": datum["patch"],
            "model_name_or_path": "gold",
        } for datum in dataset
    ]


def get_gold_predictions(dataset_name: str, split: str):
    """
    Get gold predictions for the given dataset and split.
    """
    dataset = load_tddbench_dataset(dataset_name, split)
    return [
        {
            KEY_INSTANCE_ID: datum[KEY_INSTANCE_ID],
            "model_patch": datum["test_patch"],
            "model_name_or_path": "gold",
        } for datum in dataset
    ]


def cleanup_pair_temp_files(pair_log_dir: Path, pair_suffix: str):
    """
    Clean up temporary files for a specific test-code pair after report is written.
    Deletes test_coverage files and .diff files for this pair.
    
    Args:
        pair_log_dir: Directory containing the pair's log files
        pair_suffix: Suffix identifying the pair (e.g., "_test_1_code_2")
    """
    files_to_delete = []
    
    # Add test_coverage files
    files_to_delete.append(pair_log_dir / f"test_coverage{pair_suffix}.txt")
    files_to_delete.append(pair_log_dir / f"test_coverage_combine{pair_suffix}.txt")
    
    # Add .diff files
    files_to_delete.append(pair_log_dir / f"test_patch{pair_suffix}.diff")
    files_to_delete.append(pair_log_dir / f"patch{pair_suffix}.diff")
    
    deleted_count = 0
    for file_path in files_to_delete:
        if file_path.exists():
            try:
                os.remove(file_path)
                deleted_count += 1
            except Exception as e:
                print(f"[cleanup] Failed to delete {file_path}: {e}")
    
    if deleted_count > 0:
        print(f"[cleanup] Deleted {deleted_count} temporary files for {pair_suffix}")


def main(
        dataset_name: str,
        split: str,
        instance_ids: list,
        code_patch: str,
        test_patch: str,
        max_workers: int,
        force_rebuild: bool,
        cache_level: str,
        clean: bool,
        open_file_limit: int,
        run_id: str,
        timeout: int,
        keep_container: bool | str = False,
        output_subdir: str | None = None,
        pbt_mode: str = "reproduction",
        is_rebench: bool = False,
    ):
    """
    Run evaluation harness for the given dataset and predictions.
    
    Args:
        keep_container: If True, containers will not be removed after evaluation (allows reuse).
            If set to "kill", remove matching Docker containers and exit without evaluation.
        output_subdir: Optional subdirectory name to store results (preserves old results)
    """
    # Construct run IDs for different purposes:
    # - container_run_id: Used for Docker container names (stays constant for reuse)
    #   Always uses base run_id only, NOT output_subdir, so containers can be reused
    # - log_run_id: Used for log directory paths (uses slashes for proper nesting)
    #   Includes output_subdir to organize results in separate folders
    container_run_id = run_id  # Always use base run_id for container names (enables reuse)
    log_run_id = f"{run_id}/{output_subdir}" if output_subdir else run_id
    # set open file limit
    assert len(run_id) > 0, "Run ID must be provided"
    resource.setrlimit(resource.RLIMIT_NOFILE, (open_file_limit, open_file_limit))
    client = docker.from_env()

    # load predictions as map of instance_id to prediction
    if test_patch == 'gold':
        print("Using gold predictions - ignoring test_patch")
        predictions = get_gold_predictions(dataset_name, split)
    else:
        if test_patch.endswith(".json"):
            with open(test_patch, "r") as f:
                raw_predictions = json.load(f)
        elif test_patch.endswith(".jsonl"):
            with open(test_patch, "r") as f:
                raw_predictions = [json.loads(line) for line in f]
        else:
            raise ValueError("Predictions path must be \"gold\", .json, or .jsonl")

        predictions = []
        for pred in raw_predictions:
            instance_id = pred[KEY_INSTANCE_ID]
            diffs = pred.get("diffs", None)
            if isinstance(diffs, dict):
                model_patch = [diff for diff in diffs.values() if diff]
                patch_versions = [version for version, diff in diffs.items() if diff]
            elif isinstance(diffs, list):
                model_patch = [diff for diff in diffs if diff]
                patch_versions = list(range(1, len(model_patch) + 1))
            elif diffs:
                model_patch = [diffs]
                patch_versions = [1]
            else:
                raw_model_patch = pred.get("model_patch", "")
                if isinstance(raw_model_patch, str) and raw_model_patch:
                    model_patch = [raw_model_patch]
                    patch_versions = [1]
                else:
                    model_patch = []
                    patch_versions = []

            predictions.append(
                {
                    KEY_INSTANCE_ID: instance_id,
                    "test_patches": model_patch,
                    "patch_versions": patch_versions,
                    "model_name_or_path": test_patch,
                }
            )


    if code_patch in ["", "none", "None", None]:
        print("No code patches provided - running tests against existing repository code")
        patch_model_name = "no_code_patch"
        patches = []
    elif code_patch == 'gold':
        print("Using gold patch predictions")
        patch_model_name = 'gold'
        patches = get_golden_patch(dataset_name, split)
    else:
        patch_p: Path = Path(code_patch)
        if patch_p.is_dir():
            patch_model_name = patch_p.parts[-1]
            outsw = Path(patch_p, 'output.swebench.jsonl')
            if outsw.is_file():
                print(f"Using patch prediction: {outsw}")
                patch_p = outsw
            else:
                raise ValueError(f'Given patch path given a directory ({patch_p}) without an output.swebench.jsonl file')
        else:
            patch_model_name = patch_p.parent.name or patch_p.stem

        if patch_p.suffix == ".json":
            with open(patch_p, "r") as f:
                raw_patches = json.load(f)
        elif patch_p.suffix == ".jsonl":
            with open(patch_p, "r") as f:
                raw_patches = [json.loads(line) for line in f]
        else:
            raise ValueError("Patch predictions path must be \"gold\", empty/none, .json, or .jsonl")

        patches = []
        for patch in raw_patches:
            instance_id = patch[KEY_INSTANCE_ID]
            diffs = patch.get("diffs", [])

            if isinstance(diffs, list):
                model_patch = [diff for diff in diffs if isinstance(diff, str) and diff.strip()]
                code_patch_versions = list(range(1, len(model_patch) + 1))
            elif isinstance(diffs, dict):
                model_patch = [diff for diff in diffs.values() if isinstance(diff, str) and diff.strip()]
                code_patch_versions = [version for version, diff in diffs.items() if isinstance(diff, str) and diff.strip()]
            elif isinstance(diffs, str) and diffs.strip():
                model_patch = [diffs]
                code_patch_versions = [1]
            else:
                model_patch = []
                code_patch_versions = []

            # Add golden_patch if available
            golden_patch = patch.get("golden_patch", "")
            if golden_patch and isinstance(golden_patch, str) and golden_patch.strip():
                model_patch.append(golden_patch)
                # Use "golden" as the version identifier for golden patch
                code_patch_versions.append("golden")
                print(f"[main] code patch loader instance={instance_id} added golden_patch as version 'golden'")

            print(f"[main] code patch loader instance={instance_id} extracted_code_patches={len(model_patch)}")

            patches.append(
                {
                    KEY_INSTANCE_ID: instance_id,
                    "model_patch": model_patch,
                    "code_patch_versions": code_patch_versions,
                    "model_name_or_path": patch_model_name,
                }
            )


    predictions = {pred[KEY_INSTANCE_ID]: pred for pred in predictions}
    patches = {patch[KEY_INSTANCE_ID]: patch for patch in patches}


    # print("\n[PREDICTIONS]: ")
    # print(predictions)
    # print()
    # print("\n[CODE_PATCHES]: ")
    # print(patches)
    # print()


    merged_predictions = {}
    for instance_id, prediction in predictions.items():
        patch_entry = patches.get(instance_id, {})
        code_patches = patch_entry.get("model_patch", [])
        code_patch_versions = patch_entry.get("code_patch_versions", [])

        normalized_code_patches = code_patches if isinstance(code_patches, list) else ([code_patches] if code_patches else [])
        normalized_code_patch_versions = code_patch_versions if isinstance(code_patch_versions, list) else ([code_patch_versions] if code_patch_versions else [])

        merged_prediction = {
            **dict(prediction),
            "code_patches": normalized_code_patches,
            "code_patch_versions": normalized_code_patch_versions,
            "model_name_or_path": patch_model_name,
        }
        merged_predictions[instance_id] = merged_prediction
        print(f"[main] merged instance={instance_id} test_count={len(merged_prediction['test_patches']) if isinstance(merged_prediction.get('test_patches'), list) else 1} code_count={len(normalized_code_patches)}")
        print(
            f"[main] instance={instance_id} loaded test_patches={len(merged_prediction.get('test_patches', [])) if isinstance(merged_prediction.get('test_patches', []), list) else 1} "
            f"code_patches={len(merged_prediction.get('code_patches', []))}"
        )

    predictions = merged_predictions

    # get dataset from predictions
    dataset = get_dataset_from_preds(dataset_name, split, instance_ids, predictions, patch_model_name, log_run_id)

    full_dataset = load_tddbench_dataset(dataset_name, split, instance_ids)

    if keep_container == "kill":
        kill_dataset = dataset if dataset else full_dataset
        test_specs = list(map(make_test_spec_rebench if is_rebench else make_test_spec, kill_dataset))
        removed_containers = 0
        removed_images = 0

        for test_spec in test_specs:
            container_name = test_spec.get_instance_container_name(container_run_id)
            try:
                container = client.containers.get(container_name)
                cleanup_container(client, container, "quiet")
                removed_containers += 1
                print(f"Removed container: {container_name}")
            except Exception as e:
                if "404" in str(e) or "Not Found" in str(e) or "No such container" in str(e):
                    print(f"Container not found: {container_name}")
                    continue
                print(f"Failed to remove container {container_name}: {e}")
                continue
            remove_image(client, test_spec.instance_image_key, "quiet")
            removed_images += 1
            print(f"Requested image removal: {test_spec.instance_image_key}")

        print(
            f"Kill mode complete: removed {removed_containers} containers, "
            f"requested removal of {removed_images} instance images."
        )
        return

    for i in range(0,len(dataset)):
        test_patch_value = predictions[dataset[i]['instance_id']].get('test_patches', "")
        if isinstance(test_patch_value, list):
            dataset[i]['test_patch'] = test_patch_value[0] if test_patch_value else ""
        else:
            dataset[i]['test_patch'] = test_patch_value

    existing_images = list_images(client)
    print(f"Running {len(dataset)} unevaluated instances...")
    if not dataset:
        print("No instances to run.")
    else:
        # build environment images + run instances
        build_env_images(client, dataset, force_rebuild, max_workers, is_rebench)
        run_instances(merged_predictions, dataset, cache_level, clean, force_rebuild, max_workers, log_run_id, timeout, bool(keep_container), container_run_id, pbt_mode, is_rebench)

    # clean images + make final report
    clean_images(client, existing_images, cache_level, clean)
    make_run_report(patch_model_name, predictions, full_dataset, client, log_run_id)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--dataset_name", default="TDD_Bench.json", type=str, help="Name of dataset or path to JSON file.")
    parser.add_argument("--split", type=str, default="test", help="Split of the dataset")
    parser.add_argument("--instance_ids", nargs="+", type=str, help="Instance IDs to run (space separated)")
    parser.add_argument("--code_patch", default="gold", type=str, help="Path to patches predictions file - if 'gold', uses gold patch")
    parser.add_argument("--test_patch", type=str, help="Path to predictions file - if 'gold', uses gold predictions", required=True)
    parser.add_argument("--max_workers", type=int, default=4, help="Maximum number of workers (should be <= 75%% of CPU cores)")
    parser.add_argument("--open_file_limit", type=int, default=4096, help="Open file limit")
    parser.add_argument(
        "--timeout", type=int, default=1_800, help="Timeout (in seconds) for running tests for each instance"
        )
    parser.add_argument(
        "--force_rebuild", type=str2bool, default=False, help="Force rebuild of all images"
    )
    parser.add_argument(
        "--cache_level",
        type=str,
        choices=["none", "base", "env", "instance"],
        help="Cache level - remove images above this level",
        default="env",
    )
    # if clean is true then we remove all images that are above the cache level
    # if clean is false, we only remove images above the cache level if they don't already exist
    parser.add_argument(
        "--clean", type=str2bool, default=False, help="Clean images above cache level"
    )
    parser.add_argument("--run_id", type=str, required=True, help="Run ID - identifies the run")
    parser.add_argument(
        "--keep_container",
        type=str,
        default="false",
        choices=["true", "false", "kill"],
        help="Keep containers after evaluation ('true'/'false'), or use 'kill' to remove matching containers and exit"
    )
    parser.add_argument(
        "--output_subdir",
        type=str,
        default=None,
        help="Optional subdirectory name to store results (preserves old results from previous runs)"
    )
    parser.add_argument(
        "--pbt_mode",
        type=str,
        default="reproduction",
        choices=["combined", "regression", "reproduction"],
        help="Determines which files to write the test logs to and which logs to use for determining pass/fail."
    )
    parser.add_argument(
        "--is_rebench",
        type=str2bool,
        default=False,
        help="Use [`test_spec_rebench.py`](test-generation/Test_execution/tddbench/harness/test_spec_rebench.py) instead of [`test_spec.py`](test-generation/Test_execution/tddbench/harness/test_spec.py)."
    )

    args = parser.parse_args()
    if args.keep_container in {"true", "false"}:
        args.keep_container = str2bool(args.keep_container)

    main(**vars(args))
