"""Supavision: AI-powered infrastructure monitoring + codebase improvement."""

__version__ = "0.3.0"

from .codebase_engine import CodebaseEngine
from .db import Store
from .engine import Engine
from .models.core import Resource, Run
from .models.work import Finding
from .scanner import scan_directory

__all__ = [
    "__version__",
    "CodebaseEngine",
    "Engine",
    "Finding",
    "Resource",
    "Run",
    "Store",
    "scan_directory",
]
