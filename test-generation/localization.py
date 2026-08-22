import os
from typing import Optional
import time
import json
import argparse
import ast
import re
import pandas as pd
from litellm import completion
import random
from utility import find_min_edit_distance, generate_text, get_llm_client
from prompts_base import PROMPT_FILE_LOCALIZATION, PROMPT_FILE_REDUCE

def localization(instance, model, version, output_dir):
    instance_id = instance["instance_id"]

    output_file=output_dir+"/"+instance_id+"_localization_"+model+"_"+version+".json"
        
    prompt = PROMPT_FILE_LOCALIZATION
  
    issue_des=""    

    if version=="base":
        issue_des = instance['problem_statement']

    prompt = prompt.replace("<REPO_NAME>", instance['repo'])
    prompt = prompt.replace("<ISSUE DESCRIPTION>", issue_des)#temp['problem_statement'])
    
    with open("preprocessed_data/"+instance_id+"/"+"class_info.json","r") as f:
        program_files=json.load(f)
                    
    p=""

    if len(program_files)>6000:
        selected_keys = random.sample(list(program_files.keys()), 6000)
        new_dict = {k: program_files[k] for k in selected_keys}
        program_files=new_dict

    for w in program_files:
        p=p+w+" : "
        for item in program_files[w]["methods"]:
            if instance['problem_statement'].find(item)!=-1:
                p=p+item+" "
        p=p.strip()+"\n"    

    prompt = prompt.replace("<LIST>", p.strip())  
    prompt1 = prompt

    generated_patch1 = generate_text(prompt, model=model, max_tokens=4096)

    if generated_patch1.find("```python")!=-1:
        generated_patch1=generated_patch1.replace("```python","")          

    if generated_patch1.find("```")!=-1:
        generated_patch1=generated_patch1.replace("```","")  

    lines=generated_patch1.split("\n")[0:50]

    for i in range(len(lines)):
        if lines[i].strip()=="":
            break
        if lines[i].strip().endswith(":"):
            lines[i]=lines[i][0:len(lines[i])-1].strip() 
            if lines[i].strip() not in program_files:
                lines[i]=find_min_edit_distance(lines[i].strip(), list(program_files.keys()))
        lines[i]=lines[i].strip()

    prompt = PROMPT_FILE_REDUCE

    p=""
    for w in lines:
        if w in program_files:
            p=p+w+" : "
            for item in program_files[w]["methods"]:
                p=p+item+" "
            p=p.strip()+"\n"

    prompt = prompt.replace("<REPO_NAME>", instance['repo'])
    prompt = prompt.replace("<ISSUE DESCRIPTION>", issue_des)#temp['problem_statement'])
    prompt = prompt.replace("<LIST>", p.strip())   

    prompt2=prompt  
    
    generated_patch2 = generate_text(prompt, model=model, max_tokens=4096)
   
    lines1=generated_patch2.split("<Filename>")

    file_dict={}

    for ln in lines1:
        if ln.strip()=="":
            continue
        if ln.strip().find("</Filename>")==-1:
            continue
        ln="<Filename>"+ln

        files = re.findall(r'<Filename>(.*?)</Filename>', ln, re.DOTALL)[0]

        if files.strip().endswith(":"):
            files=files[0:len(files)-1].strip() 

        if files not in program_files:
            files=find_min_edit_distance(files,lines)    

        functions = re.findall(r'<Function>(.*?)</Function>', ln, re.DOTALL) 

        functions_temp=[]

        for func in functions:
            if len(func.strip().split(" "))>1:
                funcs=func.strip().split(" ")
                functions_temp.extend(funcs) 
            else:
                functions_temp.append(func.strip()) 

        if len(functions_temp)>0:
            functions=functions_temp
            file_dict[files]=functions

    data = []
    temp = {}
    temp["instance_id"] = instance_id
    temp["file_function"] = file_dict
    temp["prompt_1"]=prompt1
    temp["prompt_2"]=prompt2
    temp["generate_1"]=generated_patch1
    temp["generate_2"]=generated_patch2 
    data.append(temp)

    with open(output_file, 'w') as file:
        json.dump(data, file, indent=4)

    return