from typing import TYPE_CHECKING

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"

if TYPE_CHECKING:
    from . import cli as cli
    from . import daemon as daemon
    from . import debounce as debounce
    from . import markers as markers
    from . import reconcile as reconcile
    from . import roots as roots
    from . import rules as rules
    from . import rules_conflicts as rules_conflicts
    from . import state as state

__all__ = ["__version__"]
