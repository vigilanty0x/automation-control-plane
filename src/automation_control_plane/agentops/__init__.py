"""Bounded AgentOps contract rehearsal.

This subpackage adds deterministic, dependency-free planning and evidence modules
without weakening the durable control-plane execution boundary.
"""

from .circuits import simulate_circuit
from .compatibility import compatibility_inventory
from .consumers import inventory_consumers
from .context import plan_context
from .inbox import project_inbox
from .inventory import inventory
from .migration_contracts import migration_contract_inventory
from .quota import simulate_quota
from .rollback import rehearse_rollback
from .routing import evaluate_routing
from .sessions import record_session, verify_session

__all__ = [
    "compatibility_inventory",
    "evaluate_routing",
    "inventory",
    "inventory_consumers",
    "migration_contract_inventory",
    "plan_context",
    "project_inbox",
    "record_session",
    "rehearse_rollback",
    "simulate_circuit",
    "simulate_quota",
    "verify_session",
]
