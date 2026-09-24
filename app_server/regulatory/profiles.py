"""
regulatory/profiles.py

Regulatory criteria are data, not code (Section 4: "shall not be
permanently hard-coded into the calculation engine"). A RegulatoryProfile
is a versioned bundle of required design events; new jurisdictions are
added by writing a new profile function, never by touching the routing
or hydrology engines.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from hydrology.rainfall import StormEvent, RainfallDistribution, SFWMD_24HR_PLACEHOLDER, SFWMD_3DAY_PLACEHOLDER


@dataclass
class RequiredEvent:
    code: str                     # e.g. "5Y-1D"
    label: str                    # e.g. "5-Year / 1-Day"
    purpose: str                  # e.g. "Parking lot protection"
    return_period_years: float
    duration_hours: float
    default_selected: bool = True
    zero_offsite_discharge: bool = False


@dataclass
class RegulatoryProfile:
    name: str                     # e.g. "Broward County SWM"
    version: str                  # e.g. "2026-01-01"
    required_events: List[RequiredEvent]

    def selected_by_default(self) -> List[RequiredEvent]:
        return [e for e in self.required_events if e.default_selected]

    def event_by_code(self, code: str) -> RequiredEvent:
        for e in self.required_events:
            if e.code == code:
                return e
        raise KeyError(f"No required event with code '{code}' in profile '{self.name}'.")


def broward_county_swm_profile(version: str = "2026-01-01") -> RegulatoryProfile:
    """Section 5's default Broward County table."""
    return RegulatoryProfile(
        name="Broward County SWM",
        version=version,
        required_events=[
            RequiredEvent("5Y-1D", "5-Year / 1-Day", "Parking lot protection",
                          5, 24, default_selected=True),
            RequiredEvent("10Y-1D", "10-Year / 1-Day", "Road protection",
                          10, 24, default_selected=False),
            RequiredEvent("10Y-3D", "10-Year / 3-Day", "Road / flood protection",
                          10, 72, default_selected=False),
            RequiredEvent("25Y-3D", "25-Year / 3-Day", "Attenuation / pre-vs-post",
                          25, 72, default_selected=True),
            RequiredEvent("100Y-3D", "100-Year / 3-Day", "Flood protection, zero off-site discharge",
                          100, 72, default_selected=True, zero_offsite_discharge=True),
        ],
    )


def sfwmd_profile(version: str = "2026-01-01") -> RegulatoryProfile:
    """Placeholder SFWMD-only profile (district-level ERP criteria are
    generally a superset/variant of county criteria and should be
    filled in against the current Applicant's Handbook Volume II
    before use -- see Section 84)."""
    return RegulatoryProfile(
        name="SFWMD",
        version=version,
        required_events=[
            RequiredEvent("25Y-3D", "25-Year / 3-Day", "Attenuation / pre-vs-post",
                          25, 72, default_selected=True),
            RequiredEvent("100Y-3D", "100-Year / 3-Day", "Flood protection, zero off-site discharge",
                          100, 72, default_selected=True, zero_offsite_discharge=True),
        ],
    )


def custom_profile(name: str, version: str, required_events: List[RequiredEvent]) -> RegulatoryProfile:
    return RegulatoryProfile(name=name, version=version, required_events=required_events)


# ---- rainfall depth lookup --------------------------------------------
# NOTE: depths below are illustrative placeholders (roughly typical
# South Florida magnitudes), NOT verified regulatory values. Section
# 10 requires rainfall depths to be editable per-project precisely
# because they vary by location -- treat this table as defaults to
# override, never as an authoritative source.

_PLACEHOLDER_DEPTHS_INCHES = {
    ("5Y-1D", 24): 5.5,
    ("10Y-1D", 24): 6.5,
    ("10Y-3D", 72): 9.0,
    ("25Y-3D", 72): 13.0,
    ("100Y-3D", 72): 17.0,
}


def build_storm_event(required_event: RequiredEvent, rainfall_depth_inches: Optional[float] = None) -> StormEvent:
    depth = rainfall_depth_inches
    if depth is None:
        depth = _PLACEHOLDER_DEPTHS_INCHES.get((required_event.code, int(required_event.duration_hours)))
        if depth is None:
            raise ValueError(
                f"No default rainfall depth for event '{required_event.code}' -- "
                "supply rainfall_depth_inches explicitly (Section 10: rainfall must "
                "be editable and is never silently assumed)."
            )
    distribution = SFWMD_24HR_PLACEHOLDER if required_event.duration_hours <= 24 else SFWMD_3DAY_PLACEHOLDER
    return StormEvent(
        name=required_event.label,
        return_period_years=required_event.return_period_years,
        duration_hours=required_event.duration_hours,
        rainfall_depth_inches=depth,
        distribution=distribution,
        source_reference="PLACEHOLDER default -- replace with project-specific regulatory rainfall depth",
    )
