"""Single source of truth for Starling's version string.

Kept as a separate module (not inside a package) so every other module can
import it without circular-import risk.
"""

__version__ = "1.3.1-alpha"
