import json
import os
import subprocess
import git
import shutil


def clone_and_checkout(name, repo_url, commit_hash, repo_path):
    """Clone repository and checkout specific commit"""
    try:
        if os.path.exists(repo_path):
            repo = git.Repo(repo_path)
        else:
            repo = git.Repo.clone_from(repo_url, repo_path)
        repo.git.checkout(commit_hash)
        return repo
    except git.exc.GitCommandError as e:
        print(f"Error: {e}")
        return None


def get_git_diff_new_file(file_path, filename):
    """Generate git diff for a new file"""
    store = os.getcwd()
    os.chdir(file_path)
    git_command = ["git", "diff", "--no-index", "/dev/null", filename]
    
    try:
        output = subprocess.run(git_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.chdir(store)
        return output.stdout
    except Exception as e:
        print(f"Error: {e}")
        os.chdir(store)
        return None


def git_reset(file_path):
    """Reset git repository to clean state"""
    store = os.getcwd()
    os.chdir(file_path)
    
    try:
        # Clean untracked files
        subprocess.check_output("git clean -fd", shell=True)
        # Reset to HEAD
        subprocess.check_output("git reset --hard", shell=True)
        os.chdir(store)
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        os.chdir(store)
        return None


def delete_folder(folder_path):
    """Delete a folder and its contents"""
    try:
        if os.path.exists(folder_path):
            if os.path.isdir(folder_path):
                shutil.rmtree(folder_path)
                print(f"Folder '{folder_path}' deleted successfully.")
            else:
                raise NotADirectoryError(f"'{folder_path}' is not a directory.")
    except Exception as e:
        print(f"Error deleting folder: {e}")


def generate_git_diff(instance, model, version, output_dir, istemp=False, forced_filename=None):
    """
    Generate git diff for a test file

    Args:
        instance: Dictionary containing instance information
        model: Model name used for generation
        version: Version identifier
        output_dir: Directory containing model outputs
        forced_filename: If set, always use this path as the diff target instead of
            trusting the LLM's self-reported 'file'/'classname' fields. The harness on
            the consuming side (mini-swe-agent's PBT setup, the prompt template, the
            hardcoded pytest invocation) all assume a fixed path -- letting the model
            pick its own filename occasionally produces a diff that applies cleanly but
            lands at the wrong path, which then looks like the test file is missing.

    Returns:
        Git diff string or None if error
    """
    instance_id = instance['instance_id']
    
    # Read the generated test file
    if istemp==False:
        test_file_path = f"{output_dir}/version_5_{model}_{version}/{instance_id}.json"
    else:
        test_file_path = f"{output_dir}/version_5_{model}_{version}/{instance_id}_temp.json"
    
    if not os.path.exists(test_file_path):
        print(f"Error: Test file not found at {test_file_path}")
        return None
    
    try:
        with open(test_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading test file: {e}")
        return None
    
    # Extract repository information
    name = instance['repo'].split("/")[-1]
    folder_name = instance['instance_id']
    repo_url = "https://github.com/" + instance['repo'] + ".git"
    commit_hash = instance['base_commit']
    repo_path = f'../../repo/{folder_name}/{name}'
    
    print(f"Processing instance: {instance_id}")
    print(f"Repository: {repo_url}")
    print(f"Commit: {commit_hash}")
    
    # Clone and checkout repository (or use existing)
    # If repo exists, reset it first to ensure clean state
    if os.path.exists(repo_path):
        print(f"Repository already exists at {repo_path}, resetting...")
        git_reset(repo_path)
    
    repo_result = clone_and_checkout(name, repo_url, commit_hash, repo_path)
    
    if repo_result is None:
        print("Error: Failed to clone/checkout repository")
        return None
    
    # Extract test file information
    test_content = data.get('function', '')
    classname = data.get('classname', '')
    filename = forced_filename if forced_filename is not None else data.get('file', classname)
    
    # Ensure filename has .py extension for Python files
    if not filename.endswith('.py'):
        filename = filename + '.py'
    
    # Construct full file path
    full_file_path = os.path.join(repo_path, filename)
    
    # Create directory if it doesn't exist
    file_dir = os.path.dirname(full_file_path)
    if file_dir and not os.path.exists(file_dir):
        os.makedirs(file_dir, exist_ok=True)
        print(f"Created directory: {file_dir}")
    
    # Delete the old test file to avoid generating reverse/modification patches instead of create file patch
    if os.path.exists(full_file_path):
        os.remove(full_file_path)
        print(f"Deleted existing test file: {full_file_path}")

    # Write the test file
    try:
        with open(full_file_path, 'w', encoding='utf-8') as f:
            f.write(test_content)
        print(f"Test file written to: {full_file_path}")
    except Exception as e:
        print(f"Error writing test file: {e}")
        return None
    
    # Generate git diff
    git_diff = get_git_diff_new_file(repo_path, filename)
    
    if git_diff:
        print("Git diff generated successfully")
        print("=" * 80)
        print(git_diff)
        print("=" * 80)
    else:
        print("Warning: Git diff generation failed")
    
    # Reset repository to clean state
    git_reset(repo_path)

    print("[generate_git_diff]:")
    print(git_diff)
    
    return git_diff


# Made with Bob
