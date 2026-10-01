#!/usr/bin/env python3
"""Entry point wrapper for the preregistered BIOS7 study harness.

This file exists as a user-friendly entry point for backward compatibility.
The actual implementation has been moved to src/ for better project structure.
"""

from src.bios7_acceptance import (
    CANDIDATE_CONFIGURATIONS,
    compare_pair,
    coverage_check,
    main,
    payload_hash,
    release_cases,
    semantic_fingerprint,
    summarize,
    validate_worker_output,
)

__all__ = [
    "CANDIDATE_CONFIGURATIONS",
    "compare_pair",
    "coverage_check",
    "main",
    "payload_hash",
    "release_cases",
    "semantic_fingerprint",
    "summarize",
    "validate_worker_output",
]

if __name__ == "__main__":
    raise SystemExit(main())