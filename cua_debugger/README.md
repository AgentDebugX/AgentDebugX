# CUA-Agent-Debugger

CUA-Agent-Debugger is a minimal open-source release of the debugger code used for analyzing computer-use agent trajectories. It focuses on root-cause analysis, taxonomy tagging, annotation review, and lightweight evaluation for OSWorld-style trajectory logs.

This repository does not vendor OSWorld runtime code, VM providers, third-party agent implementations, private experiment logs, paper drafts, or large rerollout artifacts. Bring your own trajectories, or use the optional downloader to place input trajectories under `results/input_trajectory`.

## What Is Included

- `debugger/`: RCA pipeline, trajectory loading, taxonomy, evaluation, memory helpers, and annotation UI.
- `debugger/vis/debugger_app.py`: Streamlit app for inspecting RCA outputs and human annotations.
- `results/debugger_results/claude-sonnet-4-5-20250929_50steps/annotations/`: Claude 50-step human/debugger annotation JSONs and annotation summary metadata.
- `tests/`: focused unit tests for the minimal release.
- `scripts/download_input_trajectory.py`: optional downloader/unzip helper for public input trajectories.

## What Is Not Included

- OSWorld runtime and VM management code.
- Third-party agent runner implementations.
- Full paper experiment trajectories, rerollouts, and model RCA outputs.
- Private API keys, private base URLs, or local config files.

## Included Annotations

This minimal release includes only the Claude 50-step annotation set under:

```text
results/debugger_results/claude-sonnet-4-5-20250929_50steps/
```

The tracked files are `annotations/*.json`, `annotation_agreement.json`, `annotation_assignments.json`, and `classification.json`. Deprecated Claude 15-step annotations and debugger model output folders are intentionally excluded from this release.

## Quickstart

```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell users can run: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest tests -q
```

Launch the annotation UI:

```bash
streamlit run debugger/vis/debugger_app.py
```

## Optional Input Trajectories

The repository does not track trajectory data. To run the debugger on the optional Claude 50-step OSWorld-Verified trajectories, download the archive from Hugging Face:

```text
https://huggingface.co/datasets/xlangai/ubuntu_osworld_verified_trajs/blob/main/claude-sonnet-4-5-20250929_50steps.zip
```

The file is large, about 5.38 GB before extraction. Use the included downloader to stream the zip to disk and unzip it under `results/input_trajectory`:

```bash
python scripts/download_input_trajectory.py
```

By default this writes:

```text
results/input_trajectory/claude-sonnet-4-5-20250929_50steps.zip
```

and extracts the contents into:

```text
results/input_trajectory/
```

Useful options:

```bash
# Download only, without unzipping
python scripts/download_input_trajectory.py --no-extract

# Re-download even if the zip already exists
python scripts/download_input_trajectory.py --force

# Delete the zip after successful extraction
python scripts/download_input_trajectory.py --delete-zip
```

After extraction, point the debugger at the extracted trajectory folder. If the zip creates a top-level `claude-sonnet-4-5-20250929_50steps/` directory, run:

```bash
python -m debugger \
  --trajectory-dir results/input_trajectory/claude-sonnet-4-5-20250929_50steps \
  --output-dir results/debugger_results \
  --trial-name claude-sonnet-4-5-20250929_50steps \
  --provider openai \
  --model gpt-4o-mini
```

## Running RCA

Copy the example config and edit paths/model settings:

```bash
cp debugger/config/debugger.example.json debugger/config/debugger.json
```

Set API keys through environment variables only:

```bash
export OPENAI_API_KEY=...
```

Then run:

```bash
python -m debugger \
  --trajectory-dir results/input_trajectory/claude-sonnet-4-5-20250929_50steps \
  --output-dir results/debugger_results \
  --trial-name claude-sonnet-4-5-20250929_50steps \
  --provider openai \
  --model gpt-4o-mini
```

RCA runs require trajectories with `traj.jsonl` or `trajectory.jsonl` files.

## Repository Policy

Do not commit `.env`, `debugger/config/debugger.json`, full `results/`, or large logs. Use external artifacts such as Hugging Face Datasets or GitHub Releases for full paper data.
