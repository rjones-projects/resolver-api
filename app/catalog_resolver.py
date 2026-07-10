"""
CatalogResolver

Fetches the building-block → module mapping from the catalog YAML at:
  https://raw.githubusercontent.com/rjones-projects/catalog/main/gcp-mapping.yaml
and the building-block → building-block dependency graph, recorded per architecture
pattern, from:
  https://raw.githubusercontent.com/rjones-projects/catalog/main/catalog.yaml

For each resolved module, pulls variables.tf (and outputs.tf, to wire module-to-module
dependencies) from:
  https://raw.githubusercontent.com/rjones-projects/gcp_terraform-modules/main/<module>/variables.tf
  https://raw.githubusercontent.com/rjones-projects/gcp_terraform-modules/main/<module>/outputs.tf

Produces:
  - main.tf      : terraform{} block, google provider stub, module blocks. A module
                    input that matches an output of one of its catalog dependencies is
                    wired to `module.<dependency>.<output>` instead of `var.<input>`.
  - variables.tf : merged variables with defaults / required-variable placeholders
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.file_client import get_client

logger = logging.getLogger(__name__)

# Repository coordinates — all configurable via environment variables.
CATALOG_OWNER         = os.getenv("CATALOG_OWNER",         "rjones-projects")
CATALOG_REPO          = os.getenv("CATALOG_REPO",          "catalog")
CATALOG_MAPPING_FILE  = os.getenv("CATALOG_MAPPING_FILE",  "gcp-mapping.yaml")
CATALOG_PATTERNS_FILE = os.getenv("CATALOG_PATTERNS_FILE", "catalog.yaml")
MODULES_OWNER         = os.getenv("MODULES_OWNER",         "rjones-projects")
MODULES_REPO          = os.getenv("MODULES_REPO",          "gcp_terraform-modules")
MODULES_SUBDIR        = os.getenv("MODULES_SUBDIR",        "terraform/modules")

# Used in generated main.tf module source URLs (not for HTTP calls).
GCP_MODULES_SOURCE = f"github.com/{MODULES_OWNER}/{MODULES_REPO}"

_TYPE_DEFAULTS: dict[str, str] = {
    "string":       '""',
    "number":       "0",
    "bool":         "false",
    "list(string)": "[]",
    "list(number)": "[]",
    "map(string)":  "{}",
    "map(any)":     "{}",
    "set(string)":  "[]",
    "any":          "null",
}


@dataclass
class CatalogVariable:
    name: str
    type_hcl: str = "string"
    description: str = ""
    default_hcl: Optional[str] = None  # None = required (no upstream default)
    sensitive: bool = False

    @property
    def required(self) -> bool:
        return self.default_hcl is None

    def rendered_default(self) -> str:
        if self.default_hcl is None:
            return _TYPE_DEFAULTS.get(self.type_hcl.lower(), '""')
        return self.default_hcl


@dataclass
class ResolvedModule:
    name: str
    source: str
    variables: list[CatalogVariable] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    fetch_error: Optional[str] = None


class CatalogResolver:
    def __init__(
        self,
        building_blocks: list[str],
        terraform_version: str = "~> 1.9",
        backend: Optional[str] = None,
        modules_ref: str = "main",
    ):
        self.building_blocks = building_blocks
        self.terraform_version = terraform_version
        self.backend = backend
        self.modules_ref = modules_ref

    def resolve(
        self,
        overrides_map: Optional[dict[str, Any]] = None,
        tfvars_preamble: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Resolve building blocks into main.tf + variables.tf.
        If overrides_map is provided (block → override dict or []), also produces terraform.tfvars.
        """
        block_modules, module_deps = self._fetch_mapping()

        block_names = list(overrides_map.keys()) if overrides_map is not None else self.building_blocks
        modules = self._resolve_modules(block_modules, module_deps, block_names)
        modules_by_name = {m.name: m for m in modules}

        all_vars = self._collect_variables(modules)
        main_tf = self._render_main(modules, modules_by_name)
        variables_tf = self._render_variables(all_vars)
        terraform_tfvars = (
            self._render_tfvars(overrides_map, block_modules, modules_by_name, preamble=tfvars_preamble)
            if overrides_map
            else ""
        )

        unresolved = [b for b in block_names if b not in block_modules]
        return {
            "main_tf": main_tf,
            "variables_tf": variables_tf,
            "terraform_tfvars": terraform_tfvars,
            "summary": {
                "building_blocks_requested": block_names,
                "building_blocks_resolved": [b for b in block_names if b in block_modules],
                "building_blocks_unresolved": unresolved,
                "modules_resolved": [m.name for m in modules],
                "variables_extracted": len(all_vars),
                "modules_with_fetch_errors": [m.name for m in modules if m.fetch_error],
            },
        }

    # ------------------------------------------------------------------
    # Catalog mapping fetch
    # ------------------------------------------------------------------

    def _fetch_mapping(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """
        Fetch both catalog sources and return:
          - block_modules: building_block name -> [primary module names realizing it]
          - module_deps:    module name -> [names of other modules whose outputs it
                             may need wired into its inputs]

        module_deps is merged from two sources:
          1. Fine-grained, module-specific wiring declared directly on a
             terraformModules entry in gcp-mapping.yaml via `dependsOn` — e.g.
             "firewall" needs "network"'s output even though they're both primary
             modules of the same "network" building block, a relationship
             building-block-level dependencies can't express.
          2. Coarser building-block-to-building-block dependencies, recorded per
             architecture pattern (`kind: System`) in catalog.yaml under
             `spec.metadata.dependencies` — a dict of
             `{building_block: [building_block, ...]}`. Expanded to each dependency
             block's primary modules, so every primary module of a block inherits a
             dependency on every primary module of the blocks it depends on.
        """
        block_modules, intra_module_deps = self._fetch_block_modules()
        block_deps = self._fetch_block_dependencies()

        module_deps: dict[str, list[str]] = {}

        def add_deps(module_name: str, deps: list[str]) -> None:
            # A module can depend on itself only by mistake (e.g. it belongs to more
            # than one building block and a dependency block's modules happen to
            # include it) — exclude that to avoid a self-referencing module.
            deps = [d for d in deps if d != module_name]
            if not deps:
                return
            existing = module_deps.setdefault(module_name, [])
            existing.extend(d for d in deps if d not in existing)

        for module_name, deps in intra_module_deps.items():
            add_deps(module_name, deps)

        for block, modules in block_modules.items():
            dep_blocks = block_deps.get(block, [])
            if not dep_blocks:
                continue
            candidate_deps: list[str] = []
            for dep_block in dep_blocks:
                candidate_deps.extend(block_modules.get(dep_block, []))
            for module_name in modules:
                add_deps(module_name, candidate_deps)

        return block_modules, module_deps

    @staticmethod
    def _fetch_block_modules() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """
        Fetch gcp-mapping.yaml and return:
          - block_modules: building_block name -> [primary module names]
          - intra_module_deps: module name -> [other module names it explicitly
            depends on], from an optional `dependsOn` key on its terraformModules entry
        """
        try:
            docs = get_client().proxy_catalog_file(
                CATALOG_OWNER, CATALOG_REPO, CATALOG_MAPPING_FILE
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch catalog mapping: {exc}") from exc

        block_modules: dict[str, list[str]] = {}
        intra_module_deps: dict[str, list[str]] = {}
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "Component":
                continue
            name = (doc.get("metadata") or {}).get("name", "")
            if not name:
                continue
            refs = (doc.get("spec") or {}).get("terraformModules") or []
            modules, deps = CatalogResolver._parse_terraform_modules(refs)
            block_modules[name] = modules
            for module_name, module_deps_list in deps.items():
                intra_module_deps.setdefault(module_name, []).extend(module_deps_list)
        return block_modules, intra_module_deps

    @staticmethod
    def _fetch_block_dependencies() -> dict[str, list[str]]:
        """building_block name -> [building blocks it depends on], from catalog.yaml."""
        try:
            docs = get_client().proxy_catalog_file(
                CATALOG_OWNER, CATALOG_REPO, CATALOG_PATTERNS_FILE
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch catalog patterns: {exc}") from exc

        block_deps: dict[str, list[str]] = {}
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "System":
                continue
            dependencies = ((doc.get("spec") or {}).get("metadata") or {}).get("dependencies")
            if not isinstance(dependencies, dict):
                continue
            for block_name, deps in dependencies.items():
                block_deps[str(block_name).strip()] = CatalogResolver._valid_module_names(deps)
        return block_deps

    @staticmethod
    def _parse_terraform_modules(refs: list) -> tuple[list[str], dict[str, list[str]]]:
        """
        Parse a `terraformModules` list where each entry is one of:
          - "Component:module_name"
          - {"Component": "module_name"}
          - {"Component": "module_name", "dependsOn": ["other_module", ...]}
        Returns (module_names, {module_name: [dependsOn names]}).
        """
        modules: list[str] = []
        deps: dict[str, list[str]] = {}
        for ref in refs:
            if isinstance(ref, dict):
                # "Component: module_name" (space after colon) is parsed by YAML
                # as {"Component": "module_name", ...} — extract the value directly.
                name = str(ref.get("Component", "")).strip()
                depends_on = ref.get("dependsOn")
            else:
                # "Component:module_name" (no space) stays as a plain string.
                name = re.sub(r"^Component:\s*", "", str(ref)).strip()
                depends_on = None
            if not (name and re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", name)):
                continue
            modules.append(name)
            if depends_on:
                deps[name] = CatalogResolver._valid_module_names(depends_on)
        return modules, deps

    @staticmethod
    def _valid_module_names(names) -> list[str]:
        result = []
        for n in names or []:
            n = str(n).strip()
            if n and re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", n):
                result.append(n)
        return result

    # ------------------------------------------------------------------
    # Module resolution
    # ------------------------------------------------------------------

    def _resolve_modules(
        self,
        block_modules: dict[str, list[str]],
        module_deps: dict[str, list[str]],
        block_names: Optional[list[str]] = None,
    ) -> list[ResolvedModule]:
        """
        Resolve each requested building block to its primary catalog modules, then
        follow each module's own dependency list (module_deps) transitively so that
        modules it needs for output wiring — even when not a direct building-block
        dependency — are also resolved and rendered in main.tf.
        """
        seen: dict[str, ResolvedModule] = {}
        queue: list[str] = []
        for block in (block_names if block_names is not None else self.building_blocks):
            queue.extend(block_modules.get(block, []))

        while queue:
            module_name = queue.pop(0)
            if module_name in seen:
                continue
            source = f"{GCP_MODULES_SOURCE}//{MODULES_SUBDIR}/{module_name}?ref={self.modules_ref}"
            variables, error = self._fetch_module_variables(module_name)
            outputs = self._fetch_module_outputs(module_name)
            dependencies = module_deps.get(module_name, [])
            seen[module_name] = ResolvedModule(
                name=module_name,
                source=source,
                variables=variables,
                outputs=outputs,
                dependencies=dependencies,
                fetch_error=error,
            )
            queue.extend(d for d in dependencies if d not in seen)
        return list(seen.values())

    # ------------------------------------------------------------------
    # Variable fetch & parse
    # ------------------------------------------------------------------

    def _fetch_module_variables(self, module_name: str) -> tuple[list[CatalogVariable], Optional[str]]:
        path = f"{MODULES_SUBDIR}/{module_name}/variables.tf"
        try:
            content = get_client().get_text_file(MODULES_OWNER, MODULES_REPO, path, ref=self.modules_ref)
            return self._parse_variables_tf(content), None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return [], f"variables.tf not found for module '{module_name}'"
            logger.warning("Failed to fetch variables.tf for %s: %s", module_name, exc)
            return [], str(exc)
        except Exception as exc:
            logger.warning("Failed to fetch variables.tf for %s: %s", module_name, exc)
            return [], str(exc)

    def _parse_variables_tf(self, content: str) -> list[CatalogVariable]:
        return [
            CatalogVariable(
                name=name,
                type_hcl=fields.get("type", "string"),
                description=fields.get("description", ""),
                default_hcl=fields.get("default"),
                sensitive=fields.get("sensitive", False),
            )
            for name, fields in (
                (name, self._parse_variable_body(body))
                for name, body in self._extract_named_blocks(content, "variable")
            )
        ]

    # ------------------------------------------------------------------
    # outputs.tf fetch & parse (for module-to-module dependency wiring)
    # ------------------------------------------------------------------

    def _fetch_module_outputs(self, module_name: str) -> list[str]:
        path = f"{MODULES_SUBDIR}/{module_name}/outputs.tf"
        try:
            content = get_client().get_text_file(MODULES_OWNER, MODULES_REPO, path, ref=self.modules_ref)
            return self._parse_outputs_tf(content)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                logger.warning("Failed to fetch outputs.tf for %s: %s", module_name, exc)
            return []
        except Exception as exc:
            logger.warning("Failed to fetch outputs.tf for %s: %s", module_name, exc)
            return []

    def _parse_outputs_tf(self, content: str) -> list[str]:
        return [name for name, _ in self._extract_named_blocks(content, "output")]

    @staticmethod
    def _extract_named_blocks(content: str, keyword: str) -> list[tuple[str, str]]:
        """Extract `keyword "name" { ... }` blocks (e.g. `variable`/`output`), depth-tracking braces."""
        results = []
        i = 0
        while i < len(content):
            m = re.search(rf'{keyword}\s+"([^"]+)"\s*\{{', content[i:])
            if not m:
                break
            block_name = m.group(1)
            start = i + m.end()
            depth, j = 1, start
            while j < len(content) and depth > 0:
                if content[j] == "{":
                    depth += 1
                elif content[j] == "}":
                    depth -= 1
                j += 1
            results.append((block_name, content[start : j - 1]))
            i = j
        return results

    @staticmethod
    def _parse_variable_body(body: str) -> dict:
        result: dict = {}

        m = re.search(r"^\s*type\s*=\s*", body, re.MULTILINE)
        if m:
            type_val = CatalogResolver._extract_hcl_value(body[m.end():])
            if type_val:
                result["type"] = type_val

        m = re.search(r'^\s*description\s*=\s*"([^"]*)"', body, re.MULTILINE)
        if m:
            result["description"] = m.group(1)

        m = re.search(r"^\s*sensitive\s*=\s*(true|false)", body, re.MULTILINE)
        if m:
            result["sensitive"] = m.group(1) == "true"

        m = re.search(r"^\s*default\s*=\s*", body, re.MULTILINE)
        if m:
            value = CatalogResolver._extract_hcl_value(body[m.end():])
            if value:
                result["default"] = value

        return result

    @staticmethod
    def _extract_hcl_value(s: str) -> str:
        """Extract a complete HCL value from the start of s, handling nested braces/parens."""
        s = s.lstrip(" \t")
        if not s:
            return ""
        first = s[0]
        # { or [ — depth-tracked brace/bracket pair
        if first in ("{", "["):
            close = "}" if first == "{" else "]"
            depth, i = 1, 1
            while i < len(s) and depth > 0:
                if s[i] == first:
                    depth += 1
                elif s[i] == close:
                    depth -= 1
                i += 1
            return s[:i]
        # Quoted string
        if first == '"':
            i = 1
            while i < len(s):
                if s[i] == "\\":
                    i += 2
                elif s[i] == '"':
                    return s[: i + 1]
                else:
                    i += 1
            return s
        # Identifier — may be a simple keyword (string, bool, any, null, true, false)
        # or a parameterised type like object({...}), list(...), optional(...)
        id_m = re.match(r"[a-zA-Z_][a-zA-Z0-9_]*", s)
        if id_m:
            rest = s[id_m.end():]
            paren_m = re.match(r"\s*\(", rest)
            if paren_m:
                # Depth-track parentheses so nested list(object({...})) etc. are captured whole
                paren_pos = id_m.end() + paren_m.end() - 1  # index of '(' in s
                depth, i = 1, paren_pos + 1
                while i < len(s) and depth > 0:
                    if s[i] == "(":
                        depth += 1
                    elif s[i] == ")":
                        depth -= 1
                    i += 1
                return s[:i]
            return id_m.group(0)
        # Number or anything else — read to end of line
        m = re.match(r"[^\n\r]+", s)
        return m.group(0).strip() if m else ""

    # ------------------------------------------------------------------
    # Variable collection & deduplication
    # ------------------------------------------------------------------

    def _collect_variables(self, modules: list[ResolvedModule]) -> list[CatalogVariable]:
        seen: dict[str, CatalogVariable] = {}
        for mod in modules:
            for var in mod.variables:
                if var.name not in seen:
                    seen[var.name] = var
                else:
                    # Promote to required if any module treats it as required
                    if var.required:
                        seen[var.name].default_hcl = None
        return list(seen.values())

    # ------------------------------------------------------------------
    # HCL rendering
    # ------------------------------------------------------------------

    def _render_main(self, modules: list[ResolvedModule], modules_by_name: dict[str, ResolvedModule]) -> str:
        lines: list[str] = [
            "terraform {",
            f'  required_version = "{self.terraform_version}"',
            "",
            "  required_providers {",
            "    google = {",
            '      source  = "hashicorp/google"',
            '      version = ">= 7.17.0"',
            "    }",
            "  }",
        ]

        if self.backend == "local":
            lines += [
                "",
                '  backend "local" {',
                '    path = "./terraform.tfstate"',
                "  }",
            ]
        elif self.backend:
            lines += [
                "",
                f'  backend "{self.backend}" {{',
                "    # TODO: configure backend settings",
                "  }",
            ]

        lines += ["}", ""]

        # NOTE: the `provider "google"` configuration lives in providers.tf
        # (supplied by the repo-api for CI auth); declaring it here too would be a
        # duplicate provider configuration.

        for mod in modules:
            lines.append(f'module "{mod.name}" {{')
            lines.append(f'  source = "{mod.source}"')
            if mod.fetch_error:
                lines.append(f"  # WARNING: {mod.fetch_error}")
                lines.append("  # Add module inputs manually.")
            elif mod.variables:
                lines.append("")
                dep_outputs = self._dependency_outputs(mod, modules_by_name)
                for var in mod.variables:
                    value = dep_outputs.get(var.name, f"var.{var.name}")
                    lines.append(f"  {var.name:<30} = {value}")
            lines += ["}", ""]

        return "\n".join(lines)

    # Output/variable names too generic to trust as an intentional wiring match —
    # virtually every module has its own unrelated "name"/"id"/etc., so matching on
    # these alone is far more likely to be a same-name coincidence (e.g. a "kms"
    # dependency's key `name` output colliding with an unrelated `name` input on the
    # dependent module) than a real wiring intent.
    _GENERIC_OUTPUT_NAMES = {"id", "name", "project_id", "region", "location", "description", "labels"}

    @classmethod
    def _dependency_outputs(
        cls, mod: ResolvedModule, modules_by_name: dict[str, ResolvedModule]
    ) -> dict[str, str]:
        """
        Map each output name exposed by mod's resolved catalog dependencies to a
        `module.<dependency>.<output>` reference, so an input variable with a matching
        (non-generic) name is wired to the dependency's output instead of `var.<input>`.
        """
        outputs: dict[str, str] = {}
        for dep_name in mod.dependencies:
            dep = modules_by_name.get(dep_name)
            if not dep:
                continue
            for output_name in dep.outputs:
                if output_name in cls._GENERIC_OUTPUT_NAMES:
                    continue
                outputs.setdefault(output_name, f"module.{dep_name}.{output_name}")
        return outputs

    def _render_variables(self, variables: list[CatalogVariable]) -> str:
        # Always include project_id and region; skip any module-defined duplicates of these
        top_level = {"project_id", "region"}
        preamble = [
            CatalogVariable(
                name="project_id",
                type_hcl="string",
                description="The GCP project ID.",
                default_hcl=None,
            ),
            CatalogVariable(
                name="region",
                type_hcl="string",
                description="The GCP region for resources.",
                default_hcl='"us-central1"',
            ),
        ]
        module_vars = sorted(
            (v for v in variables if v.name not in top_level),
            key=lambda v: v.name,
        )
        all_vars = preamble + module_vars

        lines: list[str] = []
        for var in all_vars:
            lines.append(f'variable "{var.name}" {{')
            desc = var.description or f"Value for {var.name}."
            lines.append(f'  description = "{desc.replace(chr(34), chr(92) + chr(34))}"')
            lines.append(f"  type        = {var.type_hcl}")
            lines.append(f"  default     = {var.rendered_default()}")
            if var.sensitive:
                lines.append("  sensitive   = true")
            if var.required:
                lines.append("")
                lines.append("  # NOTE: No upstream default — set this before applying.")
            lines += ["}", ""]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # terraform.tfvars generation
    # ------------------------------------------------------------------

    def _render_tfvars(
        self,
        overrides_map: dict[str, Any],
        block_modules: dict[str, list[str]],
        modules_by_name: dict[str, ResolvedModule],
        preamble: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        For each building block that carries override values, route each key to the
        correct any-typed config variable (e.g. 'tier' under 'sql' becomes
        cloud_sql = { tier = "premium" }) and render a terraform.tfvars block.

        Top-level (unmatched) keys are deduplicated across building blocks — if the
        same key appears in multiple blocks with the same value it is written once;
        if values conflict, the later value wins and a warning comment is emitted.

        Optional preamble dict is written first (e.g. project_id, region).
        """
        lines: list[str] = []
        preamble_keys = set(preamble or {})

        # Names of every variable declared across the resolved modules — i.e. the
        # union written to variables.tf. A terraform.tfvars file may only assign
        # declared variables, so any override key outside this set (and not routed
        # into an object config var) cannot be emitted as a bare assignment.
        known_var_names = {v.name for mod in modules_by_name.values() for v in mod.variables}

        # Write preamble vars (project_id, region, etc.) before block sections.
        if preamble:
            preamble_pairs = [(key, self._render_hcl_value(value)) for key, value in preamble.items()]
            lines.extend(self._align_assignments(preamble_pairs, ""))
            lines.append("")

        # Top-level (unmatched) keys are deduplicated across all building blocks and
        # emitted once, after the block sections, so an attribute is never redefined.
        # Later value wins; a warning comment records each overridden value.
        top_level: dict[str, Any] = {}
        top_level_warnings: list[str] = []
        # Overrides that match no variable in their block's modules. Recorded as
        # error comments (never assignments) so the file stays valid while making
        # the misconfiguration visible. Each entry: (block, modules, key, value).
        unmapped: list[tuple[str, list[str], str, Any]] = []

        for block_name, overrides in overrides_map.items():
            if isinstance(overrides, list):
                overrides = {}
            if not overrides:
                continue
            if block_name not in block_modules:
                lines.append(f"# WARNING: building block '{block_name}' not found in catalog — skipped.")
                lines.append("")
                continue

            module_names = block_modules[block_name]

            # Route each key to the matching any-typed config variable across modules.
            var_assignments: dict[str, dict[str, Any]] = {}
            unmatched: dict[str, Any] = dict(overrides)

            for module_name in module_names:
                mod = modules_by_name.get(module_name)
                if not mod:
                    continue
                still_unmatched: dict[str, Any] = {}
                for key, value in unmatched.items():
                    config_var = self._find_config_var_for_key(key, mod)
                    if config_var:
                        var_assignments.setdefault(config_var, {})[key] = value
                    else:
                        still_unmatched[key] = value
                unmatched = still_unmatched

            if var_assignments:
                lines.append(f"# {block_name} (modules: {', '.join(module_names) or 'none'})")
                for var_name, kv in var_assignments.items():
                    lines.append(f"{var_name} = {{")
                    pairs = [(k, self._render_hcl_value(v, indent=1)) for k, v in kv.items()]
                    lines.extend(self._align_assignments(pairs, "  "))
                    lines.append("}")
                lines.append("")

            # Accumulate top-level keys for the consolidated section below.
            for key, value in unmatched.items():
                if key in preamble_keys:
                    if preamble[key] != value:
                        top_level_warnings.append(
                            f"# WARNING: '{key}' from '{block_name}' "
                            f"({self._render_hcl_value(value)}) ignored — set in preamble."
                        )
                    continue
                if key not in known_var_names:
                    # No declared variable — would break terraform if assigned.
                    unmapped.append((block_name, module_names, key, value))
                    continue
                if key in top_level and top_level[key] != value:
                    top_level_warnings.append(
                        f"# WARNING: '{key}' conflict — "
                        f"was {self._render_hcl_value(top_level[key])}, "
                        f"now {self._render_hcl_value(value)} (later value wins)."
                    )
                top_level[key] = value

        # Emit the deduplicated top-level keys once, after all block sections.
        if top_level or top_level_warnings:
            lines.extend(top_level_warnings)
            top_pairs = [(key, self._render_hcl_value(value)) for key, value in top_level.items()]
            lines.extend(self._align_assignments(top_pairs, ""))
            lines.append("")

        # Log override keys that matched no variable as errors — emitting them as
        # assignments would make terraform fail with "Unexpected attribute".
        if unmapped:
            lines.append("# ERRORS: the following overrides have no corresponding variable")
            lines.append("# in their building block's modules and were skipped:")
            for block_name, module_names, key, value in unmapped:
                mods = ", ".join(module_names) or "none"
                lines.append(
                    f"# ERROR: override '{key} = {self._render_hcl_value(value)}' "
                    f"for building block '{block_name}' (modules: {mods}) "
                    f"has no corresponding variable."
                )
            lines.append("")

        while lines and lines[-1] == "":
            lines.pop()
        return ("\n".join(lines) + "\n") if lines else ""

    def _find_config_var_for_key(self, key: str, mod: ResolvedModule) -> Optional[str]:
        """
        Find the config variable that should carry 'key' and return its name.

        Modules describe a block's schema with an object-typed `<name>_default`
        variable that holds a complete set of defaults, plus a loosely-typed
        (`any`) entry variable the user actually populates; the module merges the
        entry over the defaults. The entry variable is named either by stripping
        the `_default` suffix (e.g. dns_default → dns) or after the module itself
        (e.g. gcs owns bucket_default, iam_service_account owns
        service_account_default).

        If 'key' is a direct field of some `<name>_default` object, route it into
        that merge entry variable. The strict `<name>_default` object is never a
        valid target — object types require *every* attribute, so assigning a
        partial value there fails. When no merge entry exists, return None so the
        caller records the key as unmapped rather than emitting invalid HCL.
        """
        by_name = {v.name: v for v in mod.variables}
        for var in mod.variables:
            if not (var.name.endswith("_default") and "object(" in var.type_hcl):
                continue
            if key not in self._extract_object_field_names(var.type_hcl):
                continue
            # Prefer the `<name>` sibling, else the module's own entry variable.
            for candidate in (var.name[: -len("_default")], mod.name):
                entry = by_name.get(candidate)
                if entry is not None and "object(" not in entry.type_hcl:
                    return candidate
            return None
        return None

    @staticmethod
    def _extract_object_field_names(type_hcl: str) -> set[str]:
        """Extract direct (first-level) field names from an object({...}) HCL type."""
        m = re.match(r"object\s*\(\s*\{", type_hcl)
        if not m:
            return set()
        i, depth, chars = m.end(), 1, []
        while i < len(type_hcl) and depth > 0:
            c = type_hcl[i]
            if c in ("{", "("):
                depth += 1
            elif c in ("}", ")"):
                depth -= 1
                if depth == 0:
                    break
            if depth == 1:
                chars.append(c)
            i += 1
        # Only depth-1 characters are collected, so the fields are separated by
        # commas and/or newlines (any commas inside nested type calls are at
        # deeper depth and excluded). Split on both to catch single-line and
        # multi-line object definitions alike.
        names: set[str] = set()
        for part in re.split(r"[,\n]", "".join(chars)):
            fm = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=", part)
            if fm:
                names.add(fm.group(1))
        return names

    @staticmethod
    def _render_hcl_value(value: Any, indent: int = 0) -> str:
        """Render a Python value as a valid HCL literal."""
        pad = "  " * indent
        inner = "  " * (indent + 1)
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return f'"{value}"'
        if isinstance(value, list):
            if not value:
                return "[]"
            if all(not isinstance(v, (list, dict)) for v in value):
                return "[" + ", ".join(CatalogResolver._render_hcl_value(v) for v in value) + "]"
            rendered = [CatalogResolver._render_hcl_value(v, indent + 1) for v in value]
            return "[\n" + ",\n".join(f"{inner}{r}" for r in rendered) + f"\n{pad}]"
        if isinstance(value, dict):
            if not value:
                return "{}"
            pairs = [(k, CatalogResolver._render_hcl_value(v, indent + 1)) for k, v in value.items()]
            body = "\n".join(CatalogResolver._align_assignments(pairs, inner))
            return "{\n" + body + f"\n{pad}}}"
        return f'"{value}"'

    @staticmethod
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
