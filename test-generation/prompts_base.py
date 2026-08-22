"""
Prompt templates for test generation.
Contains all prompt templates used across the test generation pipeline.
"""

# Localization prompts
PROMPT_FILE_LOCALIZATION = """Suppose you are a very experienced developer. An issue has been created, and you need to choose the best possible files (both focal and test files) to make the changes. You will be given the following pieces of information. Please write the file names after the "Answer:" token.
	
    1. Repository name: <REPO_NAME>

    2. Issue Description: <ISSUE DESCRIPTION>
    
    3. List of files that will be updated or added to address the pull request:

<LIST>

Please write the 50 most suitable file names from the list above based on the issue description. Write one file name per line. Each file name should contain the complete path to the file as presented in item 3. Do not add any numbers, indices, or explanations. Please do not hallucinate file names.

Answer: """

PROMPT_FILE_REDUCE = """Suppose you are a very experienced developer. An issue has been created, and you need to choose the best possible files and functions (both focal and test files) to make the changes. You will be given the following pieces of information. Please write the file name and function name after the "Answer:" token.
	
    1. Repository name: <REPO_NAME>

    2. Issue Description: <ISSUE DESCRIPTION>
    
    3. List of files and functions that will be updated or added to address the pull request:

<LIST>

Please write the most suitable program files and relevant function names based on the issue description. You may choose multiple functions from different files, but keep the list as short as possible.

The filename should be written between the <Filename> and </Filename> tags. The filename should contain the complete path to the file as presented in item 3.
The function name should be written between the <Function> and </Function> tags. Do not hallucinate function names. Select them from the given list.

Each line should start with a file name followed by the function names in that file. Please write the function names from the same file on one line. Please do not hallucinate file names or function names.

Answer: """

# Init plan prompt
PROMPT_INIT_PLAN = """Suppose you are a very experienced developer. An issue has been created, and you need to write a test to ensure the issue has been resolved. You will be given the following pieces of information.
	
    1. Repository name: <REPO_NAME>
    
    2. Issue Description: <ISSUE DESCRIPTION>

    3. Program files and functions that will be updated or added to address the issue:

<LIST1>

Now create an action list to write a fail-to-pass test. You may read the necessary test and focal functions to complete the task. You may only perform read actions.

Read: You can read a focal function or test from a given file. You can read only the current version of the focal function(s) or test(s).

Each line should contain one action, one file name, and one focal function or test.

The action should be written between the <Action> and </Action> tags. Nothing should be added apart from the action tokens: "Read", "Modify", and "Write".
The filename should be written between the <Filename> and </Filename> tags. The filename should contain the complete path to the file as presented in item 3.
The focal function or test name should be written between the <Function> and </Function> tags.

Example format:

<Action>Read</Action> <Filename>file1</Filename> <Function>func1</Function>

Please do not repeat the same action for the same file and focal function or test pair.

Answer: """

# Generate prompts
PROMPT_IMPORTS = """Suppose you are a very experienced developer. An issue has been created, and you need to write a test file with a fail-to-pass test to ensure the issue has been resolved. We share the following information.
  
    1. Repository name: <REPO_NAME>
    
    2. Issue Description: <ISSUE DESCRIPTION>
    
    3. Relevant Function:
    
<FUNCTION>

    4. Imports:
    
<Imports>

Write the necessary imports for the test file between the <IMPORTS> and </IMPORTS> tags. Please select the imports from item 4. Your test file should contain only the fail-to-pass test that will fail on the current repository and pass after the issue is addressed. Keep the test file as small as possible. Keep the import list as small as possible.

Answer:"""

PROMPT_FILE_LOCATION = """Suppose you are a very experienced developer. An issue has been created, and you need to write a test file with a fail-to-pass test to ensure the issue has been resolved. We share the following information.
  
    1. Repository name: <REPO_NAME>
    
    2. Issue Description: <ISSUE DESCRIPTION>
    
    3. Relevant Function:
    
<FUNCTION>

    4. Imports:
    
<IMPORT>

    5. Locations:

<Location>

Write the location for the test file between the <LOCATION> and </LOCATION> tags. Please select the location from item 5.

Answer:"""

