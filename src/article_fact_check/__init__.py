"""article-fact-check: source-backed claim audit engine for articles."""

from __future__ import annotations

from article_fact_check.audit_tool import (
    SCHEMA_VERSION,
    VERDICTS,
    calculate_reliability_score,
    validate_audit,
    generate_html_report,
    main,
)

__version__ = "2.0.0"
__all__ = [
    "SCHEMA_VERSION",
    "VERDICTS",
    "calculate_reliability_score",
    "validate_audit",
    "generate_html_report",
    "main",
    "__version__",
]
