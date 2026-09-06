# Architecture

`AgentRegistry` is the authority boundary. `Mission` declares required capability/permission and its own ceiling. `BudgetEngine` serializes ledger mutations under a lock and checks projected global, mission, and agent usage before creating a deterministic reservation.

Active reservations count their full reserved amount. Released reservations count their measured consumption, so releasing unused capacity never erases real use. Consumption accumulates transactionally and cannot exceed the reservation.

Operation fingerprints bind idempotency IDs to exact input. Identical replay returns prior evidence; conflicting replay is blocked. Optional `EvidenceJournal` persists results as content-addressed JSONL events.

