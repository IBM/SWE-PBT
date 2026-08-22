import git
import os

def clone_and_checkout(name, repo_url, commit_hash, repo_path):
    try:
        if os.path.exists(repo_path):
            repo = git.Repo(repo_path)
            repo.git.fetch("--all", "--tags", "--force")
        else:
            repo = git.Repo.clone_from(repo_url, repo_path)
            repo.git.fetch("--all", "--tags", "--force")
        repo.git.checkout(commit_hash)
        return repo
    except git.exc.GitCommandError as e:
        print(f"Error: {e}")
        return None
    

def collect_repo(item):

    name=item['repo'].split("/")[-1]
    folder_name=item['instance_id']

    repo=item['repo']
    repo="https://github.com/"+repo+".git"
    commit_hash=item['base_commit']


    if os.path.exists("../../repo")==False:
        os.mkdir("../../repo")

    if os.path.exists("../../repo/"+folder_name)==False:
        os.mkdir("../../repo/"+folder_name)
   
    repo_path = '../../repo/'+folder_name+"/"+name
    repo_result=clone_and_checkout(name, repo, commit_hash,repo_path)


    

