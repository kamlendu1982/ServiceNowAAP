#!/usr/bin/python3

import yaml, csv, os, json

CSV_FILE_NAME = 'inventory.csv'
YAML_FILE_NAME = 'hosts.yml'
yaml_inventory = {
  "all": {
    "hosts": [
    ]
  }
}

#print('############ ANSIBLE INVENTORY GENERATOR ############ \n')
#ansible_user = {'ansible_user': input('Insert ansible user: \n')}
#ansible_ssh_pass = {'ansible_ssh_pass': input('Insert ansible password: \n')}
#print('\n ....generating yaml inventory....\n')
try:
    file_path = os.path.dirname(os.path.realpath(__file__))
    with open(f"{file_path}/{CSV_FILE_NAME}", mode='r') as infile:
        reader = csv.DictReader(infile, skipinitialspace=True, escapechar="'")
        csv_dictionary = [r for r in reader]
        for index in range(len(csv_dictionary)):
            hostname = csv_dictionary[index]['host']
            host_dictionary = {}
            del csv_dictionary[index]['host']
            #csv_dictionary[index].update(ansible_user)
            #csv_dictionary[index].update(ansible_ssh_pass)
            host_dictionary[hostname] = csv_dictionary[index]
            yaml_inventory['all']['hosts'].append(host_dictionary)
    #for newkey in yaml_inventory['all']['hosts']:
    #    yaml_inventory['all']['hosts'][newkey]['ansible_ssh_port'] = int( yaml_inventory['all']['hosts'][newkey]['ansible_ssh_port'])
    #print(yaml.dump(yaml_inventory))
    print(json.dumps(yaml_inventory, indent=2))
    with open(f"{file_path}/{YAML_FILE_NAME}", 'w', encoding='utf8') as outfile:
        yaml.dump(yaml_inventory, outfile, default_flow_style=False, allow_unicode=True)
except Exception as e:
    print(f"ERROR in Execution: {e}")
