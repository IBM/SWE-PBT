import json


with open("TDD_Bench.json", 'r') as f:
    data = json.load(f)


golden_code_patch = []

for item in data:
    print(item["patch"])

    temp={}

    temp["instance_id"] = item["instance_id"]
    temp["diffs"] = [item["patch"]]

    golden_code_patch.append(temp)

with open("code_patch.json", 'w') as file:
    json.dump(golden_code_patch, file, indent=4)   



import random 
random.shuffle(data)

command="python -m tddbench.harness.run_evaluation --test_patch test_patch.json --code_patch code_patch.json  --max_workers 1 --instance_ids <ID> --run_id bob"


with open("command.sh", 'w') as file:
    for item in data:   
        file.write(command.replace("<ID>", item["instance_id"]))
        file.write("\n")

