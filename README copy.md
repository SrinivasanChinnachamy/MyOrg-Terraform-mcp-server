# srini-terraform-mcp-server

MCP server that connects Kiro to your private Terraform module registry on GitHub.

## What it does

- **search_modules** — Discover modules matching `srini-terraform-aws-*` repos
- **get_module** — Fetch variables, outputs, and README for a module
- **scaffold_terraform** — Generate a full Terraform config (main.tf, backend.tf, etc.)
- **list_module_versions** — List available git tags for a module

## Setup

### 1. Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- GitHub Personal Access Token with `repo` scope

### 2. Environment Variables

```bash
export GITHUB_TOKEN="ghp_your_token_here"
export GITHUB_ORG="your-github-org"
```

### 3. Configure in Kiro

The MCP config is already set up in `.kiro/settings/mcp.json`.
Update these values:

- `GITHUB_TOKEN` — your PAT (or use `${GITHUB_TOKEN}` to read from env)
- `GITHUB_ORG` — your GitHub org or username
- `TF_BACKEND_BUCKET` — your S3 state bucket name
- `TF_BACKEND_REGION` — your state bucket region

### 4. Local Development

```bash
cd terraform-module-mcp-server
uv run srini-terraform-mcp-server
```

## Usage in Kiro

Just describe what you need in natural language:

> "I need a VPC with 3 private subnets for the payments team in prod"

Kiro will:
1. Search your private modules for a VPC module
2. Inspect its variables and outputs
3. Generate a complete, standards-compliant Terraform config
4. Write the files to your workspace

## Conventions Enforced

- Required tags: Environment, Team, CostCenter, ManagedBy
- S3 backend with encryption
- Terraform >= 1.14
- AWS provider >= 5.0
