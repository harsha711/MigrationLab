"""Scoped evidence memory: adapter (run dir -> records), SQLite store, exact-scope queries.

Read-only consumer of runner output (`runs/`, `examples/`). Never executes SQL against
Saleor databases, starts servers, or calls models. Scope rules: docs/contracts.md.
"""
