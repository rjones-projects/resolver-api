"""
Resolver API — converts module-config YAML documents into Terraform that calls the
dne-pe-terraform-modules modules.
"""

import logging
import os
import random
import string
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.file_client import get_client
from app.yaml_resolver import YamlConfigError, YamlResolver, load_yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Owner/org under which generated Terraform repos are created.
REPO_OWNER = os.getenv("REPO_OWNER", "microservicesolutions")
# Subfolder within the new repo that the generated Terraform is written to.
REPO_DESTINATION = os.getenv("REPO_DESTINATION", "infra")
# Prefix for generated repo names; a random suffix is appended directly to it.
REPO_PREFIX = os.getenv("REPO_PREFIX", "IDP-demo-")

def _generate_repo_name() -> str:
    """Build a new repo name: REPO_PREFIX plus a random 6-character [a-z0-9] suffix."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{REPO_PREFIX}{suffix}"


def _push_terraform(result: dict, deployment_id: Optional[str]) -> dict:
    """
    Push the generated Terraform files to a new '<REPO_PREFIX><suffix>' repo via the
    repo-api. Returns a status dict for the response. Failures are caught and
    reported (status='error') so a push problem never discards the generated
    Terraform the caller still wants.
    """
    files = {
        name: result[key]
        for name, key in (
            ("main.tf", "main_tf"),
            ("variables.tf", "variables_tf"),
            ("terraform.tfvars", "terraform_tfvars"),
        )
        if result.get(key)
    }
    repo_name = _generate_repo_name()
    message = f"Add generated Terraform for deployment {deployment_id or repo_name}"
    try:
        commit = get_client().commit_files(
            owner=REPO_OWNER,
            repo=repo_name,
            files=files,
            message=message,
            destination=REPO_DESTINATION,
        )
        logger.info("Pushed Terraform to %s/%s (%s)", REPO_OWNER, repo_name, commit.get("commit_sha"))
        return {"status": "pushed", "owner": REPO_OWNER, **commit}
    except Exception as exc:
        logger.exception("Failed to push generated Terraform to %s/%s", REPO_OWNER, repo_name)
        return {"status": "error", "owner": REPO_OWNER, "repo": repo_name, "error": str(exc)}

# ── App setup ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Resolver API",
    description="Convert module-config YAML into Terraform files",
    version="1.0.0",
)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return {"message": "Resolver API — visit /docs for usage"}

@app.get("/health")
def health():
    return {"status": "ok"}


# ── Resolve endpoint ─────────────────────────────────────────────────────────

_EXAMPLE_YAML = """\
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
      containers:
        app:
          image: "europe-west1-docker.pkg.dev/my-project-id/cloud-run-source-deploy/hello-api:latest"
"""


class ResolveRequest(BaseModel):
    deploymentId: Optional[str] = None
    yaml: Optional[str] = Field(None, description="The module-config YAML document as a string.")
    config: Optional[dict[str, Any]] = Field(
        None, description="The module-config document as a JSON object (alternative to `yaml`)."
    )


class ResolveResponse(BaseModel):
    deploymentId: Optional[str] = None
    status: str = "resolved"
    projectId: Optional[str] = None
    main_tf: str
    variables_tf: str
    terraform_tfvars: str
    summary: dict
    repository: Optional[dict] = Field(
        None,
        description="Result of pushing the generated Terraform to a new repo "
        "(repo name, branch, commit SHA, files), or an error if the push failed. "
        "Null when push=false.",
    )


def _parse_body(body: bytes, content_type: str) -> tuple[dict[str, Any], Optional[str]]:
    """Return (module-config document, deploymentId from a JSON body)."""
    if "json" in content_type:
        req = ResolveRequest.model_validate_json(body)
        if (req.yaml is None) == (req.config is None):
            raise YamlConfigError("Provide exactly one of 'yaml' or 'config'.")
        doc = load_yaml(req.yaml) if req.yaml is not None else req.config
        return doc, req.deploymentId
    return load_yaml(body.decode("utf-8")), None


@app.post(
    "/resolve",
    response_model=ResolveResponse,
    summary="Convert a module-config YAML document into Terraform",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/yaml": {"schema": {"type": "string"}, "example": _EXAMPLE_YAML},
                "application/json": {"schema": ResolveRequest.model_json_schema()},
            },
        }
    },
    responses={422: {"description": "Invalid YAML or module config"}},
)
async def resolve(
    request: Request,
    deploymentId: Optional[str] = Query(None, description="Deployment ID (raw YAML bodies)."),
    push: bool = Query(True, description="Push the generated Terraform to a new repo."),
):
    """
    Accepts a module-config YAML document (raw `application/yaml` body, or JSON
    `{"yaml": "..."}` / `{"config": {...}}`). Every top-level key other than
    `project_id`, `region`, `env` and `terraform` names a module under
    `terraform/modules` in the modules repo; its `source.version` pins the module's
    release tag, `depends_on` orders it after other modules, and the rest of the block
    (e.g. `spec`) is passed to the module as its config input.

    Returns `main.tf`, `variables.tf` and `terraform.tfvars`, and (unless
    `push=false`) pushes them to a new repo.
    """
    try:
        doc, body_deployment_id = _parse_body(await request.body(), request.headers.get("content-type", ""))
        deployment_id = body_deployment_id or deploymentId
        result = YamlResolver(doc).resolve()
    except (YamlConfigError, yaml.YAMLError, ValidationError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during YAML resolution")
        raise HTTPException(status_code=500, detail=str(exc))

    repository = await run_in_threadpool(_push_terraform, result, deployment_id) if push else None

    return {
        "deploymentId": deployment_id,
        "status": "resolved",
        "projectId": result["summary"]["project_id"],
        "repository": repository,
        **result,
    }
