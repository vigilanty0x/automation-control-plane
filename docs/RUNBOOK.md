# Runbook

1. Register an agent with explicit owner, capabilities, permissions, resource ceiling, and retry ceiling.
2. Queue a bounded mission.
3. Reserve worst-case resources before work starts.
4. Record complete measured calls, milliseconds, and tokens. Never substitute zero for unknown data.
5. Release unused capacity after consumption is final.
6. Move the mission to done only after successful work and complete measurement.

Investigate every blocked/rejected result and intervention metric. Correct the input and use a new operation ID; do not rewrite journal evidence.

