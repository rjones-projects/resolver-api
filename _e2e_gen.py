import json
import os
import sys

sys.path.insert(0, ".")
from app.yaml_resolver import YamlResolver, load_yaml

DEFAULT_YAML = "../dne-pe-terraform-modules/terraform/modules/cloud_run/examples/basic-service.yaml"

path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_YAML
with open(path, encoding="utf-8") as f:
    res = YamlResolver(load_yaml(f.read())).resolve()

print("=== summary ===")
print(json.dumps(res["summary"], indent=2))
print("=== provider block present in main.tf? ===", 'provider "google"' in res["main_tf"])

os.makedirs("_e2e", exist_ok=True)
for fn, key in (("main.tf", "main_tf"), ("variables.tf", "variables_tf"), ("terraform.tfvars", "terraform_tfvars")):
    with open("_e2e/" + fn, "w", encoding="utf-8") as f:
        f.write(res[key])
print("files written to _e2e/")
