# AgentOps live public consumer scan

The consumer contract is useful only when a live collector can populate it without crossing the public/private boundary. `scripts/agentops_consumer_scan.py` is the read-only evidence collector for that job.

## Public-only boundary

The scanner deliberately avoids account-wide authenticated repository enumeration and global code search. It starts from GitHub's public user repository endpoint, rejects any item whose metadata says it is private, resolves the current default-branch SHA for each public repository, and scans only that SHA's public tarball.

For every AgentOps source it also reads the public forks endpoint. It never calls a write endpoint and has no merge, issue, pull-request, release, redirect, archive, branch-setting, or repository-setting mutation path.

The repository owner is supplied at runtime. No private repository name, topology, credential, customer record, or production-derived fixture is embedded in the scanner.

## What is collected

One pass classifies public references as:

- `import` for source-code references;
- `package` for package and dependency manifests;
- `workflow` for GitHub Actions workflows;
- `documentation` for docs and other bounded text files;
- `fork` from the public forks API;
- `pilot` only from an explicit public pilot/adopter manifest.

The scanner binds every repository reference to the observed public repository SHA and path. It compares the thirteen source repository heads with `AGENTOPS_SOURCE_INVENTORY.json`; any source SHA drift makes the evidence incomplete instead of silently refreshing the baseline.

## Pilot evidence is deliberately fail-closed

GitHub repository contents cannot prove the complete set of real pilot users. Therefore an absent pilot manifest is **not** interpreted as zero pilots. The scan remains failed until a human-supplied public manifest explicitly marks pilot coverage complete.

The manifest shape is:

```json
{
  "schema_version": 1,
  "complete": true,
  "pilots": [
    {
      "source": "agentmesh",
      "consumer": "public-adopter-id",
      "evidence": "https://example.invalid/public-evidence"
    }
  ]
}
```

Only public evidence belongs in this input.

## Manual evidence workflow

`.github/workflows/agentops-consumer-evidence.yml` is manual-only. It runs with repository contents read permission, executes the scanner, uploads the bounded JSON/Markdown evidence even when the gate fails, and then preserves the scanner's fail-closed result.

A blank pilot manifest is expected to leave the workflow red or blocked; that is evidence of an incomplete inventory, not an infrastructure failure.

## Outputs

- `inventory.json` — input for `agentops consumers`;
- `receipt.json` — deterministic contract receipt;
- `report.md` — bounded human-readable summary;
- SHA-256 values in the job summary and report;
- a GitHub Actions artifact retained for review.

A passing scan still does not authorize source migration or archive. Migration, release, redirect, real rollback, and named human approval remain separate gates.
