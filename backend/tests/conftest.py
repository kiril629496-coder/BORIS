# -*- coding: utf-8 -*-
"""Global pytest isolation for BORIS backend tests."""
import os

# Importing app.api.tasks must never start long-lived production-like workers
# inside the single pytest process. Individual tests may opt in explicitly.
os.environ.setdefault("BORIS_TASK_WORKERS_ENABLED", "0")
