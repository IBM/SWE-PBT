with open("id_list.txt", "r") as f:
    id_list = f.readlines()

command="python otter.py --instance_id <ID>"

with open("command_generate.sh", "w") as f:
    for id in id_list:
        f.write(command.replace("<ID>", id.strip()) + "\n")


