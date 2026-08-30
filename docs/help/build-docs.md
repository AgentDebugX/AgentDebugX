# Build these docs

The documentation site is built from Markdown under `docs/` with MkDocs Material.

## Install documentation dependencies

From the repository root:

```bash
python -m pip install -r requirements-docs.txt
```

## Preview locally

```bash
mkdocs serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). MkDocs reloads the preview when a source file changes.

## Run the same strict build as CI

```bash
mkdocs build --strict
```

The generated `site/` directory is a build artifact and should not be committed.

## Source-of-truth rules

When updating a tutorial:

1. verify CLI flags against `agentdebug <command> --help`,
2. verify Python behavior against the implementation and focused tests,
3. distinguish deterministic results, model-produced analysis, simulation, and observed live execution,
4. do not copy experiment metrics into usage documentation without a versioned source, and
5. keep optional dependency boundaries aligned with `pyproject.toml`.

## GitHub Pages deployment

`.github/workflows/docs.yml` builds documentation for pull requests and deploys the `main` branch through GitHub Pages. In the repository settings, set **Pages → Build and deployment → Source** to **GitHub Actions**.

The configured site URL is:

```text
https://docs.agentdebugx.com/
```

Deployment is separate from the GitHub Wiki. The Markdown sources remain versioned with the code in this repository.
