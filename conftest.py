"""
conftest.py — Pytest root configuration
Adds the project root to sys.path so that `prover`, `verifier`,
`simulator` are importable without installation.
"""
import sys
import os

# Insert project root at the front of sys.path
sys.path.insert(0, os.path.dirname(__file__))