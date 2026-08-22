import json
import os
from argparse import ArgumentParser
from typing import Any

from tree_sitter import Language, Parser
import tree_sitter_python as tspython

from collect_repo import collect_repo


class PythonBreakpointExtractor:
    def __init__(self) -> None:
        self.parser = Parser()
        self.language = Language(tspython.language())
        self.parser.language = self.language

    def parse_file(self, file_path: str) -> tuple[str, Any]:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source_code = f.read()
        tree = self.parser.parse(source_code.encode("utf-8"))
        return source_code, tree

    def _node_text(self, source_code: str, node) -> str:
        return source_code[node.start_byte:node.end_byte]

    def _find_child_by_type(self, node, node_type: str):
        for child in node.children:
            if child.type == node_type:
                return child
        return None

    def _find_identifier_name(self, source_code: str, node) -> str:
        for child in node.children:
            if child.type == "identifier":
                return self._node_text(source_code, child).strip()
        return ""

    def _iter_function_nodes(self, node):
        if node.type == "function_definition":
            yield node
        for child in node.children:
            yield from self._iter_function_nodes(child)

    def _iter_nodes(self, node):
        yield node
        for child in node.children:
            yield from self._iter_nodes(child)

    def _collect_return_lines(self, function_node) -> list[int]:
        return_lines = []
        for node in self._iter_nodes(function_node):
            if node.type == "return_statement":
                return_lines.append(node.start_point[0] + 1)
        return sorted(set(return_lines))

    def _collect_executable_lines(self, block_node) -> list[int]:
        executable_lines = []
        if block_node is None:
            return executable_lines

        for child in block_node.children:
            if child.type in {"comment"}:
                continue
            executable_lines.append(child.start_point[0] + 1)

        return sorted(set(executable_lines))

    def extract_function_metadata(self, file_path: str, target_function_names: list[str]) -> dict[str, dict[str, Any]]:
        source_code, tree = self.parse_file(file_path)
        root_node = tree.root_node
        target_names = set(target_function_names)
        results: dict[str, dict[str, Any]] = {}

        for func_node in self._iter_function_nodes(root_node):
            func_name = self._find_identifier_name(source_code, func_node)
            if func_name not in target_names:
                continue

            block_node = self._find_child_by_type(func_node, "block")
            definition_start_line = func_node.start_point[0] + 1
            definition_end_line = func_node.end_point[0] + 1

            if block_node is not None:
                body_start_line = block_node.start_point[0] + 1
                body_end_line = block_node.end_point[0] + 1
            else:
                body_start_line = definition_start_line
                body_end_line = definition_end_line

            executable_lines = self._collect_executable_lines(block_node)
            return_lines = self._collect_return_lines(func_node)
            begin_line = executable_lines[0] if executable_lines else body_start_line

            results[func_name] = {
                "function_name": func_name,
                "definition_start_line": definition_start_line,
                "definition_end_line": definition_end_line,
                "body_start_line": body_start_line,
                "body_end_line": body_end_line,
                "begin_line": begin_line,
                "return_lines": return_lines,
                "executable_body_lines": executable_lines,
            }

        return results


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_repo(instance: dict[str, Any]) -> str:
    collect_repo(instance)
    repo_name = instance["repo"].split("/")[-1]
    return os.path.abspath(os.path.join("..", "..", "repo", instance["instance_id"], repo_name))


def has_existing_result(output_file: str, instance_id: str) -> bool:
    if not os.path.exists(output_file):
        return False
    with open(output_file, "r", encoding="utf-8") as f:
        results = json.load(f)
    return any(item.get("instance_id") == instance_id for item in results)


def load_existing_results(output_file: str) -> list[dict[str, Any]]:
    if not os.path.exists(output_file):
        return []
    with open(output_file, "r", encoding="utf-8") as f:
        return json.load(f)


def upsert_result(existing_results: list[dict[str, Any]], instance_result: dict[str, Any]) -> list[dict[str, Any]]:
    updated = False
    for index, item in enumerate(existing_results):
        if item.get("instance_id") == instance_result.get("instance_id"):
            existing_results[index] = instance_result
            updated = True
            break
    if not updated:
        existing_results.append(instance_result)
    return existing_results


def write_results(output_file: str, results: list[dict[str, Any]]) -> None:
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)


def build_instance_breakpoint_metadata(
    dataset_by_id: dict[str, dict[str, Any]],
    extractor: PythonBreakpointExtractor,
    mapping_item: dict[str, Any],
) -> dict[str, Any]:
    instance_id = mapping_item["instance_id"]

    if instance_id not in dataset_by_id:
        return {
            "instance_id": instance_id,
            "status": "missing_dataset_entry",
            "files": {},
        }

    instance = dataset_by_id[instance_id]
    repo_root = ensure_repo(instance)
    file_results: dict[str, Any] = {}
    overall_status = "ok"

    for relative_file_path, function_names in mapping_item["file_functions"].items():
        absolute_file_path = os.path.join(repo_root, relative_file_path)

        if not os.path.exists(absolute_file_path):
            overall_status = "partial"
            file_results[relative_file_path] = {
                "status": "missing_file",
                "functions": {},
                "missing_functions": function_names,
            }
            continue

        extracted = extractor.extract_function_metadata(absolute_file_path, function_names)
        missing_functions = [name for name in function_names if name not in extracted]
        if missing_functions:
            overall_status = "partial"

        file_results[relative_file_path] = {
            "status": "ok" if not missing_functions else "partial",
            "functions": extracted,
            "missing_functions": missing_functions,
        }

    return {
        "instance_id": instance_id,
        "repo": instance["repo"],
        "base_commit": instance["base_commit"],
        "repo_root": repo_root,
        "status": overall_status,
        "files": file_results,
    }


def build_breakpoint_metadata(
    dataset: list[dict[str, Any]],
    mapping: list[dict[str, Any]],
    output_file: str,
    instance_filter: str | None = None,
    skip_existing: bool = False,
) -> list[dict[str, Any]]:
    dataset_by_id = {item["instance_id"]: item for item in dataset}
    extractor = PythonBreakpointExtractor()
    output = load_existing_results(output_file)

    for mapping_item in mapping:
        instance_id = mapping_item["instance_id"]

        if "django" not in instance_id:
            continue
        # if instance_filter and instance_id != instance_filter:
        #     continue

        print("=" * 30)
        print(f"Pre-processing instance id: {instance_id}")
        print("=" * 30)

        if skip_existing and has_existing_result(output_file, instance_id):
            print(f"Skipping {instance_id}: already present in {output_file}")
            continue

        instance_result = build_instance_breakpoint_metadata(
            dataset_by_id=dataset_by_id,
            extractor=extractor,
            mapping_item=mapping_item,
        )
        output = upsert_result(output, instance_result)
        write_results(output_file, output)
        print(f"Wrote incremental results for {instance_id} to {output_file}")

    return output


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--dataset_name", default="TDD_Bench.json", type=str, help="Path to dataset JSON file.")
    parser.add_argument(
        "--mapping_file",
        default="file_functions_mapping.json",
        type=str,
        help="Path to focal file/function mapping JSON file.",
    )
    parser.add_argument(
        "--instance_id",
        default=None,
        type=str,
        help="Optional single instance_id to process.",
    )
    parser.add_argument(
        "--output_file",
        default="focal_method_breakpoints.json",
        type=str,
        help="Path to output JSON artifact.",
    )
    parser.add_argument(
        "--skip_existing",
        action="store_true",
        help="Skip processing if the instance_id already exists in the output file.",
    )
    args = parser.parse_args()

    dataset = load_json(args.dataset_name)
    mapping = load_json(args.mapping_file)

    results = build_breakpoint_metadata(
        dataset=dataset,
        mapping=mapping,
        output_file=args.output_file,
        instance_filter=args.instance_id,
        skip_existing=args.skip_existing,
    )

    print(f"Wrote breakpoint metadata to {args.output_file}")

# Made with Bob
