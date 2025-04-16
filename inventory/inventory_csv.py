#!/usr/bin/env python3

import csv
import json
import sys
from collections import defaultdict

INVENTORY_FILE = 'inventory.csv'

def parse_csv(file_path):
    inventory = defaultdict(lambda: {"hosts": [], "vars": {}})
    hostvars = {}

    with open(file_path, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            host = row.get("hostname")
            group = row.get("group", "ungrouped")

            inventory[group]["hosts"].append(host)
            hostvars[host] = {k: v for k, v in row.items() if k not in ["hostname", "group"]}

    inventory["_meta"] = {"hostvars": hostvars}
    return inventory

def main():
    if "--list" in sys.argv:
        print(json.dumps(parse_csv(INVENTORY_FILE), indent=2))
    elif "--host" in sys.argv:
        print(json.dumps({}))  # Not needed since we use _meta
    else:
        print("Usage: inventory_csv.py --list")

if __name__ == "__main__":
    main()

