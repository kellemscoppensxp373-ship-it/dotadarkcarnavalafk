"""Frozen core of the Dark Carnival RPA shell (GUI + loader + OTA).

Everything in this package is compiled into the ``.exe``. All *business logic* lives
outside, as raw ``.py`` files in ``scripts/``, so it can be hot-swapped at runtime.
"""

__version__ = "1.0.0"
