# GUI / CUA Debugger Commands

The GUI RCA pipeline ships inside the `agentdebug` package as `agentdebug.gui`.
It expects OSWorld-style trajectory logs; it does not include OSWorld runtime or
agent runner code.

Everything below resolves paths against your current working directory, so run
these commands from the directory that holds your `results/` tree.

## Install

The RCA main path (`agentdebug.gui.rca`, `.ingester`, `.taxonomy`, `.tagger`)
needs nothing beyond a core install. The pipeline, provider adapters and
annotation UI are optional:

```bash
pip install "agentdebugx[gui]"                       # + screenshot decoding
pip install "agentdebugx[gui,gui-app]"               # + providers, pipeline, UI
pip install "agentdebugx[gui,gui-memory,gui-app]"    # + lesson/episodic memory
```

## Configuration

Configuration is resolved in this order, first hit wins:

1. `$AGENTDEBUG_GUI_CONFIG`
2. `~/.agentdebug/gui.json`
3. `./debugger/config/debugger.json`
4. the built-in defaults

A copy of the defaults ships with the package as a starting point:

```bash
python -c "from pathlib import Path; import agentdebug.gui.config as c; \
print(Path(c.__file__).parent / 'config' / 'debugger.example.json')"
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
python -m agentdebug.gui \
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

`streamlit run` needs a file path, so ask the package where it installed the app:

```bash
streamlit run "$(python -c 'from agentdebug.gui.vis import app_path; print(app_path())')"
```

For read-only inspection, use the main FastAPI dashboard instead:

```bash
pip install "agentdebugx[ui]"
agentdebug serve --store-sqlite .agentdebug/traces.sqlite
```

An imported OSWorld trace with screenshots beneath its recorded `source_dir`
opens in **Visual** mode automatically. The shared timeline controls the
screenshot, step metadata, click marker, and RCA evidence; **Trace / Visual**
switches views without rerunning diagnosis. Streamlit is still required for
annotation writes, reviewer assignment, discussion, and accuracy tooling.

## Accuracy

Pass the debugger subdirectory, not the agent-level directory:

```python
from agentdebug.gui.eval import quick_acc, compute_accuracy
quick_acc("results/debugger_results/<trial>/<debugger>")
compute_accuracy("results/debugger_results/<trial>/<debugger>")
```

## Other entry points

```bash
python -m agentdebug.gui.scripts.download_input_trajectory --help
python -m agentdebug.gui.vis.generate_assignments results/debugger_results/<trial>
```
