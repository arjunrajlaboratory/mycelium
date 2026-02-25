# Environment Setup Conventions

Best practices for managing environments, dependencies, and installations in a mycelium-enabled repository.

## Python Environment Management

### Recommended: `uv`

[uv](https://github.com/astral-sh/uv) is the recommended package manager for new projects. It's fast, handles virtual environments automatically, and produces deterministic lock files.

```bash
# Create a new project with uv
uv init
uv add pandas numpy scipy matplotlib seaborn

# Or create a venv and install from requirements
uv venv
uv pip install -r requirements.txt
```

**Tradeoffs**: Excellent for pure-Python projects. May struggle with complex compiled dependencies (e.g., CUDA-linked libraries).

### Alternative: `conda` / `mamba`

Use conda when you need:
- Non-Python dependencies (R, system libraries, CUDA toolkit)
- Compiled packages that are painful to build from source
- Cross-language environments (Python + R)

```bash
# Create environment from file
conda env create -f environment.yml
conda activate project-env
```

**Tradeoffs**: Slower resolution than uv, larger environments, but handles compiled dependencies much better.

### Decision Guide

| Situation | Use |
|-----------|-----|
| Pure Python project | `uv` |
| Needs R packages | `conda` |
| Needs CUDA/GPU libraries | `conda` |
| Needs system-level dependencies | `conda` |
| CI/CD pipelines | `uv` (faster) |
| Mixed Python + R | `conda` |

## R Environment Management

For R-heavy projects, use `renv` for reproducibility:

```r
renv::init()
renv::snapshot()
renv::restore()
```

If using both R and Python, manage the Python side with conda and R with renv inside the conda environment.

## Documenting in ENVIRONMENTS_INSTALLATIONS.md

Every dependency must be documented with the exact install command AND any gotchas encountered. This file lives at the repo root.

### Template

```markdown
# Environments & Installations

## Primary Environment

- **Manager**: uv / conda / other
- **Python version**: 3.11.x
- **Created**: YYYY-MM-DD
- **Last updated**: YYYY-MM-DD

### Setup from scratch

\```bash
# Step-by-step commands to recreate the environment
uv venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt
\```

## Dependencies

### [package-name] (version)
- **Install**: `uv add package-name` or `conda install package-name`
- **Purpose**: Brief description of why this is needed
- **Gotchas**: Any issues encountered during installation or use
- **Platform notes**: Any platform-specific considerations

## System Dependencies

### [system-dep-name]
- **Install (Ubuntu)**: `sudo apt-get install ...`
- **Install (macOS)**: `brew install ...`
- **Required by**: Which Python/R packages need this
- **Gotchas**: Any issues encountered

## GPU / CUDA (if applicable)

- **CUDA version**: X.Y
- **cuDNN version**: X.Y.Z
- **Driver version**: XXX.XX
- **Setup notes**: How CUDA was configured
```

## Common Gotchas

### System Dependencies
- Many Python packages with C extensions need build tools: `build-essential` (Ubuntu) or Xcode Command Line Tools (macOS)
- `libhdf5-dev` is needed for h5py/anndata
- `libffi-dev` is needed for some cryptography packages

### CUDA
- CUDA toolkit version must match the PyTorch/TensorFlow build
- `nvidia-smi` shows driver version, not toolkit version — they're different
- Multiple CUDA versions can coexist; use `CUDA_HOME` to select

### Platform-Specific
- macOS ARM (M1/M2/M3): Some packages need `arch -arm64` prefix or Rosetta
- Windows WSL: GPU passthrough requires specific driver versions
- Linux: Check `ldd` output if shared libraries are missing

### PyYAML
- Required by mycelium scripts for manifest parsing
- Install: `uv add pyyaml` or `conda install pyyaml`
- Already included in many environments via other packages

## Convention

**Every time you install something, update `ENVIRONMENTS_INSTALLATIONS.md` immediately.** Don't batch these updates — they're easy to forget, and a missing dependency is the most common cause of "works on my machine" failures.

When you encounter a gotcha during installation, document it right away. Future-you (or future-AI) will thank you.
