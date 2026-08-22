"""
PotterLogger — structured JSON conversation logger for e-potter.py.

Output path (relative to e-potter.py's working directory):
    Test_execution/logs/run_evaluation/potter_logs/<instance_id>.json

Schema
------
{
    "instance_id": "<instance_id>",
    "attempts": {
        "attempt_0": [
            {"phase": "test_execution", "command": "...", "stdout": "...", "stderr": "...", "return_code": 0},
            {"phase": "critic",      "prompt": "...", "coverage_feedback": "...", "debug_info": "...",
                                     "llm_output": "...", "decision": "...", "explain": "...",
                                     "buggy": "...", "retrieve": "...", "look": [...]},
            {"phase": "refinement",  "prompt": "...", "coverage_feedback": "...", "debug_info": "...",
                                     "llm_output": "...", "generated_test": "...", "test_file": "..."}
        ],
        "attempt_1": [ ... ],
        ...
        "attempt_final_cleanup": [
            {"phase": "test_execution", ...}
        ]
    }
}

Each attempt key is a list so that step ordering is preserved.
Steps are appended one at a time and the file is flushed to disk after
every append, so a crash never produces a half-written or empty file.
"""

import json
import os
from pathlib import Path
from typing import Any


_POTTER_LOG_ROOT = Path("Test_execution/logs/run_evaluation/potter_logs")


class PotterLogger:
    """
    Accumulates a structured JSON conversation log for a single instance_id
    and flushes it to disk after every logged step.

    Usage
    -----
        plog = PotterLogger(instance_id)

        plog.start_attempt(0)
        plog.log_step("test_execution", command=cmd, stdout=out, stderr=err, return_code=rc)
        plog.log_step("critic", prompt=p, coverage_feedback=cov, debug_info=dbg,
                      llm_output=raw, decision=dec, explain=exp, buggy=bug,
                      retrieve=ret, look=lks)
        plog.log_step("refinement", prompt=p2, coverage_feedback=cov, debug_info=dbg,
                      llm_output=raw2, generated_test=test, test_file=fname)

        plog.start_attempt("final_cleanup")
        plog.log_step("test_execution", command=cmd, stdout=out, stderr=err, return_code=rc)
    """

    def __init__(self, instance_id: str):
        self.instance_id = instance_id
        self._log: dict[str, Any] = {
            "instance_id": instance_id,
            "container_name": None,
            "attempts": {},
        }
        self._current_attempt_key: str | None = None

        # Ensure the directory exists
        _POTTER_LOG_ROOT.mkdir(parents=True, exist_ok=True)
        self._path = _POTTER_LOG_ROOT / f"{instance_id}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_container_name(self, container_name: str) -> None:
        """Record the Docker container name at the top level and flush."""
        self._log["container_name"] = container_name
        self._flush()

    def start_attempt(self, attempt: int | str) -> None:
        """
        Mark the beginning of a new attempt block.
        Use an integer for normal attempts (produces key "attempt_0", "attempt_1", …)
        or the string "final_cleanup" for the post-loop cleanup run.
        """
        if isinstance(attempt, int):
            key = f"attempt_{attempt}"
        else:
            key = f"attempt_{attempt}"
        self._current_attempt_key = key
        self._log["attempts"][key] = []
        self._flush()

    def log_step(self, phase: str, **fields: Any) -> None:
        """
        Append a step to the current attempt block and flush to disk.

        Args:
            phase:   Step identifier, e.g. "test_execution", "critic", "refinement".
            **fields: Arbitrary key-value pairs for the step (see schema above).
        """
        if self._current_attempt_key is None:
            raise RuntimeError("Call start_attempt() before log_step().")
        entry = {"phase": phase, **fields}
        self._log["attempts"][self._current_attempt_key].append(entry)
        self._flush()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Write the full log to disk atomically (write to .tmp then rename)."""
        tmp_path = self._path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._log, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self._path)
