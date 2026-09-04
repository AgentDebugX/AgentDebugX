# Taxonomy manifest and classification contract

`agentdebug.taxonomy_manifest` freezes `SEED_FAILURE_MODES` into a JSON-serialisable
document with a content fingerprint and the package revision it came from, and defines a
side-effect-free classification contract on top of it. A label stored in a corpus is only
interpretable if the reader can recover the exact definitions the labeller saw; the
fingerprint is how a corpus notices that its labels and its taxonomy drifted apart.

```python
from agentdebug.taxonomy_manifest import (
    ClassificationRequest, cohens_kappa, compile_classification_prompt,
    parse_classification_response, taxonomy_manifest,
)

manifest = taxonomy_manifest()             # 23 modes in 9 families, fingerprint, source_revision
system, user = compile_classification_prompt(manifest, ClassificationRequest(
    trace_uid="tr-1", task_statement="...", outcome_text="failed", window=rendered_tail))
reply = my_client.complete(system=system, user=user)   # the consumer owns the call and its budget
result = parse_classification_response(reply.text, manifest)
```

`ClassificationResult` carries `mode_id`, `family`, optional `submode`, `decisive_step`,
`evidence_quote`, `rationale`, `confidence` in [0, 1], and an explicit `abstain` with one of
`ABSTAIN_REASONS` (`infrastructure_fault`, `decisive_step_not_in_window`, `ambiguous`,
`unknown_mode`, `malformed_response`, `unparsed`). Infrastructure faults and ambiguous cases
are never forced into an agent-error family. `parse_classification_response` never raises:
fenced JSON, trailing prose, an unknown mode id, duplicate keys and malformed JSON all map to a
result or an abstention with the raw object preserved.

The grouping from leaf modes to families keeps every family, including `verification`,
`multiagent`, `multimodal` and `observation`. `cohens_kappa(labels_a, labels_b)` measures
two-judge agreement over the items both labelled and returns `None` where kappa is undefined.

Consumers keep provider calls, budgets, metering, trace binding and append-only storage; a
row should record the manifest `fingerprint`, `source_revision` and the SHA-256 of the
compiled prompt so the classification is reproducible from the stored reply.
