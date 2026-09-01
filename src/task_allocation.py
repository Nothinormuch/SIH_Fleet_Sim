"""Task-allocation policy names shared by the runner, robots, manager and UI.

This module deliberately contains no path-planning or traffic code. Other team members
can replace the motion policy while these two task-allocation contracts stay unchanged.
"""

from __future__ import annotations

ALLOCATION_PREASSIGNED = "preassigned"
ALLOCATION_AUCTION = "auction"
ALLOCATION_AUCTION_BUNDLE = "auction_bundle"
ALLOCATION_HUNGARIAN = "hungarian"
ACTIVE_ALLOCATION_POLICIES = (
    ALLOCATION_AUCTION,
    ALLOCATION_AUCTION_BUNDLE,
    ALLOCATION_HUNGARIAN,
)
ALLOCATION_POLICIES = (ALLOCATION_PREASSIGNED, *ACTIVE_ALLOCATION_POLICIES)


def validate_allocation_policy(policy: str | None) -> None:
    """Validate an explicit allocation choice; ``None`` means pre-assigned work."""
    if policy is not None and policy not in ALLOCATION_POLICIES:
        raise ValueError(f"unknown task allocation policy {policy!r}")
