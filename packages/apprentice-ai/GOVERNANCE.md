# Governance

The project uses maintainer review with evidence gates.

Changes to privacy boundaries, storage schema, Skill IR, LearnPack validation, API authentication, or execution policy require:

1. a written invariant and failure mode;
2. adversarial regression tests;
3. an independent reviewer;
4. updated contracts and threat model;
5. a release note.

No contributor may silently expand capture or execution. A proposal for real activity capture must document explicit consent, platform indicators, pause/stop behavior, denied surfaces, retention, and deletion. A proposal for execution must be a new major safety design; it cannot be slipped into `0.1.x`.

Releases are cut from a clean tree after Linux/Windows CI, source and wheel builds, installation from the wheel outside the checkout, CLI/API smoke tests, deterministic reference-pack comparison, and checksum publication. Maintainers never publish directly from unreviewed generated output.

Decisions prefer falsifiable evidence over aggregate scores. A privacy or integrity failure cannot be averaged away by successful functional tests.
