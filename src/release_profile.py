"""Scenario-specific software demo defaults.

Explicit comparison policies, old recorded results and the low-level AMR model
do not consult these defaults. BIOS_PIBT.7 is scoped to the Chokepoint scenario
only, where the sim-only acceptance benchmark showed a measured win over
BIOS_PIBT.6 (30/30 candidate seeds, +8.5% completion time on the showcase
preset). It has not been validated on any other scenario or on live hardware,
so every other scenario keeps the BIOS_PIBT.6 + Auction V2 release default.
"""

DEFAULT_ROUTE_POLICY = "BIOS_PIBT.6"
DEFAULT_ALLOCATION_POLICY = "auction_bundle"

CHOKEPOINT_ROUTE_POLICY = "BIOS_PIBT.7"
CHOKEPOINT_SCENARIOS = frozenset({"crossing_chokepoint", "showcase_chokepoint"})


def default_route_policy(scenario: str) -> str:
    """Demo routing defaults; explicit policy overrides remain authoritative."""
    return CHOKEPOINT_ROUTE_POLICY if scenario in CHOKEPOINT_SCENARIOS else DEFAULT_ROUTE_POLICY
