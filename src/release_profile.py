"""User-approved scenario-specific software demo defaults.

Explicit comparison policies, old recorded results and the low-level AMR model
do not consult these defaults. This mixed demo profile is not full BIOS7
acceptance; see docs/25 for preserved completion and live-timing limitations.
"""

DEFAULT_ROUTE_POLICY = "BIOS_PIBT.7"
DEFAULT_ALLOCATION_POLICY = "auction_bundle"


def default_route_policy(scenario: str) -> str:
    """Demo routing defaults; explicit policy overrides remain authoritative."""
    return "BIOS_PIBT.6" if scenario == "showcase_grand_challenge" else DEFAULT_ROUTE_POLICY
