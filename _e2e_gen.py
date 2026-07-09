import json
import os
import sys

sys.path.insert(0, ".")
from app.catalog_resolver import CatalogResolver

overrides = {
    "bucket": {"location": "EU", "storage_class": "STANDARD"},
    "network": {},
    "iam": {},
}
r = CatalogResolver(
    building_blocks=list(overrides),
    terraform_version="~> 1.9",
    backend="local",
    modules_ref="main",
)
res = r.resolve(overrides_map=overrides, tfvars_preamble={"project_id": "my-project-dev"})

print("=== summary ===")
print(json.dumps(res["summary"], indent=2))
print("=== main.tf (head) ===")
print("\n".join(res["main_tf"].splitlines()[:22]))
print("=== provider block present in main.tf? ===", 'provider "google"' in res["main_tf"])

os.makedirs("_e2e", exist_ok=True)
for fn, key in (("main.tf", "main_tf"), ("variables.tf", "variables_tf"), ("terraform.tfvars", "terraform_tfvars")):
    open("_e2e/" + fn, "w").write(res[key])
print("files written to _e2e/")
