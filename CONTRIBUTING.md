# Contributing

## One-time setup

1. Install dev dependencies: `uv sync --dev`
2. Install hooks locally: `uv run prek install`
3. Install the commit-msg hook too: `uv run prek install --hook-type commit-msg`

That gives you local checks for:
- commit message format via Commitizen
- `ruff` lint/fix and formatting
- `basedpyright`

## Daily workflow

- Write Conventional Commits (`feat: ...`, `fix: ...`, etc.).
- Before pushing, you can run the same checks as CI:
  - `uv run ruff format --check src tests`
  - `uv run ruff check src tests`
  - `uv run basedpyright`
  - `uv run pytest tests`
- PRs run the same checks in GitHub Actions.
- Same-repo PRs also get a Commitizen bump preview comment showing the version bump that would happen after merge. Fork PRs are skipped for safety.

## Release process

Releases are driven from commits merged to `main`.

- `.github/workflows/bumpversion.yml` runs on pushes to `main`.
- It reruns formatting, lint, type checking, and tests before releasing.
- If no `v0.0.0` tag exists yet, the first successful run bootstraps the project:
  - generates `CHANGELOG.md`
  - creates commit `bump: release 0.0.0`
  - tags `v0.0.0`
  - creates the GitHub release
- After that, Commitizen determines the next version from Conventional Commits, updates `pyproject.toml`, updates `CHANGELOG.md`, creates the release commit/tag, and creates the GitHub release notes from the incremental changelog.
- `.github/workflows/publish.yml` runs on pushed tags matching `v*`, builds both sdist and wheel with `uv build --no-sources`, smoke-tests both artifacts, then publishes to PyPI.

## Required GitHub and PyPI setup

You need to do these steps once:

### 1. Add the PAT used for version bumps

Create a GitHub personal access token and save it as repository secret `PERSONAL_ACCESS_TOKEN`.

It is used only by `.github/workflows/bumpversion.yml`, which runs on pushes to `main` and manual dispatch. It is not exposed to arbitrary PRs.

### 2. Create the PyPI environment in GitHub

In the GitHub repo, create an environment named `pypi`.

The publish workflow expects exactly that environment name.

### 3. Configure PyPI trusted publishing

In PyPI, add this repository as a trusted publisher for project `uminer`.
Use these values:

- owner: `shyndman`
- repository: `uminer`
- workflow: `publish.yml`
- environment: `pypi`

After that, tag-triggered publishes can use `uv publish` without storing a PyPI token in GitHub.

## Notes

- Current versioning is PEP 621 + Commitizen, sourced from `pyproject.toml`.
- Tags use the format `vX.Y.Z`.
- `major_version_zero = true` is enabled, so breaking changes still stay under major version `0` until you decide otherwise.
