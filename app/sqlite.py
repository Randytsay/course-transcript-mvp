"""SQLite connection helpers used by the application's stores."""
from __future__ import annotations

import sqlite3


class ClosingConnection(sqlite3.Connection):
    """Close a SQLite connection after its context manager exits.

    ``sqlite3.Connection`` commits or rolls back in ``__exit__`` but leaves
    the handle open.  The stores expose ``connect()`` and several read paths
    use it in a ``with`` statement, so this subclass makes that pattern safe.
    """

    def __exit__(self, exc_type, exc_value, traceback):  # type: ignore[no-untyped-def]
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
