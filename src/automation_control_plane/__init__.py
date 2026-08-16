"""Public package surface.

``run`` and ``transition`` retain the original simulation/state-transition
contract. Durable orchestration is available through ``ControlPlane``.
"""

from .core import run, simulate, transition
from .engine import ControlPlane, ControlPlaneError, KillSwitchError, LeaseLostError
from .handlers import HandlerContext, HandlerError, HandlerRegistry, HandlerResult, builtin_registry
from .models import ModelError, WorkflowDefinition
from .policy import AuthorizationError
from .storage import ConflictError, ControlPlaneStore, NotFoundError, StorageError

__all__ = [
    "ControlPlane",
    "ControlPlaneError",
    "ControlPlaneStore",
    "ConflictError",
    "HandlerContext",
    "HandlerError",
    "HandlerRegistry",
    "HandlerResult",
    "KillSwitchError",
    "LeaseLostError",
    "ModelError",
    "NotFoundError",
    "AuthorizationError",
    "StorageError",
    "WorkflowDefinition",
    "builtin_registry",
    "run",
    "simulate",
    "transition",
]

__version__ = "1.0.0"
