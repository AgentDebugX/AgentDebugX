# Debugger Commands

Paths below are repo-root-relative. This minimal release expects OSWorld-style trajectory logs, but it does not include OSWorld runtime or agent runner code.

## Configuration

Copy the safe template if you want a local config:

```bash
cp debugger/config/debugger.example.json debugger/config/debugger.json
```

API keys must be set through environment variables, never committed in JSON files:

| provider | env var | base URL |
|---|---|---|
| `openai` | `OPENAI_API_KEY` | defaults to `https://api.openai.com/v1` |
| `anthropic` | `ANTHROPIC_API_KEY` | native Anthropic SDK |
| `together` | `TOGETHER_API_KEY` | native Together SDK |
| `gemini` | `GEMINI_API_KEY` | set `GEMINI_BASE_URL` or `base_urls.gemini` |
| custom OpenAI-compatible alias | `<ALIAS>_API_KEY` | set `<ALIAS>_BASE_URL` or `base_urls.<alias>` |

## Run RCA

```bash
python -m debugger \
  --trajectory-dir results/input_trajectory/claude-sonnet-4-5-20250929_50steps \
  --output-dir results/debugger_results \
  --trial-name claude-sonnet-4-5-20250929_50steps \
  --provider openai \
  --model gpt-4o-mini
```

## Result Layout

```text
results/debugger_results/
  <trial_name>/
    annotations/
    <debugger_model>/
      rca/
      summary.json
      episodic.json
```

## Annotation UI

```bash
streamlit run debugger/vis/debugger_app.py
```

## Accuracy

Pass the debugger subdirectory, not the agent-level directory:

```python
from debugger.eval import quick_acc, compute_accuracy
quick_acc("results/debugger_results/<trial>/<debugger>")
compute_accuracy("results/debugger_results/<trial>/<debugger>")
```
