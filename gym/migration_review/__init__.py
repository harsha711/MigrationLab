"""MigrationLab migration-review environment for NeMo Gym (framework-independent core).

This package needs only the runner venv (Python 3.12). It builds the frozen tasks
(`tasks.py`) and scores a policy's plan with the deterministic runner (`scoring.py`).
The NeMo Gym resources server in `gym/resources_server/` (Python >= 3.13, nemo-gym 0.7.0)
reaches it through `gym/verify_cli.py` in a subprocess, because the two cannot share a venv.
"""
