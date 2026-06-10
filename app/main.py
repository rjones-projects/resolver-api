"""
Resolver API — resolves building blocks into Terraform files via the catalog mapping.
"""

import logging
import os
import random
import string
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.catalog_resolver import CatalogResolver
from app.file_client import get_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Owner/org under which generated Terraform repos are created.
REPO_OWNER = os.getenv("REPO_OWNER", "rjones-projects")
# Auto-created Terraform repos are private by default; override with REPO_PRIVATE=false.
# REPO_PRIVATE = os.getenv("REPO_PRIVATE", "true").lower() != "false"
REPO_PRIVATE = 0

def _generate_repo_name() -> str:
    """Build a new repo name: 'IDP-demo-' plus a random 3-letter suffix."""
    suffix = "".join(random.choices(string.ascii_lowercase, k=3))
    return f"IDP-demo-{suffix}"


def _push_terraform(result: dict, deployment_id: Optional[str]) -> dict:
    """
    Push the generated Terraform files to a new 'IDP-demo-<xyz>' repo via the
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
            private=REPO_PRIVATE,
        )
        logger.info("Pushed Terraform to %s/%s (%s)", REPO_OWNER, repo_name, commit.get("commit_sha"))
        return {"status": "pushed", "owner": REPO_OWNER, **commit}
    except Exception as exc:
        logger.exception("Failed to push generated Terraform to %s/%s", REPO_OWNER, repo_name)
        return {"status": "error", "owner": REPO_OWNER, "repo": repo_name, "error": str(exc)}

# ── App setup ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Resolver API",
    description="Resolve building blocks into Terraform files",
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

class DeploymentPayload(BaseModel):
    patternId: Optional[str] = None
    projectId: Optional[str] = None
    projectName: Optional[str] = None
    building_blocks: dict[str, Any] = Field(
        ...,
        description="Map of building block name to override values dict (empty dict {} for no overrides).",
    )
    terraform_version: Optional[str] = Field("~> 1.9")
    backend: Optional[str] = None
    modules_ref: Optional[str] = Field("main")
    estimatedMonthlyCost: Optional[float] = None
    createdBy: Optional[str] = None
    timestamp: Optional[str] = None


class DeploymentRequest(BaseModel):
    deploymentId: Optional[str] = None
    status: Optional[str] = None
    payload: DeploymentPayload
    message: Optional[str] = None
    createdBy: Optional[str] = None
    timestamp: Optional[str] = None


class DeploymentResolveResponse(BaseModel):
    deploymentId: Optional[str] = None
    status: str = "resolved"
    projectId: Optional[str] = None
    projectName: Optional[str] = None
    main_tf: str
    variables_tf: str
    terraform_tfvars: str
    summary: dict
    repository: Optional[dict] = Field(
        None,
        description="Result of pushing the generated Terraform to a new repo "
        "(repo name, branch, commit SHA, files), or an error if the push failed.",
    )


@app.post(
    "/resolve",
    response_model=DeploymentResolveResponse,
    summary="Resolve building blocks into Terraform files",
    responses={
        502: {"description": "Failed to fetch catalog mapping or module variables from GitHub"},
    },
)
def resolve_catalog(request: DeploymentRequest):
    """
    Accepts a deployment payload containing **building block** names mapped to their
    variable overrides. Resolves each block to GCP Terraform modules via the catalog
    mapping YAML, fetches each module's `variables.tf` from GitHub, and returns
    ready-to-use `main.tf`, `variables.tf`, and `terraform.tfvars`.

    The `projectId` from the payload is written as `project_id` at the top of
    `terraform.tfvars`. Override values for each block are routed to the correct
    module config variable and deduplicated across blocks.
    """
    try:
        p = request.payload
        overrides_map: dict[str, Any] = p.building_blocks
        block_names = list(overrides_map.keys())

        # Preamble vars written at the top of terraform.tfvars before block sections.
        preamble: dict[str, Any] = {}
        if p.projectId:
            preamble["project_id"] = p.projectId

        resolver = CatalogResolver(
            building_blocks=block_names,
            terraform_version=p.terraform_version or "~> 1.9",
            backend=p.backend,
            modules_ref=p.modules_ref or "main",
        )
        result = resolver.resolve(overrides_map=overrides_map, tfvars_preamble=preamble or None)

        repository = _push_terraform(result, request.deploymentId)

        return {
            "deploymentId": request.deploymentId,
            "status": "resolved",
            "projectId": p.projectId,
            "projectName": p.projectName,
            "repository": repository,
            **result,
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during catalog resolution")
        raise HTTPException(status_code=500, detail=str(exc))
