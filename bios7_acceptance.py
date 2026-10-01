#!/usr/bin/env python3
"""Entry point wrapper for the preregistered BIOS7 study harness.

This file exists as a user-friendly entry point for backward compatibility.
The actual implementation has been moved to src/ for better project structure.
"""

from src.bios7_acceptance import main

if __name__ == "__main__":
    raise SystemExit(main())