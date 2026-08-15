# Automation Control Plane

Governed offline automation state transitions, approval, kill switch, and budgets.

Public offline Python MVP using only the standard library. Inputs are bounded, failures remain visible, and all examples/tests use synthetic data.

## CLI

```bash
python -m automation_control_plane.cli input.json
python -m unittest discover -s tests -v
python scripts/check.py
```

The public Python API is `automation_control_plane.core.run(data)`. The CLI accepts the same JSON object from a path or standard input and emits machine-readable JSON.

Apache License 2.0.

