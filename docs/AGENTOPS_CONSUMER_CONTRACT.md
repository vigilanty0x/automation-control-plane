# AgentOps consumer inventory contract

The target must not deprecate or archive a source until consumers have been inventoried. This rehearsal adds a strict, deterministic contract for that evidence without pretending that the live inventory has already been completed.

## Required coverage

A complete inventory covers every source in `AGENTOPS_SOURCE_INVENTORY.json` and records whether the following consumer classes were scanned:

- imports;
- packages;
- workflows;
- documentation references;
- forks;
- pilot users or explicitly recorded adopters.

The `agentops consumers` command fails closed when repository coverage is partial or when any consumer class has not been scanned. Duplicate source entries, duplicate evidence references, unknown source repositories, unknown consumer kinds, floats, duplicate JSON members, and unbounded inputs are blocked.

## Input shape

```json
{
  "scan_scope": {
    "observed_at": "2026-08-18T03:00:00Z",
    "expires_at": "2026-09-17T03:00:00Z",
    "repositories_expected": 3,
    "repositories_scanned": 3,
    "complete_kinds": ["documentation", "fork", "import", "package", "pilot", "workflow"]
  },
  "sources": [
    {
      "repository": "agentmesh",
      "references": [
        {
          "consumer": "synthetic-consumer",
          "kind": "import",
          "evidence": "synthetic://consumer/import"
        }
      ]
    }
  ]
}
```

The real inventory requires all thirteen source entries. The abbreviated object above documents the record shape only; the executable synthetic fixture in `examples/agentops/consumers.json` contains the complete source set.

## Evidence semantics

A `passed` command result means only that the supplied inventory is structurally complete for its declared scan scope. It does not prove that a hosted API scan occurred. The result therefore contains `mutation_performed: false` and `portfolio_gate: not_run`.

A live migration decision must additionally bind the inventory to current repository evidence, retain the exact source and target SHAs, expire stale observations, and receive the required human decision before any redirect, deprecation, or archive action.
