#!/usr/bin/env python3
"""Convenient entry point script to execute LLM Gateway benchmarks."""

import sys
from pathlib import Path

# Ensure root workspace is on python path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from benchmarks.harness import main

if __name__ == "__main__":
    main()
