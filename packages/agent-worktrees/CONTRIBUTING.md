# Contributing

Focused pull requests are welcome.

1. Branch from `main`.
2. Keep repositories, users, paths, and evidence generic and synthetic.
3. Add a failing test for changed behavior when practical.
4. Exercise destructive-looking behavior only in disposable repositories.
5. Run `python scripts/check.py` and `python -m unittest discover -s tests -v`.
6. Explain ownership, state, Git, rollback, and cleanup effects in the pull request.

Never weaken a safety rejection merely to make a scenario pass. Do not introduce force deletion, shell interpolation, remote mutation, credentials, customer identifiers, or private prompts.
