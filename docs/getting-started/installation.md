# Installation

AgentDebugX supports Python 3.9 through 3.13. Install the base package with pip:

```bash
python -m pip install agentdebugx
```

Verify that the CLI and import are available:

```bash
agentdebug --help
python -c "import agentdebug; print(agentdebug.__version__)"
```

## Optional features

The base install includes the portable schema, raw ingest, deterministic diagnosis, rerun planning, and the core GUI RCA package. Install extras only for the integrations you use.

| Extra | Install command | Adds |
| --- | --- | --- |
| Local inspection UI | `pip install "agentdebugx[ui]"` | FastAPI and Uvicorn |
| LangGraph | `pip install "agentdebugx[langgraph]"` | LangGraph adapter dependency |
| CrewAI | `pip install "agentdebugx[crewai]"` | CrewAI event adapter |
| OpenAI Agents SDK | `pip install "agentdebugx[openai-agents]"` | OpenAI Agents tracing bridge |
| OpenTelemetry | `pip install "agentdebugx[otel]"` | OTel import/export support |
| GUI screenshot decoding | `pip install "agentdebugx[gui]"` | Pillow |
| GUI memory | `pip install "agentdebugx[gui-memory]"` | Lesson and episodic memory dependencies |
| GUI batch app | `pip install "agentdebugx[gui-app]"` | Provider adapters, batch pipeline, and Streamlit app |
| Hugging Face Hub | `pip install "agentdebugx[hub-hf]"` | Hugging Face bundle backend |
| Everything declared by the project | `pip install "agentdebugx[all]"` | All optional integrations |

!!! important "GUI extras are split by responsibility"

    Importing `agentdebug.gui` and using the core RCA surface does not require the heavy GUI application stack. Pillow is only needed to decode screenshots. The lesson-memory and batch-application layers have separate extras.

## Install from this repository

For local development:

```bash
git clone https://github.com/AgentDebugX/AgentDebugX.git
cd AgentDebugX
python -m pip install -e ".[ui]"
```

Run a quick health check:

```bash
agentdebug doctor
python -m pytest tests -q
```

The full GUI test matrix needs the three GUI-related extras:

```bash
python -m pip install -e ".[gui,gui-memory,gui-app]"
python -m pytest tests/gui -q
```
