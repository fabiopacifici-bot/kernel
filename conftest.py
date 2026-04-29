"""
conftest.py — Shared pytest fixtures and configuration.
"""
import sys
import os

# Ensure src/ is always on the path for unit tests
SRC_DIR = os.path.join(os.path.dirname(__file__), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
