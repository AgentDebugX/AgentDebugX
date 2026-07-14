# Runtime Infrastructure

`agentdebug.runtime` provides shared infrastructure used by workflows without
owning Diagnose or Rerun business logic.

## Responsibilities

- `storage.py` defines the `TraceStore` protocol and JSONL/SQLite stores.
- `llm.py` defines model and embedding protocols plus the OpenAI-compatible
  client.
- `events.py` provides the in-process event bus used by instrumentation and
  integrations.
- `plugins/` contains the general plugin registry and metadata types.
- `llm_channel.py` and `gui_taxonomy.py` bridge optional computer-use support
  without making GUI dependencies mandatory.

## Boundaries

- Runtime code may provide transport, persistence, and client abstractions.
- Diagnose owns detection, attribution, and recovery decisions.
- Rerun owns execution requests, runner protocols, and branch evaluation.
- Schema owns portable serialized contracts.
- Optional dependencies must remain lazy so the base package stays importable.

New infrastructure should expose a narrow protocol and keep network, storage,
and process side effects explicit to callers.
