# conftest.py
import pytest
import json
from pathlib import Path
from datetime import datetime, timezone
import hashlib
try:
    from hypothesis.errors import FlakyFailure
except ImportError:
    FlakyFailure = None

class HypothesisLogger:
    def __init__(self):
        self.test_data = {}
        self.current_test = None
        self.summary_file = Path('/testbed/pbt/hypothesis_summary.json')
        self.seen_examples = set()
        
    def start_test(self, test_id):
        self.current_test = test_id
        self.seen_examples.clear()
        self.test_data[test_id] = {
            'test_id': test_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'examples': [],
            'passing_count': 0,
            'failing_count': 0,
            'status': 'running'
        }
    
    def _example_hash(self, args, kwargs):
        """Create a hash to detect duplicate examples"""
        example_str = f"{args}_{kwargs}"
        return hashlib.md5(example_str.encode()).hexdigest()
    
    def _make_json_serializable(self, obj, depth=0, max_depth=3):
        """Convert objects to JSON-serializable format, recursively extracting attributes"""
        # Prevent infinite recursion
        if depth > max_depth:
            return f"<{type(obj).__name__}: max depth reached>"
        
        # First check if it's already JSON serializable
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        
        if isinstance(obj, bytes):
            return f"<bytes: {obj[:50]!r}{'...' if len(obj) > 50 else ''}>"
        elif isinstance(obj, (list, tuple)):
            return [self._make_json_serializable(item, depth + 1, max_depth) for item in obj]
        elif isinstance(obj, dict):
            return {str(k): self._make_json_serializable(v, depth + 1, max_depth) for k, v in obj.items()}
        else:
            # For custom objects, try to extract their attributes
            try:
                # Get object's __dict__ if available (works for most custom classes)
                if hasattr(obj, '__dict__'):
                    obj_dict = {
                        '__type__': type(obj).__name__,
                        '__str__': str(obj)[:100]
                    }
                    # Extract attributes, avoiding private/magic attributes and methods
                    for key, value in obj.__dict__.items():
                        if not key.startswith('_') and not callable(value):
                            try:
                                obj_dict[key] = self._make_json_serializable(value, depth + 1, max_depth)  # type: ignore
                            except Exception:
                                obj_dict[key] = f"<error extracting {key}>"
                    return obj_dict
                else:
                    # Fallback to string representation
                    obj_str = str(obj)
                    if len(obj_str) > 100:
                        obj_str = obj_str[:100] + "..."
                    return f"<{type(obj).__name__}: {obj_str}>"
            except Exception as e:
                # If extraction fails, just use the type name
                return f"<{type(obj).__name__} object: extraction failed>"
    
    def log_example(self, args, kwargs, outcome, error=None):
        if self.current_test:
            # Skip duplicates
            example_hash = self._example_hash(args, kwargs)
            if example_hash in self.seen_examples:
                return
            
            self.seen_examples.add(example_hash)
            
            example = {
                'args': self._make_json_serializable(list(args)),
                'kwargs': self._make_json_serializable(dict(kwargs)),
                'outcome': outcome
            }
            if error:
                example['error'] = str(error)
                example['error_type'] = type(error).__name__
            
            self.test_data[self.current_test]['examples'].append(example)
            
            if outcome == 'passed':
                self.test_data[self.current_test]['passing_count'] += 1
            else:
                self.test_data[self.current_test]['failing_count'] += 1
    
    def finish_test(self, test_id, outcome):
        if test_id in self.test_data:
            self.test_data[test_id]['status'] = outcome
            self.test_data[test_id]['end_time'] = datetime.now(timezone.utc).isoformat()
    
    def save_summary(self):
        """Save all test data to summary file"""
        with open(self.summary_file, 'w') as f:
            json.dump(self.test_data, f, indent=2)

hypothesis_logger = HypothesisLogger()

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Hook into test execution to wrap Hypothesis tests"""
    is_hypothesis_test = (
        hasattr(item, 'obj') and
        hasattr(item.obj, 'hypothesis')
    )
    
    if is_hypothesis_test:
        hypothesis_logger.start_test(item.nodeid)
        original_test = item.obj.hypothesis.inner_test

        def logging_wrapper(*args, **kwargs):
            # # Commenting out to prevent side-effects or overfitting
            # # Reset any function-scoped fixtures (e.g. caplog) that accumulate
            # # state across Hypothesis example iterations.  Without this, log
            # # records from iteration N bleed into iteration N+1, causing
            # # Hypothesis to see a different failing assertion during its
            # # shrinking replay and raise FlakyFailure instead of AssertionError.
            # for value in kwargs.values():
            #     if hasattr(value, 'clear') and hasattr(value, 'records'):
            #         # caplog-like fixture: clear accumulated log records
            #         value.clear()

            try:
                result = original_test(*args, **kwargs)
                hypothesis_logger.log_example(args, kwargs, 'passed')
                return result
            except Exception as e:
                hypothesis_logger.log_example(args, kwargs, 'failed', error=e)
                # Always re-raise so Hypothesis sees the failure, runs its
                # shrinker, and prints the full "Falsifying example:" block
                # with drawn values. The conftest observes outcome passively.
                raise

        item.obj.hypothesis.inner_test = logging_wrapper

    outcome = yield

    if is_hypothesis_test:
        # If Hypothesis raised FlakyFailure, recover the underlying AssertionError
        # from its sub-exceptions and record it so the summary reflects the real
        # cause rather than the shrinking artefact.
        if (
            FlakyFailure is not None
            and outcome.excinfo is not None
            and isinstance(outcome.excinfo[1], FlakyFailure)
        ):
            flaky_exc = outcome.excinfo[1]
            # FlakyFailure stores the sub-exceptions that triggered it; the first
            # one is the original failure found during exploration.
            sub_exceptions = getattr(flaky_exc, 'exceptions', None) or []
            root_cause = sub_exceptions[0] if sub_exceptions else flaky_exc
            hypothesis_logger.log_example((), {}, 'failed', error=root_cause)

        result = 'passed' if not outcome.excinfo else 'failed'
        num_failures = hypothesis_logger.test_data.get(item.nodeid, {}).get('failing_count', 0)
        if num_failures:
            print(f"\n[WARN] Found {num_failures} failures")
        hypothesis_logger.finish_test(item.nodeid, result)

def pytest_sessionfinish(session, exitstatus):
    """Save summary after all tests complete"""
    hypothesis_logger.save_summary()
    total = sum(len(test['examples']) for test in hypothesis_logger.test_data.values())
    print(f"\n[OK] Hypothesis summary saved to {hypothesis_logger.summary_file}")
    print(f"[OK] Total examples logged: {total}")
    for test_id, data in hypothesis_logger.test_data.items():
        print(f"  {test_id}: {data['passing_count']} passed, {data['failing_count']} failed")

def pytest_addoption(parser):
    """Add command-line options"""
    try:
        parser.addoption(
            "--hypothesis-summary",
            action="store",
            default="/testbed/pbt/hypothesis_summary.json",
            help="Path to save Hypothesis summary"
        )

        # Kept for backwards compatibility with existing test commands that
        # pass --hypothesis-log-all; the flag is now a no-op because Hypothesis
        # always handles its own failure reporting and shrinking.
        parser.addoption(
            "--hypothesis-log-all",
            action="store_true",
            default=False,
            help="(no-op) Retained for CLI compatibility."
        )

    except ValueError:
        pass

def pytest_configure(config):
    """Configure logger with command-line options"""
    hypothesis_logger.summary_file = Path("/testbed/pbt/hypothesis_summary.json")
