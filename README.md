# Resolver API

A lightweight REST API (FastAPI) that accepts a set of **building blocks** and resolves
them into ready-to-use `main.tf`, `variables.tf`, and `terraform.tfvars` files.

## How it works

1. Building block names are looked up in the catalog mapping
   ([`gcp-mapping.yaml`](https://github.com/rjones-projects/catalog/blob/main/gcp-mapping.yaml))
   to find their constituent GCP Terraform modules.
2. Each module's `variables.tf` is fetched from
   [`gcp_terraform-modules`](https://github.com/rjones-projects/gcp_terraform-modules)
   via the file service.
3. Variables from all modules are **merged and deduplicated**; required variables (no
   upstream default) receive safe placeholder defaults and are annotated with a comment.
4. The `terraform {}` block, provider stub, module blocks, `variables.tf`, and a
   `terraform.tfvars` (from the supplied overrides) are generated in valid HCL.

---

## Running

```bash
# Docker
docker build -t resolver-api .
docker run -p 8080:8080 resolver-api

# Compose
docker compose up

# Local dev
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

---

## API

### `GET /health`
Returns `{"status": "ok"}`.

### `POST /catalog/resolve`

Accepts a deployment payload whose `building_blocks` map each building block name to a
dict of variable overrides (use `{}` for no overrides). Resolves them to GCP Terraform
modules and returns ready-to-use `main.tf`, `variables.tf`, and `terraform.tfvars`.

**Request body**

```json
{
  "deploymentId": "deploy-123",
  "payload": {
    "projectId": "my-gcp-project",
    "projectName": "My Project",
    "building_blocks": {
      "bucket": {},
      "sql": { "tier": "db-custom-2-7680" },
      "network": {}
    },
    "terraform_version": "~> 1.9",
    "backend": "gcs",
    "modules_ref": "main"
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `payload.building_blocks` | object | ✅ | Map of building block name → override values dict (`{}` for none) |
| `payload.projectId` | string | | Written as `project_id` at the top of `terraform.tfvars` |
| `payload.terraform_version` | string | | Default `~> 1.9` |
| `payload.backend` | string | | Backend type stub (`gcs`, `s3`, `azurerm`, …) |
| `payload.modules_ref` | string | | Git ref to pin module sources to (default `main`) |

**Response**

```json
{
  "deploymentId": "deploy-123",
  "status": "resolved",
  "projectId": "my-gcp-project",
  "projectName": "My Project",
  "main_tf": "terraform {\n  required_version = ...",
  "variables_tf": "variable \"project_id\" {\n ...",
  "terraform_tfvars": "project_id = \"my-gcp-project\"\n ...",
  "summary": {
    "building_blocks_requested": ["bucket", "sql", "network"],
    "building_blocks_resolved": ["bucket", "sql", "network"],
    "building_blocks_unresolved": [],
    "modules_resolved": ["gcs", "cloud_sql", "network", "firewall", "dns"],
    "variables_extracted": 24,
    "modules_with_fetch_errors": []
  }
}
```

**Example curl**

```bash
curl -s -X POST http://localhost:8080/catalog/resolve \
  -H 'Content-Type: application/json' \
  -d '{
    "payload": {
      "projectId": "my-gcp-project",
      "building_blocks": { "bucket": {}, "sql": {} },
      "backend": "gcs"
    }
  }' | jq .
```

---

## Running tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## Project structure

```
resolver-api/
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app, routes, request/response models
│   ├── catalog_resolver.py   # Building-block → GCP module resolver & HCL generation
│   └── file_client.py        # Client for the GitHub file service
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Deployment notes (GCP / Cloud Run)

```bash
# Create a service account
gcloud iam service-accounts create github-actions  --project=vf-gned-ngdi-alpha-ing

# Grant required roles
gcloud projects add-iam-policy-binding vf-gned-ngdi-alpha-ing --member="serviceAccount:github-actions@vf-gned-ngdi-alpha-ing.iam.gserviceaccount.com" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding vf-gned-ngdi-alpha-ing --member="serviceAccount:github-actions@vf-gned-ngdi-alpha-ing.iam.gserviceaccount.com" --role="roles/run.developer"
#add IAM permissions
gcloud iam service-accounts add-iam-policy-binding  479677124022-compute@developer.gserviceaccount.com --project=vf-gned-ngdi-alpha-ing  --role="roles/iam.serviceAccountUser"  --member="serviceAccount:github-actions@vf-gned-ngdi-alpha-ing.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding vf-gned-ngdi-alpha-ing --member="serviceAccount:github-actions@vf-gned-ngdi-alpha-ing.iam.gserviceaccount.com" --role="roles/run.admin"

# Create WIF pool + provider (swap in your GitHub org/repo)
gcloud iam workload-identity-pools create github-pool --project=vf-gned-ngdi-alpha-ing --location=global
gcloud iam workload-identity-pools providers create-oidc github-provider --project=vf-gned-ngdi-alpha-ing --location=global --workload-identity-pool=github-pool --issuer-uri="https://token.actions.githubusercontent.com"  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" --attribute-condition="assertion.repository=='rjones-projects/resolver-api'"

# Allow the pool to impersonate the SA
gcloud iam service-accounts add-iam-policy-binding github-actions@vf-gned-ngdi-alpha-ing.iam.gserviceaccount.com --project=vf-gned-ngdi-alpha-ing --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/projects/$(gcloud projects describe vf-gned-ngdi-alpha-ing --format='value(projectNumber)')/locations/global/workloadIdentityPools/github-pool/attribute.repository/rjones-projects/resolver-api"

#create secrets
 Settings → Secrets and variables → Actions → New repository secret

#get the secret - WIF_PROVIDER
gcloud iam workload-identity-pools providers describe github-provider --project=vf-gned-ngdi-alpha-ing --location=global --workload-identity-pool=github-pool --format="value(name)"

#secret - WIF_SERVICE_ACCOUNT
github-actions@vf-gned-ngdi-alpha-ing.iam.gserviceaccount.com

#github variables used by the resolver
CATALOG_OWNER=rjones-projects
CATALOG_REPO=catalog
CATALOG_MAPPING_FILE=gcp-mapping.yaml

docker build -t resolver-api .
#docker tag resolver-api europe-west2-docker.pkg.dev/idp-poc-495014/resolver-api/resolver-api:latest
#docker push europe-west2-docker.pkg.dev/idp-poc-495014/resolver-api/resolver-api:latest
#docker run -p 8080:8080 resolver-api
```
