# LVM Operator Development Environment

A workspace template for developing and testing the **LVM Storage Operator** - an OpenShift local storage operator utilizing the TopoLVM CSI driver which wraps LVM storage utilities.

## Quick Start

```bash
# Clone this repo as your workspace
git clone https://github.com/openshift-eng/edge-tooling.git
cd environments/lvm-operator

# Clone all LVM Operator related repositories
./setup.sh
```

## How It Works

All LVMS source repositories are cloned into the `repos/` folder. The `CLAUDE.md` file provides Claude Code with context about each repository's role in LVMS, enabling AI-assisted development across the entire codebase.

**Workflow:**
1. Work with Claude from this top-level directory for cross-repo context
2. Navigate into `repos/<repo-name>/` to implement changes
3. Use `./setup.sh update` to pull latest changes

## Setup Script Commands

| Command | Description |
|---------|-------------|
| `./setup.sh` | Clone all repositories (first-time setup) |
| `./setup.sh update` | Pull latest changes for all repos |
| `./setup.sh status` | Show clone status and current branches |
| `./setup.sh list` | List configured repositories |

## Release Branching

The `branch-release.sh` script automates the LVMS Y-stream release branching procedure across multiple repositories.

```bash
# Preview changes with dry run
./branch-release.sh --old-version 4.21 --new-version 4.22 \
  --lvm-dir ~/repos/lvm-operator \
  --konflux-dir ~/repos/konflux-release-data \
  --prodsec-dir ~/repos/product-definitions \
  --dry-run

# Run interactively
./branch-release.sh --old-version 4.21 --new-version 4.22 \
  --lvm-dir ~/repos/lvm-operator \
  --konflux-dir ~/repos/konflux-release-data \
  --prodsec-dir ~/repos/product-definitions
```

See `CLAUDE.md` for detailed documentation on all script options and steps.

## Configuration

Edit `repos.txt` to customize which repositories to clone and which branches to use. The file is created from `repos.txt.template` on first run.

## Requirements

- Git
- [Claude Code](https://claude.ai/code) (recommended for AI-assisted development)
- For release branching: `kustomize`, `jq`, `sed`