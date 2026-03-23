#!/usr/bin/env python3
"""Quick test for db.py."""
import sys
import tempfile
import os

sys.path.insert(0, "src")

from supervisor.models import Resource, Report, RunType, RunStatus
from supervisor.db import Store

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "test.db")
print(f"DB: {db_path}")

store = Store(db_path)
print("Store created")

root = Resource(name="Root", resource_type="aws_account")
store.save_resource(root)
print(f"Saved: {root.id}")

loaded = store.get_resource(root.id)
print(f"Loaded: {loaded.name}")

tree = store.get_resource_tree(root.id)
print(f"Tree: {len(tree)}")

store.close()
print("PASS")
