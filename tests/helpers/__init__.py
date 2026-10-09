"""Shared test helpers (import as ``from helpers.<module> import ...``).

``tests/`` has no ``__init__.py``, so pytest inserts it on ``sys.path`` and the
package is addressed as ``helpers``, never ``tests.helpers``.
"""
