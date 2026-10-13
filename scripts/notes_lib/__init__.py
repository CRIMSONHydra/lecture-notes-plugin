"""notes_lib: shared, stdlib-only helpers for the `notes` Claude Code plugin.

Modules
-------
platform   OS detection, data-dir resolution, path helpers, GPU summary
chrome     Chrome/Edge/Chromium discovery and headless launch flags
profile    subject-profile lookup, loading, defaults (lazy ``yaml`` import)
layout     fixed project layout (resources/ notes/ pdf/ .notes-plugin-internal/): the single source of paths
notefile   note parsing and word/minute statistics (lazy ``yaml`` import)
guard      read-only guard for resources (and external prior notes)

Scripts in ``scripts/`` import this package with::

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from notes_lib import guard, platform
"""

__version__ = "0.1.0"
