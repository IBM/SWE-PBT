import argparse
import json
from mimetypes import init
import re
import os
import pandas as pd
from utility import generate_text, find_min_edit_distance
from prompts_base import PROMPT_IMPORTS, PROMPT_FILE_LOCATION

def find_last_slash_index(path):
    return path.rfind("/")

def generate(instance, model, version, output_dir, pbt_mode):
    # =====================================================
    # Dynamic prompts for regression/reproduction/combined
    # =====================================================
    if pbt_mode == "regression":
        from prompts_regression import PROMPT_TEST_FILE
    elif pbt_mode == "reproduction":
        from prompts_reproduction import PROMPT_TEST_FILE
    elif pbt_mode == "combined":
        from prompts_combined import PROMPT_TEST_FILE
    # =====================================================

    instance_id = instance["instance_id"]

    with open(output_dir+"/"+instance_id+"_localization_"+model+"_"+version+".json","r") as f:
        localization=json.load(f)

    with open(output_dir+"/"+instance_id+"_init_plan_"+model+"_"+version+".json","r") as f:
        initial_states=json.load(f)

    if os.path.exists(output_dir+"/"+"version_5_"+model+"_"+version)==False:
        os.mkdir(output_dir+"/"+"version_5_"+model+"_"+version)

    with open("preprocessed_data/"+instance_id+"/"+"method_bodies.json","r") as f:
        function_body_preprocess=json.load(f)   

    with open("preprocessed_data/"+instance_id+"/"+"class_info.json","r") as f:
        program_files=json.load(f)      

    issue_des=""    

    if version=="base":
        issue_des = instance['problem_statement']
            
  
    local={}   
    init_files=[]

    for i in range(len(localization)):
        if localization[i]['instance_id']==instance_id:
            init_files=localization[i]["generate_1"].split("\n")
            break            

    init_file_list=[]

    # === Comment out for Golden Focal ===
    for i in range(len(init_files)):
        if init_files[i].strip() in program_files:
            init_file_list.append(init_files[i].strip())
        else:
            init_file_list.append(find_min_edit_distance(init_files[i].strip(), list(program_files.keys())))

    initial_state=""
    for i in range(len(initial_states)):
        if initial_states[i]['instance_id']==instance_id:
            initial_state=initial_states[i]["generated"]
            break

    initial_state=initial_state.split("<Action>")
    # === Comment out for Golden Focal ===

    read_dict={}
    
    for i in range (1, len(initial_state)):
        item=initial_state[i]

        action="<Action>"+item
    
        filename=re.findall(r"<Filename>(.*?)</Filename>", action, re.DOTALL)[0].strip()
        functions=re.findall(r"<Function>(.*?)</Function>", action, re.DOTALL)
        if filename not in read_dict:
            read_dict[filename]=functions
        else:
            read_dict[filename].extend(functions)
    
    read_func=""    

    for key in read_dict:
        for key1 in read_dict[key]:
            try:
                read_func=read_func+"File : "+key+"\n\n"+function_body_preprocess[key.strip()][key1.strip()]+"\n\n"
            except:
                continue 

    #get imports
    prompt=PROMPT_IMPORTS

    p=""
    visited=[]    
    for item in program_files:
        # if item not in init_file_list:
        #     continue
        if len(visited)==6000:
            break
        
        fimp=program_files[item]["imports"]

        for imp in fimp:
            if imp.strip()=="":
                continue

            if imp not in visited:
                p=p+imp+"\n"
                visited.append(imp)

    if instance is not None:
        prompt = prompt.replace("<REPO_NAME>", instance['repo'])
        prompt = prompt.replace("<ISSUE DESCRIPTION>", issue_des)
        prompt = prompt.replace("<FUNCTION>", read_func)
        prompt = prompt.replace("<Imports>", p)


    generated_patch = generate_text(prompt, model=model)
    # Ensure generated_patch is a string
    if not isinstance(generated_patch, str):
        generated_patch = str(generated_patch)
    
    # Find imports with error handling
    import_matches = re.findall(r"<IMPORTS>(.*?)</IMPORTS>", generated_patch, re.DOTALL)
    if import_matches:
        fileimport = import_matches[0].strip()
    else:
        fileimport = ""  # Default to empty string if no match found
  
    #get testfile location
    prompt=PROMPT_FILE_LOCATION

    p=""

    visited=[]
    for item in program_files:
        if len(visited)==6000:
            break
        
        if item.lower().find("test")==-1:
            continue

        item=item[0:find_last_slash_index(item)+1]

        if item not in visited:

            p=p+item+"\n"
            visited.append(item)

    if instance is not None:
        prompt = prompt.replace("<REPO_NAME>", instance['repo'])
        prompt = prompt.replace("<ISSUE DESCRIPTION>", issue_des)
        prompt = prompt.replace("<FUNCTION>", read_func)
        prompt = prompt.replace("<IMPORT>", fileimport)
        prompt = prompt.replace("<Location>", p)

    generated_patch=generate_text(prompt, model=model)

    # Ensure generated_patch is a string
    if generated_patch is None:
        generated_patch = ""

    # Extract location from generated patch with error handling
    location_match = re.search(r"<LOCATION>(.*?)</LOCATION>", generated_patch, flags=re.DOTALL)
    if not location_match:
        print("Warning: No LOCATION tag found in generated patch")
        location = ""
    else:
        location = location_match.group(1).strip()
    
    if location not in visited:
        location=find_min_edit_distance(location, visited)

    #get full file 
    prompt=PROMPT_TEST_FILE

    if instance is not None:
        prompt = prompt.replace("<REPO_NAME>", instance['repo'])
        prompt = prompt.replace("<ISSUE DESCRIPTION>", issue_des)
        prompt = prompt.replace("<FUNCTION>", read_func)
        prompt = prompt.replace("<IMPORT>", fileimport)
        prompt = prompt.replace("<LOCATION>", location)
        

    generated_patch=generate_text(prompt, model=model)

    # Ensure generated_patch is a string
    if generated_patch is None:
        generated_patch = ""

    test=re.findall(r"<COMPLETE_TEST>(.*?)</COMPLETE_TEST>", generated_patch, flags=re.DOTALL)[0].strip()
    classname=re.findall(r"<FILE>(.*?)</FILE>", generated_patch, flags=re.DOTALL)[0].strip()

    temp1={}
    temp1['function']=test+"\n"
    temp1['act']="NewFile"
    temp1['classname']=classname

    with open(output_dir+"/"+"version_5_"+model+"_"+version+"/"+instance_id+".json", "w") as file:
        json.dump(temp1, file, indent=4) 

    with open(output_dir+"/"+"version_5_"+model+"_"+version+"/"+instance_id+".txt", "w") as file:
        file.write(test) 

