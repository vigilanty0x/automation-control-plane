"""AgentOps canonical suite API.

AgentOps is currently backed by the stable ``automation_control_plane`` package.
The legacy package name remains supported during consolidation.
"""

from automation_control_plane import *  # noqa: F401,F403
from automation_control_plane import __all__ as _legacy_all
from automation_control_plane import __version__

__all__ = list(_legacy_all)
