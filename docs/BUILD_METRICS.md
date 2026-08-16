# Build metrics

Release metrics are generated from fresh commands, not hard-coded marketing claims.

Required gates:

- all unit/integration/adversarial tests pass;
- Python modules compile;
- wheel and sdist build without runtime dependencies;
- wheel installs and `apprentice version`, `capabilities`, and `demo` run outside the checkout;
- two independent D1–D5 runs produce identical LearnPack bytes;
- repository contains no generated database, pack, credential, or cache artifact.

The runtime benchmark reports capture, privacy, episode, pattern, question, memory, and skill dimensions plus named required checks. `aggregate_score` is intentionally `null`.

## Fresh local release gate — 2026-08-16

- `python -m compileall -q src tests`: passed.
- source checkout suite: **88/88 passed**.
- built wheel suite, installed in an isolated virtual environment and invoked from
  outside the checkout: **88/88 passed**.
- wheel and sdist: built with the declared `setuptools.build_meta` backend.
- sdist: extracted, rebuilt into a wheel, installed, and command-smoked outside
  its source tree.
- installed `version`, `capabilities`, `demo`, and `pack validate`: passed.
- independent installed-wheel and sdist-derived demos produced the same reference
  LearnPack digest:
  `sha256:639009795b7ed63e333126130b2c5e2cd295a9d291f3270fc7dc7556f4554de4`.

GitHub Actions repeats source tests on Python 3.11 and 3.13 for Linux and Windows,
then builds, installs, and tests wheels outside the checkout on both operating
systems. Every third-party action is pinned to a full commit SHA; checkout does
not persist credentials and workflow permissions are read-only.
