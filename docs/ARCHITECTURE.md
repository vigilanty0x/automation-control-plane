# Architecture

`contract.py` owns bounded public structures. `inbox.py` owns the SQLite schema and
all write transactions. `cli.py` exposes the same API to local tools. `probes.py`
executes persistence, recovery, retry exhaustion, idempotency, and fail-closed proof.

SQLite WAL mode permits concurrent readers. Every claim, recovery, transition, and
event append uses a transaction; claim/recovery take an immediate write lock so two
workers cannot claim the same row.

