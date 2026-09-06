# Security policy

Report vulnerabilities through GitHub private vulnerability reporting.

Security properties:

- all numeric resources and collection sizes are bounded;
- permissions and capabilities are explicit allowlists;
- terminal mission states cannot restart through an invalid transition;
- ledger mutations are protected by one re-entrant lock;
- operation IDs are content-bound and conflicting reuse is blocked;
- consumption cannot exceed reservations and unknown measurements fail the mission;
- released reservations retain consumed usage in every ceiling calculation;
- journal writes are locked, append-only, flushed, fsynced, and content-addressed;
- journal corruption or duplicate/conflicting evidence fails closed.

The project does not launch agents, contact models, manage credentials, or execute tools.

