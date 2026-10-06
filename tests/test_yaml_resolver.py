import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.yaml_resolver import YamlConfigError, YamlResolver, load_yaml

BASIC = """
project_id: "my-project-id"
region: "europe-west1"

terraform:
  required_version: ">= 1.14.0"
  providers:
    google: ">= 7.17.0, < 8.0.0"

project_services:
  source:
    version: v1.0.3
  depends_on: []
  spec:
    - name: "cloud-run-required-apis"
      service_list:
        - "run.googleapis.com"

cloud_run:
  source:
    version: v1.1.2
  depends_on:
    - project_services
  spec:
    - name: "hello-api"
      type: "SERVICE"
      iam:
        roles/run.invoker:
          - "allUsers"
"""


def resolve(text: str) -> dict:
    return YamlResolver(load_yaml(text)).resolve()


def test_modules_become_pinned_module_blocks():
    main_tf = resolve(BASIC)["main_tf"]
    assert (
        'source = "git::https://github.com/VFGROUP-NSE-NDPE/dne-pe-terraform-modules.git'
        '//terraform/modules/cloud_run?ref=cloud_run-v1.1.2"'
    ) in main_tf
    assert "cloud_run  = var.cloud_run" in main_tf
    assert "depends_on = [module.project_services]" in main_tf


def test_region_only_passed_to_regional_modules():
    main_tf = resolve(BASIC)["main_tf"]
    project_services_block = main_tf.split('module "project_services"')[1].split("}")[0]
    assert "region" not in project_services_block
    assert "region     = var.region" in main_tf.split('module "cloud_run"')[1]


def test_google_beta_pinned_alongside_google():
    main_tf = resolve(BASIC)["main_tf"]
    assert 'google-beta = {' in main_tf
    assert main_tf.count('version = ">= 7.17.0, < 8.0.0"') == 2


def test_tfvars_carry_yaml_values_without_meta_keys():
    tfvars = resolve(BASIC)["terraform_tfvars"]
    assert 'project_id = "my-project-id"' in tfvars
    assert '"roles/run.invoker" = ["allUsers"]' in tfvars
    assert "source" not in tfvars
    assert "depends_on" not in tfvars


def test_repeated_module_keys_need_override_names():
    doc = """
project_id: p
cloud_run:
  module_overide_name: api
  spec: [{name: api}]
cloud_run:
  module_overide_name: worker
  depends_on: [api]
  spec: [{name: worker}]
"""
    res = resolve(doc)
    assert 'module "api"' in res["main_tf"]
    assert 'module "worker"' in res["main_tf"]
    assert "cloud_run  = var.worker" in res["main_tf"]
    assert "depends_on = [module.api]" in res["main_tf"]

    with pytest.raises(YamlConfigError, match="module_overide_name"):
        resolve("project_id: p\ncloud_run: {spec: [{name: a}]}\ncloud_run: {spec: [{name: b}]}\n")


def test_depends_on_module_name_covers_all_instances():
    doc = """
project_id: p
gcs:
  module_overide_name: a
  spec: [{name: a}]
gcs:
  module_overide_name: b
  spec: [{name: b}]
cloud_run:
  depends_on: [gcs]
  spec: [{name: x}]
"""
    assert "depends_on = [module.a, module.b]" in resolve(doc)["main_tf"]


def test_strings_are_escaped():
    tfvars = resolve('project_id: p\ncloud_run:\n  spec: [{cmd: "echo \\"${HOME}\\" %{x}"}]\n')["terraform_tfvars"]
    assert r'cmd = "echo \"$${HOME}\" %%{x}"' in tfvars


def test_empty_spec_is_not_passed():
    res = resolve("project_id: p\nproject_services:\n  source: {version: v1.0.3}\n  spec: []\n")
    assert "project_services = var" not in res["main_tf"]
    assert "project_services" not in res["terraform_tfvars"]


@pytest.mark.parametrize(
    "doc, message",
    [
        ("region: x\ncloud_run: {}", "project_id"),
        ("project_id: p", "No modules"),
        ("project_id: p\ncloud_run: [1, 2]", "mapping"),
        ("project_id: p\ncloud_run:\n  depends_on: [nope]", "undeclared"),
        ("- a\n- b", "root must be a mapping"),
    ],
)
def test_invalid_documents(doc, message):
    with pytest.raises(YamlConfigError, match=message):
        resolve(doc)


def test_endpoint_accepts_raw_yaml_and_json():
    client = TestClient(app)
    raw = client.post(
        "/resolve?push=false&deploymentId=d1",
        content=BASIC,
        headers={"content-type": "application/yaml"},
    )
    assert raw.status_code == 200
    assert raw.json()["deploymentId"] == "d1"
    assert raw.json()["repository"] is None

    as_json = client.post("/resolve?push=false", json={"deploymentId": "d2", "yaml": BASIC})
    assert as_json.status_code == 200
    assert as_json.json()["main_tf"] == raw.json()["main_tf"]

    bad = client.post("/resolve?push=false", content="project_id: [", headers={"content-type": "application/yaml"})
    assert bad.status_code == 422
