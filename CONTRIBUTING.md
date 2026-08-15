# Contributing

Focused pull requests are welcome.

1. Branch from `main`.
2. Keep fixtures generic and synthetic.
3. Add a failing test for changed behavior when practical.
4. Run `python scripts/check.py` and `python -m unittest discover -s tests -v`.
5. Explain state, persistence, compatibility, and rollback effects in the pull request.

New state transitions require explicit evidence rules. New CLI inputs must stay bounded. Never commit credentials, production logs, customer identifiers, or private prompts.
