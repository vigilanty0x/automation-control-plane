# LearnPack 0.1

A LearnPack is a deterministic ZIP_STORED archive containing a closed manifest, canonical public Skill IR, permissions, synthetic tests/evidence, provenance attestation, SPDX SBOM, human-readable files, and `MANIFEST.sha256`.

The reference exporter first revalidates current store evidence. It scans the complete source Skill object, then removes run-local IDs/timestamps from the distributable D1–D5 artifact. Independent logical runs therefore produce identical bytes for the same `skill_id` and version.

Validation opens a no-follow descriptor once, checks regular-file identity/size/time while reading a bounded snapshot, and parses ZIP from that in-memory snapshot. It rejects traversal, absolute/drive paths, duplicates, symlinks/non-regular members, encryption, excessive members/sizes/ratios, unknown or missing files, digest gaps/mismatches, incoherent IDs/permissions/tests, non-D4/D5 holdouts, and privacy findings.

Validation is not trust. Import stores a bounded inspectable bundle with `trust_state: disabled_untrusted`, performs no extraction, and grants no execution authority. Release 0.1.0 has no enable/execute transition.
