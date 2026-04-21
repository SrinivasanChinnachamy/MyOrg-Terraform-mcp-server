"""
MCP Server for discovering and scaffolding Terraform modules
from private GitHub repos following the myorg-terraform-aws-* naming convention.
"""

import json
import os
import re
import base64
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")  # org or user
MODULE_PREFIX = os.environ.get("MODULE_PREFIX", "myorg-terraform-aws-")
GITHUB_API = "https://api.github.com"

# ---------------------------------------------------------------------------
# Conventions (defaults – can be overridden via env)
# ---------------------------------------------------------------------------
REQUIRED_TAGS = ["Environment", "Team", "CostCenter"]
TF_MIN_VERSION = os.environ.get("TF_MIN_VERSION", "1.14")

mcp = FastMCP("myorg-terraform-mcp-server")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _service_name_from_repo(repo_name: str) -> str:
    """Extract the AWS service name from a repo name like myorg-terraform-aws-vpc."""
    return repo_name.replace(MODULE_PREFIX, "")


async def _github_get(path: str, params: dict | None = None) -> Any:
    """Make an authenticated GET request to the GitHub API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API}{path}",
            headers=_headers(),
            params=params or {},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


async def _get_file_content(repo_full_name: str, file_path: str, ref: str = "main") -> str | None:
    """Fetch a file's decoded content from a GitHub repo."""
    try:
        data = await _github_get(
            f"/repos/{repo_full_name}/contents/{file_path}",
            params={"ref": ref},
        )
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8")
        return data.get("content", "")
    except httpx.HTTPStatusError:
        return None


def _parse_variables(content: str) -> list[dict]:
    """Parse variable blocks from a variables.tf file."""
    variables = []
    pattern = re.compile(
        r'variable\s+"(\w+)"\s*\{([^}]*)\}', re.DOTALL
    )
    for match in pattern.finditer(content):
        name = match.group(1)
        body = match.group(2)
        var: dict[str, Any] = {"name": name}
        # extract description
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', body)
        if desc_match:
            var["description"] = desc_match.group(1)
        # extract type
        type_match = re.search(r'type\s*=\s*(\S+)', body)
        if type_match:
            var["type"] = type_match.group(1)
        # extract default
        default_match = re.search(r'default\s*=\s*"?([^"\n]*)"?', body)
        if default_match:
            var["default"] = default_match.group(1).strip()
        variables.append(var)
    return variables


def _parse_outputs(content: str) -> list[dict]:
    """Parse output blocks from an outputs.tf file."""
    outputs = []
    pattern = re.compile(
        r'output\s+"(\w+)"\s*\{([^}]*)\}', re.DOTALL
    )
    for match in pattern.finditer(content):
        name = match.group(1)
        body = match.group(2)
        out: dict[str, Any] = {"name": name}
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', body)
        if desc_match:
            out["description"] = desc_match.group(1)
        outputs.append(out)
    return outputs


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_modules(keyword: str = "") -> str:
    """Search available Terraform modules in the private GitHub registry.

    Discovers repos matching the myorg-terraform-aws-* naming convention.
    Optionally filter by keyword (e.g., 'vpc', 'eks', 'rds').

    Args:
        keyword: Optional keyword to filter modules by AWS service name.

    Returns:
        JSON list of matching modules with name, service, description, and URL.
    """
    # Search for repos with the module prefix
    query = f"{MODULE_PREFIX}{keyword} in:name"
    if GITHUB_ORG:
        query += f" org:{GITHUB_ORG}"

    data = await _github_get("/search/repositories", params={"q": query, "per_page": 50})

    modules = []
    for repo in data.get("items", []):
        if not repo["name"].startswith(MODULE_PREFIX):
            continue
        modules.append({
            "name": repo["name"],
            "service": _service_name_from_repo(repo["name"]),
            "description": repo.get("description", ""),
            "url": repo["html_url"],
            "default_branch": repo.get("default_branch", "main"),
            "updated_at": repo.get("updated_at", ""),
        })

    if not modules:
        return json.dumps({"message": f"No modules found matching '{keyword}'. Kindly reachout to platform engineering team[platform-team-dl@yourcompany.com] with a business/technical requirement to propose a new module request", "modules": []})

    return json.dumps({"count": len(modules), "modules": modules}, indent=2)


@mcp.tool()
async def get_module(
    service_name: str,
    ref: str = "main",
) -> str:
    """Get detailed information about a specific Terraform module.

    Fetches the module's variables, outputs, and README from GitHub.

    Args:
        service_name: The AWS service name (e.g., 'vpc', 'eks', 'rds').
        ref: Git ref (branch/tag) to fetch from. Defaults to 'main'.

    Returns:
        JSON with module variables, outputs, README, and usage example.
    """
    repo_name = f"{MODULE_PREFIX}{service_name}"
    repo_full = f"{GITHUB_ORG}/{repo_name}" if GITHUB_ORG else repo_name

    # Fetch variables.tf, outputs.tf, and README concurrently
    variables_content = await _get_file_content(repo_full, "variables.tf", ref)
    outputs_content = await _get_file_content(repo_full, "outputs.tf", ref)
    readme_content = await _get_file_content(repo_full, "README.md", ref)

    variables = _parse_variables(variables_content) if variables_content else []
    outputs = _parse_outputs(outputs_content) if outputs_content else []

    result = {
        "module": repo_name,
        "service": service_name,
        "source": f"git::https://github.com/{repo_full}.git?ref={ref}",
        "variables": variables,
        "outputs": outputs,
        "readme": readme_content[:3000] if readme_content else "No README found.",
    }

    return json.dumps(result, indent=2)


@mcp.tool()
async def scaffold_terraform(
    service_name: str,
    module_variables: str = "{}",
    environment: str = "dev",
    team: str = "platform",
    cost_center: str = "engineering",
    ref: str = "main",
) -> str:
    """Generate a complete Terraform configuration using a private module.

    Creates main.tf, variables.tf, outputs.tf, and providers.tf
    following team conventions (required tags, provider pinning).
    Backend/state is managed externally by the CI/CD pipeline.

    Args:
        service_name: AWS service name (e.g., 'vpc', 'eks', 'rds').
        module_variables: JSON string of variable overrides for the module.
        environment: Environment name (dev, staging, prod). Default: 'dev'.
        team: Team name for tagging. Default: 'platform'.
        cost_center: Cost center for tagging. Default: 'engineering'.
        ref: Git ref (branch/tag) for the module source. Default: 'main'.

    Returns:
        JSON with generated file contents for main.tf, variables.tf, outputs.tf,
        and providers.tf. Backend config is not included (managed by pipeline).
    """
    repo_name = f"{MODULE_PREFIX}{service_name}"
    repo_full = f"{GITHUB_ORG}/{repo_name}" if GITHUB_ORG else repo_name
    source = f"git::https://github.com/{repo_full}.git?ref={ref}"

    # Parse variable overrides
    try:
        var_overrides = json.loads(module_variables)
    except json.JSONDecodeError:
        var_overrides = {}

    # Fetch module variables to build the config
    variables_content = await _get_file_content(repo_full, "variables.tf", ref)
    outputs_content = await _get_file_content(repo_full, "outputs.tf", ref)
    module_vars = _parse_variables(variables_content) if variables_content else []
    module_outs = _parse_outputs(outputs_content) if outputs_content else []

    # --- providers.tf ---
    providers_tf = f'''terraform {{
  required_version = ">= {TF_MIN_VERSION}"

  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }}
  }}
}}

provider "aws" {{
  default_tags {{
    tags = {{
      Environment = var.environment
      Team        = var.team
      CostCenter  = var.cost_center
      ManagedBy   = "terraform"
    }}
  }}
}}
'''

    # --- variables.tf ---
    variables_tf = f'''variable "environment" {{
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "{environment}"
}}

variable "team" {{
  description = "Team name for resource tagging"
  type        = string
  default     = "{team}"
}}

variable "cost_center" {{
  description = "Cost center for resource tagging"
  type        = string
  default     = "{cost_center}"
}}
'''
    # Add module-specific variables that don't have defaults
    for v in module_vars:
        if "default" not in v and v["name"] not in var_overrides:
            vtype = v.get("type", "string")
            desc = v.get("description", f"Value for {v['name']}")
            variables_tf += f'''
variable "{v['name']}" {{
  description = "{desc}"
  type        = {vtype}
}}
'''

    # --- main.tf ---
    var_lines = []
    for v in module_vars:
        vname = v["name"]
        if vname in var_overrides:
            val = var_overrides[vname]
            if isinstance(val, str):
                var_lines.append(f'  {vname} = "{val}"')
            else:
                var_lines.append(f'  {vname} = {json.dumps(val)}')
        elif "default" not in v:
            var_lines.append(f'  {vname} = var.{vname}')

    var_block = "\n".join(var_lines)
    main_tf = f'''module "{service_name}" {{
  source = "{source}"

{var_block}
}}
'''

    # --- outputs.tf ---
    output_lines = []
    for o in module_outs:
        oname = o["name"]
        desc = o.get("description", oname)
        output_lines.append(f'''output "{oname}" {{
  description = "{desc}"
  value       = module.{service_name}.{oname}
}}
''')
    outputs_tf = "\n".join(output_lines) if output_lines else "# No outputs defined in the module\n"

    result = {
        "files": {
            "providers.tf": providers_tf,
            "variables.tf": variables_tf,
            "main.tf": main_tf,
            "outputs.tf": outputs_tf,
        },
        "instructions": (
            f"Generated Terraform config for module '{repo_name}'.\n"
            f"Source: {source}\n"
            f"Note: Backend/state config is managed by your CI/CD pipeline.\n\n"
            "Next steps:\n"
            "1. Review and adjust variable values\n"
            "2. Run: terraform init\n"
            "3. Run: terraform plan\n"
            "4. Run: terraform apply"
        ),
    }

    return json.dumps(result, indent=2)


@mcp.tool()
async def list_module_versions(service_name: str) -> str:
    """List available versions (git tags) for a Terraform module.

    Args:
        service_name: AWS service name (e.g., 'vpc', 'eks', 'rds').

    Returns:
        JSON list of available tags/versions for the module.
    """
    repo_name = f"{MODULE_PREFIX}{service_name}"
    repo_full = f"{GITHUB_ORG}/{repo_name}" if GITHUB_ORG else repo_name

    try:
        tags = await _github_get(f"/repos/{repo_full}/tags", params={"per_page": 50})
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"Could not fetch tags: {e.response.status_code}"})

    versions = [{"name": t["name"], "sha": t["commit"]["sha"]} for t in tags]
    return json.dumps({
        "module": repo_name,
        "versions": versions,
        "count": len(versions),
    }, indent=2)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
