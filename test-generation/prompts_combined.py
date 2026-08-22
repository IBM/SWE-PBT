"""
Prompt templates for test generation.
Contains all prompt templates used across the test generation pipeline.
"""

# ---------------------------------------------------------------------------
# Shared constants — used in both PROMPT_TEST_FILE and PROMPT_REWRITE below
# ---------------------------------------------------------------------------

_PBT_REQUIREMENTS = """\
1. When `is_regression` is "true", the generated property-based test MUST pass on the buggy version of the repository and MUST pass on the fixed version of the repository. The regression branch MUST identify and capture the already correct behavior with respect to the issue description.
2. When `is_regression` is "false", the generated property-based test MUST fail on the buggy version of the repository and MUST pass on the fixed version of the repository. The reproduction branch MUST identify and capture the buggy behavior with respect to the issue description.
3. In the standalone test script, helper function names MUST NOT contain the word "test".
4. If the test contains any database transactions, you MUST add the necessary pytest decorators or project-specific context managers to ensure that transactions are atomic.
5. NEVER create local "simulate_buggy_*()" or "simulate_fixed_*()" helper functions that replicate the behavior of the function under test.
   - CORRECT:   `result = target_module.the_function(drawn_input)` — call the real installed library.
   - INCORRECT: `def simulate_the_function(x): return x + wrong_offset` — this hardcodes the bug and bypasses the library entirely.
   The test MUST import and call the actual function from the installed library. The pass/fail outcome MUST be determined solely by the installed library's behavior, not by any locally hardcoded reimplementation of the bug or its fix.
6. Each input from the hypothesis generator MUST run on a fresh version of the test. Any fixture object, log, database tables, filesystem resources, or any stale object from older inputs MUST be cleared before running the test on a new input. Use a try/finally block to guarantee cleanup even on failure.
7. If the test needs to create any temporary file or temporary directory, it MUST use Python's standard library `tempfile` context manager (e.g., `tempfile.NamedTemporaryFile`, `tempfile.TemporaryDirectory`). Do NOT create temporary files or directories manually using `open()`, `os.mkdir()`, or any other mechanism.
8. If 200 examples cause TimeOut or TimeLimitExceeded errors, successively halve the number of `max_examples` in the `@hypothesis_settings` decorator.\
"""

_PBT_TEMPLATE = """\
```
import sys
from hypothesis import given
from hypothesis import strategies as st
from hypothesis import settings as hypothesis_settings
from hypothesis import HealthCheck

# Helper functions (ONLY for test setup/teardown and data construction —
# NOT for reimplementing library logic):
def setup_state():
    ...  # e.g., create a temp file, initialise a DB row

def teardown_state(state):
    ...  # e.g., delete temp file, roll back DB row

# Property-based test:
@given(data=st.data())
@hypothesis_settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow, HealthCheck.filter_too_much])
def test(data):
    import os
    is_regression = os.environ.get("IS_REGRESSION")
    if is_regression is None:
        print("Error: IS_REGRESSION environment variable is not defined.")
        sys.exit(1)

    # --- Setup ---
    state = setup_state()
    try:
        if is_regression == "true":
            # Regression test logic:
            # Step 1: Draw inputs — use data.draw / assume / filter to pick values that asserts the already correct behavior
            # Step 2: Call the REAL function — result = actual_library_function(drawn_inputs)
            #         NEVER call a local simulate_*() function here.
            # Step 3: Do NOT assert the behavior of the specific bug path itself.
            #         This assertion MUST already pass on the buggy repository and MUST continue
            #         to pass after the fix is applied.
            # MUST PASS on buggy repository AND PASS on fixed repository
        elif is_regression == "false":
            # Reproduction test logic:
            # Step 1: Draw inputs — use data.draw / assume / filter to pick values
            #         that exercise the bug path described in the issue.
            # Step 2: Call the REAL function — result = actual_library_function(drawn_inputs)
            #         NEVER call a local simulate_*() function here.
            # Step 3: Assert the buggy behavior — derive the assertion directly from
            #         the "Actual" vs "Expected" contrast in the issue description.
            #         e.g.: assert result == <Expected_from_issue>
            #         This assertion MUST fail when the library returns the buggy <Actual> value
            #         and MUST pass once the library is fixed.
            # MUST FAIL on buggy repository but PASS on fixed repository
    finally:
        teardown_state(state)  # Always clean up, even on failure

# Entry point:
if __name__ == "__main__":
    import pytest
    pytest.main([__file__, '--hypothesis-verbosity=verbose', '--hypothesis-log-all', '-vs'])
```\
"""


PROMPT_TEST_FILE = """Suppose you are a very experienced developer. An issue has been created.

Generate a property-based test using the Python Hypothesis framework for reproducing the bug in the following problem statement. We share the following information.

    1. Repository name: <REPO_NAME>

    2. Issue Description: <ISSUE DESCRIPTION>

    3. Relevant Function:

<FUNCTION>

    4. Imports:

<IMPORT>

    5. Locations:

<LOCATION>


Before writing the test, identify the following from the issue description:
  - What input (or class of inputs) triggers the bug?
  - What does the buggy code return or do (the "Actual" behavior in the issue)?
  - What should the fixed code return or do (the "Expected" behavior in the issue)?
  - What is the bug path (the specific code path that is broken)?
  - What behavior does the buggy repository already handle correctly (unrelated to the bug path)?

Test file writing rules:
- Write the test file name between the <FILE> and </FILE> tags. The new test file should be created at the location: `pbt/test.py`.
- Write the complete test file, containing only one property-based test, between the <COMPLETE_TEST> and </COMPLETE_TEST> tags.
- Use the given package and imports. You may update the imports (remove or add them) and make the necessary changes to avoid compilation errors. Avoid circular imports by all means.
- If the issue description contains a proof-of-concept reproducer, use the following decision framework for gathering reference:
    (i) If the given proof-of-concept example is sufficient and faithful, use it.
    (ii) If the given relevant functions are more faithful, prioritize them over given examples.
    (iii) If both the given proof-of-concept example and the given focal functions are relevant, use both as references.
- Do not write additional tests or other functions. Write only one property-based test and keep the test file as small as possible.
- The generated property-based test MUST be inside a standalone test script.

- The generated standalone property-based test MUST follow the template given below:
<PROPERTY_BASED_TEST_TEMPLATE>
""" + _PBT_TEMPLATE + """
</PROPERTY_BASED_TEST_TEMPLATE>

- Your generated property-based test file MUST satisfy the following requirements:
<PROPERTY_BASED_TEST_REQUIREMENTS>
""" + _PBT_REQUIREMENTS + """
</PROPERTY_BASED_TEST_REQUIREMENTS>

Answer:"""

PROMPT_REWRITE = """Suppose you are a very experienced developer. An issue has been created, and you need to address it. You have written a fail-to-pass test that should fail on the current code base and will pass on the new code base after the issue has been addressed. We share the following information.

    1. Issue Description:

<ISSUE DESCRIPTION>

    2. Test:

<TEST>

    3. Logs on current code base:

<LOGBEFORE>

    4. Buggy lines that should not be repeated in the new test:

<BUGGY>

    5. Retrieved line from issue that may help to avoid the bug:

<RETR>

    6. Relevant functions (before addressing the issue) that you wanted to see to solve the bug:

<LOOK>

The test is not failing for the right reason on the current code base. Please modify the test to fail for the right reason on the old code base and pass on the new code base after addressing the issue. Do not write any explanation or add any class within the tags. Write down the complete function using the following format.

Before writing the revised test, re-identify the following from the issue description:
  - What input (or class of inputs) triggers the bug?
  - What does the buggy code return or do (the "Actual" behavior in the issue)?
  - What should the fixed code return or do (the "Expected" behavior in the issue)?
  - What is the bug path (the specific code path that is broken)?
  - What behavior does the buggy repository already handle correctly (unrelated to the bug path)?

Test file writing rules:
- Write the test file name between the <FILE> and </FILE> tags. You can copy and paste it from the given test diff in 2.
- Write the complete test file, containing only one property-based test, between the <COMPLETE_TEST> and </COMPLETE_TEST> tags.
- Use the given package and imports. You may update the imports (remove or add them) and make the necessary changes to avoid compilation errors. Avoid circular imports by all means.
- If the issue description contains a proof-of-concept reproducer, use the following decision framework for gathering reference:
    (i) If the given proof-of-concept example is sufficient and faithful, use it.
    (ii) If the given relevant functions are more faithful, prioritize them over given examples.
    (iii) If both the given proof-of-concept example and the given focal functions are relevant, use both as references.
- Do not write additional tests or other functions. Write only one compilable property-based test and keep the test file as small as possible.
- The generated property-based test MUST be inside a standalone test script.

- The generated standalone property-based test MUST follow the template given below:
<PROPERTY_BASED_TEST_TEMPLATE>
""" + _PBT_TEMPLATE + """
</PROPERTY_BASED_TEST_TEMPLATE>

- Your generated property-based test file MUST satisfy the following requirements:
<PROPERTY_BASED_TEST_REQUIREMENTS>
""" + _PBT_REQUIREMENTS + """
</PROPERTY_BASED_TEST_REQUIREMENTS>

<COVERAGE FEEDBACK>

<DEBUGINFO>

Answer:"""
