# Root Packaging Example

This directory demonstrates the Roots packaging workflow: pack, inspect, install, and run.

## Contents

- `process.yaml` — A sample process with 3 agent nodes and a deterministic decision gate
- `defaults/` — Default agent implementations bundled with the package
- `sample-review-1.0.0.root` — Pre-built `.root` package (ready to install)
- `install_and_run.py` — Script that installs and runs the package

## Workflow

### 1. Pack a process into a `.root` archive

```bash
roots pack examples/packaging/process.yaml \
  --include-defaults examples/packaging/defaults \
  --version 1.0.0 \
  --author "Your Name"
```

### 2. Inspect the package

```bash
roots inspect sample-review-1.0.0.root
```

### 3. Install the package

```bash
roots install sample-review-1.0.0.root --apply-defaults
```

### 4. Run the installed process

```bash
roots run sample-review --work-item '{"source": "example"}'
```

### Quick demo (all-in-one script)

```bash
python examples/packaging/install_and_run.py
```

## How it works

The `sample-review` process:

1. **Ingest** — Normalizes incoming data (sets `quality: "good"`)
2. **Quality Gate** — Deterministic decision routes to `enrich` or `review` based on quality
3. **Enrich** or **Review** — Processes the data through the appropriate path
4. **Done** — Marks the run as completed

Default agent implementations are bundled in the `defaults/` directory and automatically registered when installing with `--apply-defaults`.
