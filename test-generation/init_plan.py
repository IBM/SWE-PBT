import os
from typing import Optional
import time
import json
import argparse
import ast
import re
import pandas as pd
from utility import generate_text
from prompts_base import PROMPT_INIT_PLAN


def init_plan(instance, model, version, output_dir):
    instance_id = instance["instance_id"]

    with open(output_dir+"/"+instance_id+"_localization_"+model+"_"+version+".json","r") as f:
        local=json.load(f)

    output_file=output_dir+"/"+instance_id+"_init_plan_"+model+"_"+version+".json"   

    with open("preprocessed_data/"+instance_id+"/"+"method_bodies.json","r") as f:
        function_body_preprocess=json.load(f) 
    
    prompt = PROMPT_INIT_PLAN


    focus={}
    for i in range(len(local)):
        sample=local[i]
        if sample['instance_id']==instance_id:
            focus=sample["file_function"]
            break


    issue_des=""    

    if version=="base":
        issue_des = instance['problem_statement']
                

    prompt = prompt.replace("<REPO_NAME>", instance['repo'])
    prompt = prompt.replace("<ISSUE DESCRIPTION>", issue_des) #temp['problem_statement'])

    p=""

    for key in focus:
        try:
            c=0
            for key1 in focus[key]:
                p=p+"File: "+key+"\n"
                p=p+function_body_preprocess[key][key1]+"\n"
                c=c+1
            p=p.strip()+"\n\n" 
        except:
            continue        


    prompt = prompt.replace("<LIST1>", p.strip())  


    generated_patch = generate_text(prompt, model=model, max_tokens=4096)

    try:
        generated_patch=generated_patch.replace("\n"," ")
        generated_patch=generated_patch.replace("<Action>","\n<Action>").strip()
    except Exception as e:
        print(f"Error processing generated patch: {e}")
        generated_patch = generated_patch.strip() if generated_patch else ""


    data = []
    temp = {}
    temp["instance_id"] = instance_id
    temp["generated"] = generated_patch
    temp["prompt"] = prompt
    data.append(temp)

    with open(output_file, 'w') as file:
        json.dump(data, file, indent=4)           

        

