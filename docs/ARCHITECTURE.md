# Architecture

`models` validates the versioned DAG and ownership contract. `store` owns SQLite transactions and idempotent events. `engine` schedules only dependency-ready tasks, enforces leases and evidence, resumes expired work, and rejects dependents of terminal failures. `probes` separates liveness/readiness from a functional control and counter-proof.

