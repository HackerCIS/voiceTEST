#!/usr/bin/env python3
"""Thin wrapper so Trial phone can be run as a script from the repo root.

Usage:
  python scripts/clawops_trial_phone.py
  python scripts/clawops_trial_phone.py --to 01012345678
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python scripts/clawops_trial_phone.py` without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.clawops_phone import main

if __name__ == "__main__":
    main()
