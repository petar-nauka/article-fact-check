#!/usr/bin/env python3
"""Run the package CLI directly from a source checkout without installation."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from article_fact_check.audit_tool import main


if __name__ == "__main__":
    raise SystemExit(main())
