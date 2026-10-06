# Resolver API

A lightweight REST API (FastAPI) that converts a **module-config YAML document** into
ready-to-use `main.tf`, `variables.tf`, and `terraform.tfvars` that call the modules in
[`dne-pe-terraform-modules`](https://github.com/VFGROUP-NSE-NDPE/dne-pe-terraform-modules)
(`terraform/modules/<module>`).

## How it works

The YAML format is the one used by the module examples, e.g.
`terraform/modules/cloud_run/examples/basic-service.yaml`:

```yaml
project_id: "my-project-id"          # required
region: "europe-west1"               # optional, default europe-west1
env: "dev"                           # optional, passed to modules that take `env`

terraform:
  required_version: ">= 1.14.0"
  providers:
    google: ">= 7.17.0, < 8.0.0"     # google-beta is pinned to the same range
  backend: gcs                       # optional: "<type>" or {<type>: {settings}}; default local

project_services:                    # top-level key = module directory name
  source:
    version: v1.0.3                  # -> ?ref=project_services-v1.0.3
  depends_on: []
  spec:
    - name: "cloud-run-required-apis"
      service_list: ["run.googleapis.com"]

cloud_run:
  source:
    version: v1.1.2
  depends_on: [project_services]
  spec:
    - name: "hello-api"
      type: "SERVICE"
      containers:
        app:
          image: "europe-west1-docker.pkg.dev/my-project-id/cloud-run-source-deploy/hello-api:latest"
```

1. Every top-level key other than `project_id`, `region`, `env` and `terraform` names a
   module. It becomes a `module` block sourced from
   `git::https://github.com/VFGROUP-NSE-NDPE/dne-pe-terraform-modules.git//terraform/modules/<module>?ref=<module>-<version>`
   (the release-please tag; `MODULES_DEFAULT_REF`, default `main`, when `source.version` is absent).
2. The rest of the block (`spec`, plus any other keys such as `alert_config`) is passed to
   the module's config input of the same name (`cloud_run = var.cloud_run`) through an
   `any`-typed variable whose value is written to `terraform.tfvars`. `source`,
   `depends_on` and `module_overide_name` are consumed by the generator and not passed on.
3. `project_id` / `region` / `env` are wired into each module that takes them.
4. `depends_on` becomes `depends_on = [module.<name>, ...]`.
5. To declare several instances of one module, repeat its top-level key and give each a
   distinct `module_overide_name`; that name is used for the module block and its variable.
   A `depends_on` entry naming the module (rather than an instance) covers all its instances.

This mirrors the modules repo's own `.github/scripts/yaml_to_tfvars.py`. No `provider`
block is generated — `providers.tf` is supplied by the repo-api.

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

### `POST /resolve`

Send the YAML document either as a raw body (`Content-Type: application/yaml`) or as JSON:

```json
{ "deploymentId": "deploy-123", "yaml": "project_id: my-project-id
..." }
```

(or `"config": { ... }` with the document as a JSON object instead of `"yaml"`).

| Query param | Default | Description |
|---|---|---|
| `deploymentId` | | Deployment ID, for raw YAML bodies (JSON bodies carry it in the body) |
| `push` | `true` | Push the generated files to a new `IDP-demo-<xyz>` repo via the repo-api |

**Response**

```json
{
  "deploymentId": "deploy-123",
  "status": "resolved",
  "projectId": "my-project-id",
  "main_tf": "terraform {
  required_version = ...",
  "variables_tf": "variable \"project_id\" {
 ...",
  "terraform_tfvars": "project_id = \"my-project-id\"
 ...",
  "summary": {
    "project_id": "my-project-id",
    "region": "europe-west1",
    "modules": [
      {"name": "project_services", "module": "project_services", "source": "git::...?ref=project_services-v1.0.3", "depends_on": []},
      {"name": "cloud_run", "module": "cloud_run", "source": "git::...?ref=cloud_run-v1.1.2", "depends_on": ["project_services"]}
    ]
  },
  "repository": { "status": "pushed", "repo": "IDP-demo-abc", "...": "..." }
}
```

Invalid YAML or module config (missing `project_id`, unknown `depends_on` target,
repeated module without `module_overide_name`, …) returns `422` with a `detail` message.

**Example curl**

```bash
curl -s -X POST 'http://localhost:8080/resolve?push=false'   -H 'Content-Type: application/yaml'   --data-binary @../dne-pe-terraform-modules/terraform/modules/cloud_run/examples/basic-service.yaml | jq .
```

| Env var | Default |
|---|---|
| `MODULES_SOURCE` | `git::https://github.com/VFGROUP-NSE-NDPE/dne-pe-terraform-modules.git` |
| `MODULES_SUBDIR` | `terraform/modules` |
| `MODULES_DEFAULT_REF` | `main` |
| `REPO_OWNER` / `REPO_DESTINATION` | `microservicesolutions` / `infra` |

---

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

`python _e2e_gen.py [path/to/config.yaml]` writes the generated files to `_e2e/`.

---

## Project structure

```
resolver-api/
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app, routes, request/response models
│   ├── yaml_resolver.py      # Module-config YAML → main.tf / variables.tf / terraform.tfvars
│   └── file_client.py        # Client for the repo-api (pushes generated Terraform)
├── tests/
├── Dockerfile
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


docker build -t resolver-api .
#docker tag resolver-api europe-west2-docker.pkg.dev/idp-poc-495014/resolver-api/resolver-api:latest
#docker push europe-west2-docker.pkg.dev/idp-poc-495014/resolver-api/resolver-api:latest
#docker run -p 8081:8080 resolver-api
```
