from argparse import ArgumentParser
import pandas as pd
import os
import json

from collect_repo import collect_repo
from preprocessing import preprocessing_instance
from localization import localization
from init_plan import init_plan
from generate import generate
from generate_git_diff import generate_git_diff


if __name__ == "__main__":
    parser = ArgumentParser()

    parser.add_argument("--dataset_name", default="TDD_Bench.json", type=str, help="Path to json/csv file.")
    parser.add_argument("--instance_id", type=str, help="Instance ID to run")
    parser.add_argument("--model", default="claude", type=str, help="model to generate test")
    parser.add_argument("--version", default="base", type=str, help="version name of the test")
    parser.add_argument("--pbt_mode", type=str, default="reproduction", choices=["combined", "regression", "reproduction"], help="Determines which files to write the test logs to and which logs to use for determining pass/fail.")
    parser.add_argument("--output_file", default="potter_test.json", type=str, help="Path to the output JSON file.")

    args = parser.parse_args()


    instance_id = args.instance_id

    # Detect file format and load data accordingly
    file_extension = os.path.splitext(args.dataset_name)[1].lower()
    
    if file_extension == '.json':
        # Load JSON file
        with open(args.dataset_name, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Find the instance with matching instance_id
        instance = None
        for item in data:
            if item.get("instance_id") == instance_id:
                instance = item
                break
        
        if instance is None:
            raise ValueError(f"Instance ID '{instance_id}' not found in {args.dataset_name}")
    
    elif file_extension == '.csv':
        # Load CSV file
        df = pd.read_csv(args.dataset_name)
        instance = df[df["instance_id"] == instance_id]
        
        if instance.empty:
            raise ValueError(f"Instance ID '{instance_id}' not found in {args.dataset_name}")
        
        instance = instance.to_dict(orient="records")[0]
    
    else:
        raise ValueError(f"Unsupported file format: {file_extension}. Please use .csv or .json files.")

    #collect the repo
    name=instance["repo"].split("/")[-1]
    folder_name=instance["instance_id"]

    if os.path.exists("../../repo/"+folder_name+"/"+name)==False:
        print("Cloning the repo ...")
        collect_repo(instance)
    else:
        print("Repo already exists")


    if os.path.exists("preprocessed_data") == False:
        os.mkdir("preprocessed_data")
    
    if os.path.exists("preprocessed_data/" + instance_id) == False:
        os.mkdir("preprocessed_data/" + instance_id)
        print("preprocessing the data")
        preprocessing_instance(instance)
    else:
        print("Data is already preprocessed ...")

    if os.path.exists("model_output") == False:
        os.mkdir("model_output") 

    
    # =====================================================
    # Comment out to avoid regenerating and to prevent overwriting the manually over-ridden golden localization
    # =====================================================
    print("Running localization ...")
    localization(instance, args.model, args.version, "model_output")
    # =====================================================

    print("Running init_plan ...")
    init_plan(instance, args.model, args.version, "model_output")
    
    # =====================================================
    # Dynamic prompts for regression/reproduction/combined
    # =====================================================
    print("Running generate ...")
    if args.pbt_mode == "regression":
        generate(instance, args.model, args.version, "model_output", pbt_mode="regression")
    elif args.pbt_mode == "reproduction":
        generate(instance, args.model, args.version, "model_output", pbt_mode="reproduction")
    elif args.pbt_mode == "combined":
        generate(instance, args.model, args.version, "model_output", pbt_mode="combined")
    # =====================================================
    
    print("Generate test diff ...")
    test_diff = generate_git_diff(instance, args.model, args.version, "model_output", forced_filename="pbt/test.py")
    
    # Save git diff to output.json
    output_file = args.output_file
    
    # Read existing data if file exists
    if os.path.isfile(output_file):
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            data = []
    else:
        data = []
    
    # Append new entry
    entry = {
        "instance_id": instance_id,
        "model_patch": test_diff
    }
    data.append(entry)
    
    # Write back to file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
    
    print(f"Git diff saved to {output_file}")
        



 

  
