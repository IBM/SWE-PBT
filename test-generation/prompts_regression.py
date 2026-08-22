"""
Prompt templates for test generation.
Contains all prompt templates used across the test generation pipeline.
"""

# ---------------------------------------------------------------------------
# Shared constants — used in both PROMPT_TEST_FILE and PROMPT_REWRITE below
# ---------------------------------------------------------------------------

_PBT_REQUIREMENTS = """\
1. The generated standalone property-based test MUST capture the already correct behavior of the buggy code base so that we can prevent regressions later.
2. The generated standalone property-based test MUST pass on the buggy code base and also MUST pass on the fixed code base.
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
    # --- Setup ---
    state = setup_state()
    try:
        # Regression test logic:
        # Step 1: Draw inputs — use data.draw / assume / filter to pick values that asserts the already correct behavior
        # Step 2: Call the REAL function — result = actual_library_function(drawn_inputs)
        #         NEVER call a local simulate_*() function here.
        # Step 3: Do NOT assert the behavior of the specific bug path itself.
        #         This assertion MUST already pass on the buggy repository and MUST continue
        #         to pass after the fix is applied.
        # MUST PASS on buggy repository AND PASS on fixed repository
    finally:
        teardown_state(state)  # Always clean up, even on failure

# Entry point:
if __name__ == "__main__":
    import pytest
    pytest.main([__file__, '--hypothesis-verbosity=verbose', '--hypothesis-log-all', '-vs'])
```\
"""


PROMPT_TEST_FILE = """Suppose you are a very experienced developer. An issue has been created.

Generate a property-based test using the Python Hypothesis framework to test for regressions related to the bug in the following problem statement. We share the following information.

    1. Repository name: <REPO_NAME>

    2. Issue Description: <ISSUE DESCRIPTION>

    3. Relevant Function:

<FUNCTION>

    4. Imports:

<IMPORT>

    5. Locations:

<LOCATION>


Before writing the test, identify the following from the issue description:
  - What is the bug path (the specific input or code path that is broken)?
  - What behavior does the buggy repository already handle correctly (unrelated to the bug path)?

Test file writing rules:
- Write the test file name between the <FILE> and </FILE> tags. The new test file should be created at the location: `pbt/test.py`.
- Write the complete test file, containing only one pass-to-pass property-based test, between the <COMPLETE_TEST> and </COMPLETE_TEST> tags.
- Use the given package and imports. You may update the imports (remove or add them) and make the necessary changes to avoid compilation errors. Avoid circular imports by all means.
- If the issue description contains a proof-of-concept reproducer, use the following decision framework for gathering reference:
    (i) If the given proof-of-concept example is sufficient and faithful, use it.
    (ii) If the given relevant functions are more faithful, prioritize them over given examples.
    (iii) If both the given proof-of-concept example and the given focal functions are relevant, use both as references.
- Do not write additional tests or other functions. Write only one pass-to-pass property-based test and keep the test file as small as possible.
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


# Decision and rewrite prompts
PROMPT_DECISION = """Suppose you are a very experienced developer. An issue has been created, and you need to address it. You have written a pass-to-pass test that should pass on the current code base and will also pass on the new code base after the issue has been addressed. We share the following information.

    1. Issue Description:

<ISSUE DESCRIPTION>

    2. Test:

<TEST>

    3. Logs on current code base:

<LOGBEFORE>


Based on the issue description and log, do you think the test is passing on the current code base?

If the test is passing on the existing code base, the decision must be "yes". If the test is failing for any reason, the decision must be "no". The decision should be written between the <DECISION> and </DECISION> tags.

If the decision is "yes", no further action is needed. However, if the decision is "no", please collect the information mentioned below.

Writing rules:

1. Explain the bug within the <Explain> and </Explain> tags.
2. Include the failing line within the <Buggy> and </Buggy> tags.
3. Choose the most relevant lines (program statement if available) from the issue within the <Retrieve> and </Retrieve> tags that may fix the bug. Do not modify this line.
4. Mention the function name that you need to see to avoid the bug. The function name should be written between the <Look> and </Look> tags.

<DEBUGINFO>

Answer:"""

PROMPT_REWRITE = """Suppose you are a very experienced developer. An issue has been created, and you need to address it. You have written a pass-to-pass test that should pass on the current code base and will also pass on the new code base after the issue has been addressed. We share the following information.

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

The test is not passing on the current code base. Please modify the test to pass on the old code base and also pass on the new code base after addressing the issue. Do not write any explanation or add any class within the tags. Write down the complete function using the following format.

Before writing the revised test, re-identify the following from the issue description:
  - What is the bug path (the specific input or code path that is broken)?
  - What behavior does the buggy repository already handle correctly (unrelated to the bug path)?

Test file writing rules:
- Write the test file name between the <FILE> and </FILE> tags. You can copy and paste it from the given test diff in 2.
- Write the complete test file, containing only one pass-to-pass property-based test, between the <COMPLETE_TEST> and </COMPLETE_TEST> tags.
- Use the given package and imports. You may update the imports (remove or add them) and make the necessary changes to avoid compilation errors. Avoid circular imports by all means.
- If the issue description contains a proof-of-concept reproducer, use the following decision framework for gathering reference:
    (i) If the given proof-of-concept example is sufficient and faithful, use it.
    (ii) If the given relevant functions are more faithful, prioritize them over given examples.
    (iii) If both the given proof-of-concept example and the given focal functions are relevant, use both as references.
- Do not write additional tests or other functions. Write only one compilable pass-to-pass property-based test and keep the test file as small as possible.

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
