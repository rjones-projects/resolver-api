"""
YamlResolver

Converts a module-config YAML document — the format used by the examples in
dne-pe-terraform-modules, e.g. terraform/modules/cloud_run/examples/basic-service.yaml —
into Terraform that calls those modules:

  project_id: "my-project-id"        # required
  region: "europe-west1"             # optional (default europe-west1)
  env: "dev"                         # optional, passed to modules that take it
  terraform:
    required_version: ">= 1.14.0"
    providers:
      google: ">= 7.17.0, < 8.0.0"   # or {source: ..., version: ...}
    backend: gcs                     # optional: "<type>" or {<type>: {settings}}
  <module_name>:                     # any other top-level key: a module in terraform/modules/
    source:
      version: v1.1.2                # -> git ref "<module_name>-v1.1.2" (release-please tag)
    depends_on: [project_services]
    spec: [...]

Each module instance becomes a `module` block whose `<module_name>` input receives the
instance's block (minus generator-only keys) via an `any`-typed variable set in
terraform.tfvars. This is the same contract as the modules repo's
.github/scripts/yaml_to_tfvars.py, including repeated top-level keys for several
instances of one module, each named with `module_overide_name`.

Produces:
  - main.tf          : terraform{} block and one module block per instance
  - variables.tf     : project_id / region / env plus one variable per instance
  - terraform.tfvars : the values from the YAML
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

# Used in generated module source URLs (not for HTTP calls).
MODULES_SOURCE      = os.getenv("MODULES_SOURCE", "git::https://github.com/VFGROUP-NSE-NDPE/dne-pe-terraform-modules.git")
MODULES_SUBDIR      = os.getenv("MODULES_SUBDIR", "terraform/modules")
# Git ref for a module whose YAML block has no `source.version`.
MODULES_DEFAULT_REF = os.getenv("MODULES_DEFAULT_REF", "main")

DEFAULT_REGION = "europe-west1"

_RESERVED_KEYS = frozenset({"project_id", "region", "env", "terraform"})

# Keys the generator consumes itself; everything else in a module block is passed
# through to the module (modules read more than `spec`, e.g. kms.keyring, alert_config).
_META_KEYS = frozenset({"source", "depends_on", "module_overide_name"})

# Mirrors yaml_to_tfvars.py: modules that don't declare a `region` / `project_id`
# input, and modules that take `env`.
_NO_REGION_MODULES = frozenset({
    "project_services", "iam_service_account", "vpc_tags", "dashboard_bq",
    "dashboard_composer", "dashboard_gcs", "dashboard_dataproc", "dns",
    "project_iam", "gke_standard_cluster", "bastion_vm", "vf_security_policy",
    "service_agent_iam", "compute_instance", "os_login", "compute_disk",
    "gke_autopilot_cluster", "iam_custom_role_stack", "logging_sink",
    "vpc_peering", "ssl_policy", "project_base",
})
_NO_PROJECT_ID_MODULES = frozenset({"iam_custom_role_stack", "vpc_peering"})
_ENV_MODULES = frozenset({"bigquery", "bigquery_table"})

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


class YamlConfigError(ValueError):
    """The YAML document doesn't describe a valid set of modules."""


@dataclass
class ModuleInstance:
    module: str                 # directory under terraform/modules, and the module's config input
    block: str                  # module block and variable name (module_overide_name or module)
    ref: str
    config: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        return f"{MODULES_SOURCE}//{MODULES_SUBDIR}/{self.module}?ref={self.ref}"

    @property
    def has_config(self) -> bool:
        """False when there's nothing to pass beyond an empty spec, so the module default applies."""
        return any(v not in (None, "", [], {}) for v in self.config.values())


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------

def load_yaml(text: str) -> dict[str, Any]:
    """
    Parse a YAML document. A top-level module key may be repeated to declare several
    instances of that module; the instances are collected into a list in document order.
    """
    loader = yaml.SafeLoader(text)
    try:
        root = loader.get_single_node()
        if root is None:
            return {}
        if not isinstance(root, yaml.MappingNode):
            raise YamlConfigError("YAML root must be a mapping.")
        loader.flatten_mapping(root)
        doc: dict[str, Any] = {}
        for key_node, value_node in root.value:
            key = loader.construct_object(key_node, deep=True)
            value = loader.construct_object(value_node, deep=True)
            if key in doc and key not in _RESERVED_KEYS:
                previous = doc[key] if isinstance(doc[key], list) else [doc[key]]
                doc[key] = previous + (value if isinstance(value, list) else [value])
            else:
                doc[key] = value
        return doc
    finally:
        loader.dispose()


# ----------------------------------------------------------------------
# Resolver
# ----------------------------------------------------------------------

class YamlResolver:
    def __init__(self, doc: dict[str, Any]):
        if not isinstance(doc, dict):
            raise YamlConfigError("YAML root must be a mapping.")
        self.doc = doc
        self.project_id = doc.get("project_id")
        if not self.project_id or not isinstance(self.project_id, str):
            raise YamlConfigError("'project_id' is required and must be a string.")
        self.region = doc.get("region") or DEFAULT_REGION
        self.env = doc.get("env")
        self.terraform_cfg = doc.get("terraform") or {}
        if not isinstance(self.terraform_cfg, dict):
            raise YamlConfigError("'terraform' must be a mapping.")
        self.instances = self._parse_instances()

    def resolve(self) -> dict:
        return {
            "main_tf": self._render_main(),
            "variables_tf": self._render_variables(),
            "terraform_tfvars": self._render_tfvars(),
            "summary": {
                "project_id": self.project_id,
                "region": self.region,
                "modules": [
                    {
                        "name": inst.block,
                        "module": inst.module,
                        "source": inst.source,
                        "depends_on": inst.depends_on,
                    }
                    for inst in self.instances
                ],
            },
        }

    # ------------------------------------------------------------------
    # Module instances
    # ------------------------------------------------------------------

    def _parse_instances(self) -> list[ModuleInstance]:
        instances: list[ModuleInstance] = []
        for key, value in self.doc.items():
            if key in _RESERVED_KEYS:
                continue
            if not isinstance(key, str) or not _IDENTIFIER.match(key):
                raise YamlConfigError(f"Top-level key {key!r} is not a valid module name.")
            entries = value if isinstance(value, list) else [value]
            if not entries or not all(isinstance(e, dict) for e in entries):
                raise YamlConfigError(
                    f"Top-level key '{key}' must be a module config mapping "
                    f"(with source / depends_on / spec)."
                )
            instances.extend(self._parse_instance(key, entry) for entry in entries)

        if not instances:
            raise YamlConfigError("No modules found — add at least one top-level module key.")

        by_block: dict[str, int] = {}
        for inst in instances:
            by_block[inst.block] = by_block.get(inst.block, 0) + 1
        duplicates = [name for name, count in by_block.items() if count > 1]
        if duplicates:
            raise YamlConfigError(
                f"Duplicate module name(s) {duplicates}: give each repeated module a "
                f"distinct 'module_overide_name'."
            )

        self._check_dependencies(instances)
        return instances

    @staticmethod
    def _parse_instance(module: str, entry: dict) -> ModuleInstance:
        block = entry.get("module_overide_name") or module
        if not isinstance(block, str) or not _IDENTIFIER.match(block):
            raise YamlConfigError(f"'module_overide_name' {block!r} is not a valid Terraform name.")

        source = entry.get("source") or {}
        if not isinstance(source, dict):
            raise YamlConfigError(f"'{block}.source' must be a mapping, e.g. {{version: v1.0.0}}.")
        version = source.get("version")
        ref = f"{module}-{version}" if version else MODULES_DEFAULT_REF

        depends_on = entry.get("depends_on") or []
        if isinstance(depends_on, str):
            depends_on = [depends_on]
        if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
            raise YamlConfigError(f"'{block}.depends_on' must be a list of module names.")

        config = {k: v for k, v in entry.items() if k not in _META_KEYS}
        return ModuleInstance(module=module, block=block, ref=str(ref), config=config, depends_on=depends_on)

    @staticmethod
    def _check_dependencies(instances: list[ModuleInstance]) -> None:
        names = {inst.block for inst in instances} | {inst.module for inst in instances}
        for inst in instances:
            unknown = [d for d in inst.depends_on if d not in names]
            if unknown:
                raise YamlConfigError(f"'{inst.block}' depends on undeclared module(s) {unknown}.")

    def _dependency_blocks(self, inst: ModuleInstance) -> list[str]:
        """Module block names for inst.depends_on; a module name covers all its instances."""
        blocks: list[str] = []
        for dep in inst.depends_on:
            matches = [i.block for i in self.instances if i.block == dep] or [
                i.block for i in self.instances if i.module == dep
            ]
            blocks.extend(b for b in matches if b not in blocks and b != inst.block)
        return blocks

    # ------------------------------------------------------------------
    # HCL rendering
    # ------------------------------------------------------------------

    def _render_main(self) -> str:
        lines: list[str] = ["terraform {"]
        required_version = self.terraform_cfg.get("required_version")
        if required_version:
            lines += [f"  required_version = {_hcl_string(str(required_version))}", ""]

        providers = self._required_providers()
        if providers:
            lines.append("  required_providers {")
            for name, (source, version) in providers.items():
                lines.append(f"    {name} = {{")
                pairs = [("source", _hcl_string(source))]
                if version:
                    pairs.append(("version", _hcl_string(version)))
                lines += _align_assignments(pairs, "      ")
                lines.append("    }")
            lines.append("  }")

        lines += self._render_backend()
        lines += ["}", ""]

        # NOTE: the `provider "google"` configuration lives in providers.tf
        # (supplied by the repo-api for CI auth); declaring it here too would be a
        # duplicate provider configuration.

        for inst in self.instances:
            lines.append(f'module "{inst.block}" {{')
            lines.append(f"  source = {_hcl_string(inst.source)}")
            lines.append("")
            inputs: list[tuple[str, str]] = []
            if inst.module not in _NO_PROJECT_ID_MODULES:
                inputs.append(("project_id", "var.project_id"))
            if inst.module not in _NO_REGION_MODULES:
                inputs.append(("region", "var.region"))
            if self.env is not None and inst.module in _ENV_MODULES:
                inputs.append(("env", "var.env"))
            if inst.has_config:
                inputs.append((inst.module, f"var.{inst.block}"))
            lines += _align_assignments(inputs, "  ")
            deps = self._dependency_blocks(inst)
            if deps:
                lines += ["", f"  depends_on = [{', '.join(f'module.{d}' for d in deps)}]"]
            lines += ["}", ""]

        return "\n".join(lines).rstrip() + "\n"

    def _required_providers(self) -> dict[str, tuple[str, Optional[str]]]:
        """provider name -> (source, version) from terraform.providers."""
        raw = self.terraform_cfg.get("providers") or {}
        if not isinstance(raw, dict):
            raise YamlConfigError("'terraform.providers' must be a mapping of provider name to version.")
        providers: dict[str, tuple[str, Optional[str]]] = {}
        for name, spec in raw.items():
            if not isinstance(name, str) or not _IDENTIFIER.match(name):
                raise YamlConfigError(f"Invalid provider name {name!r} in 'terraform.providers'.")
            if isinstance(spec, dict):
                source = spec.get("source") or f"hashicorp/{name}"
                version = spec.get("version")
            else:
                source, version = f"hashicorp/{name}", spec
            providers[name] = (str(source), str(version) if version else None)
        # The modules use google-beta alongside google; pin it to the same range.
        if "google" in providers and "google-beta" not in providers:
            providers["google-beta"] = ("hashicorp/google-beta", providers["google"][1])
        return providers

    def _render_backend(self) -> list[str]:
        backend = self.terraform_cfg.get("backend", "local")
        if not backend:
            return []
        if isinstance(backend, str):
            if backend == "local":
                return ["", '  backend "local" {', '    path = "./terraform.tfstate"', "  }"]
            return ["", f'  backend "{backend}" {{', "    # TODO: configure backend settings", "  }"]
        if isinstance(backend, dict) and len(backend) == 1:
            (kind, settings), = backend.items()
            if isinstance(kind, str) and _IDENTIFIER.match(kind) and isinstance(settings, (dict, type(None))):
                pairs = [(_hcl_key(k), _render_hcl_value(v, indent=2)) for k, v in (settings or {}).items()]
                return ["", f'  backend "{kind}" {{', *_align_assignments(pairs, "    "), "  }"]
        raise YamlConfigError("'terraform.backend' must be a backend type or {<type>: {settings}}.")

    def _render_variables(self) -> str:
        lines = [
            'variable "project_id" {',
            '  description = "GCP project ID."',
            "  type        = string",
            "}",
            "",
            'variable "region" {',
            '  description = "Region for regional modules."',
            "  type        = string",
            f"  default     = {_hcl_string(DEFAULT_REGION)}",
            "}",
            "",
        ]
        if self.env is not None:
            lines += [
                'variable "env" {',
                '  description = "Environment name."',
                "  type        = string",
                "}",
                "",
            ]
        for inst in self.instances:
            if not inst.has_config:
                continue
            lines += [
                f'variable "{inst.block}" {{',
                f'  description = "Config for module.{inst.block} (YAML key \'{inst.module}\')."',
                "  type        = any",
                "}",
                "",
            ]
        return "\n".join(lines).rstrip() + "\n"

    def _render_tfvars(self) -> str:
        preamble = [("project_id", _hcl_string(self.project_id)), ("region", _hcl_string(str(self.region)))]
        if self.env is not None:
            preamble.append(("env", _hcl_string(str(self.env))))
        lines = _align_assignments(preamble, "")
        for inst in self.instances:
            if not inst.has_config:
                continue
            lines += ["", f"{inst.block} = {_render_hcl_value(inst.config)}"]
        return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# HCL value helpers
# ----------------------------------------------------------------------

def _hcl_string(value: str) -> str:
    """Quote a string as an HCL literal, escaping template sequences."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("${", "$${")
        .replace("%{", "%%{")
    )
    return f'"{escaped}"'


def _hcl_key(key: Any) -> str:
    """Object key: bare when it's an identifier, quoted otherwise (e.g. roles/run.invoker)."""
    key = str(key)
    return key if _IDENTIFIER.match(key) else _hcl_string(key)


def _render_hcl_value(value: Any, indent: int = 0) -> str:
    """Render a YAML-parsed value as an HCL literal."""
    pad = "  " * indent
    inner = "  " * (indent + 1)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise YamlConfigError(f"Unsupported number {value!r}.")
        return json.dumps(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(not isinstance(v, (list, dict)) for v in value):
            return "[" + ", ".join(_render_hcl_value(v) for v in value) + "]"
        rendered = [_render_hcl_value(v, indent + 1) for v in value]
        return "[\n" + ",\n".join(f"{inner}{r}" for r in rendered) + f"\n{pad}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        pairs = [(_hcl_key(k), _render_hcl_value(v, indent + 1)) for k, v in value.items()]
        return "{\n" + "\n".join(_align_assignments(pairs, inner)) + f"\n{pad}}}"
    # str, and anything else YAML produces (e.g. dates) as its string form
    return _hcl_string(str(value))


def _align_assignments(pairs: list[tuple[str, str]], prefix: str) -> list[str]:
    """
    Render `key = value` lines with the `=` aligned across each run of
    consecutive single-line assignments, matching `terraform fmt`.

    A value that spans multiple lines (a nested block) is not aligned and
    breaks the surrounding run, so the next run starts fresh after it.
    """
    lines: list[str] = []
    group: list[tuple[str, str]] = []

    def flush() -> None:
        if not group:
            return
        width = max(len(k) for k, _ in group)
        lines.extend(f"{prefix}{k.ljust(width)} = {v}" for k, v in group)
        group.clear()

    for key, val in pairs:
        if "\n" in val:
            flush()
            lines.append(f"{prefix}{key} = {val}")
        else:
            group.append((key, val))
    flush()
    return lines
