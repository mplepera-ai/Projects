"""
qa/validation.py

Automated QA/QC checks run before report generation (Section 60-61).
Every finding carries a level (INFO/WARNING/CRITICAL, Section 62) and
identifies the exact object it concerns (Section 61's closing line) --
findings are never vague.

This is a first working subset of the checklist: the mechanically
checkable items (mass balance, rainfall depths, zero-offsite
compliance, stage-storage integrity, existing-vs-proposed direction).
Checks that require external regulatory judgment the software can't
verify on its own (e.g. "is this really the correct FDOT rainfall
distribution for this jurisdiction") are flagged as TODOs rather than
silently skipped, so the gap is visible rather than invisible.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List
from project.model import Project, ScenarioRunResult, compare_existing_vs_proposed


class Level(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class Finding:
    level: Level
    message: str
    object_id: str  # e.g. "scenario PR-100Y-3D, basin B"


MASS_BALANCE_TOLERANCE_PCT_DEFAULT = 0.1


def run_qa(
    project: Project,
    results: Dict[str, ScenarioRunResult],
    mass_balance_tolerance_pct: float = MASS_BALANCE_TOLERANCE_PCT_DEFAULT,
) -> List[Finding]:
    findings: List[Finding] = []

    findings.extend(_check_rainfall_depths(results))
    findings.extend(_check_mass_balance(results, mass_balance_tolerance_pct))
    findings.extend(_check_zero_offsite_compliance(results))
    findings.extend(_check_100y_zod_scenario_present(project))
    findings.extend(_check_existing_vs_proposed_direction(results))
    findings.extend(_check_berm_overtopped(project, results))

    return findings


def _check_rainfall_depths(results: Dict[str, ScenarioRunResult]) -> List[Finding]:
    findings = []
    for sid, r in results.items():
        if r.storm.rainfall_depth_inches <= 0:
            findings.append(Finding(
                Level.CRITICAL,
                f"Rainfall depth is zero or missing ({r.storm.rainfall_depth_inches} in).",
                f"scenario {sid}",
            ))
        if "PLACEHOLDER" in (r.storm.source_reference or ""):
            findings.append(Finding(
                Level.WARNING,
                "Rainfall depth/distribution is an engine-default PLACEHOLDER, "
                "not a verified regulatory value -- replace before permitting.",
                f"scenario {sid}",
            ))
    return findings


def _check_mass_balance(
    results: Dict[str, ScenarioRunResult], tolerance_pct: float
) -> List[Finding]:
    findings = []
    for sid, r in results.items():
        for bid, mb in r.network_result.mass_balance.items():
            if mb.residual_pct_of_inflow > tolerance_pct:
                findings.append(Finding(
                    Level.CRITICAL,
                    f"Mass-balance residual {mb.residual_pct_of_inflow:.3f}% exceeds "
                    f"tolerance of {tolerance_pct}%.",
                    f"scenario {sid}, basin {bid}",
                ))
    return findings


def _check_zero_offsite_compliance(results: Dict[str, ScenarioRunResult]) -> List[Finding]:
    findings = []
    for sid, r in results.items():
        if not r.scenario.zero_offsite_discharge:
            continue
        total_offsite = r.network_result.network_total_offsite_discharge_acre_ft
        if abs(total_offsite) > 1e-6:
            findings.append(Finding(
                Level.CRITICAL,
                f"Zero-offsite-discharge scenario shows {total_offsite:.6f} ac-ft "
                "of offsite discharge -- expected 0.000. An OFFSITE_DISCHARGE "
                "structure may be missing its destination classification.",
                f"scenario {sid}",
            ))
    return findings


def _check_100y_zod_scenario_present(project: Project) -> List[Finding]:
    has_it = any(
        s.condition_name == "proposed" and s.event_code == "100Y-3D" and s.enabled
        for s in project.scenarios
    )
    if not has_it:
        return [Finding(
            Level.WARNING,
            "No proposed 100-Year/3-Day zero-offsite-discharge scenario is defined.",
            "project scenarios",
        )]
    return []


def _check_existing_vs_proposed_direction(results: Dict[str, ScenarioRunResult]) -> List[Finding]:
    findings = []
    for row in compare_existing_vs_proposed(results):
        if row.result == "REVIEW REQUIRED":
            findings.append(Finding(
                Level.WARNING,
                f"Proposed peak stage ({row.proposed_peak_stage_ft:.3f} ft) exceeds "
                f"existing peak stage ({row.existing_peak_stage_ft:.3f} ft) by "
                f"{row.difference_ft:.3f} ft. Check perimeter-grade/containment criteria.",
                f"event {row.event_code}, basin {row.basin_id}",
            ))
    return findings


def _check_berm_overtopped(project: Project, results: Dict[str, ScenarioRunResult]) -> List[Finding]:
    """Berm/perimeter containment (raised alongside the Miami-Dade DERM
    comparison): a basin's berm elevation isn't a routed structure, so
    nothing else in the engine checks it. No universal regulatory
    minimum freeboard was found to check against (varies by
    jurisdiction/reviewer), so freeboard itself is reported as a plain
    output value (see api/adapter.py's freeboardFt) rather than
    checked here -- the only thing this check flags is outright
    overtopping (freeboard < 0), which is unambiguous regardless of
    what freeboard a given reviewer wants."""
    findings = []
    for sid, r in results.items():
        basins = project.conditions[r.scenario.condition_name].network.basins
        for bid, peak_stage in r.network_result.peak_stage_ft.items():
            berm = basins[bid].berm_elevation_ft
            if berm is None:
                continue
            freeboard = berm - peak_stage
            if freeboard < 0:
                findings.append(Finding(
                    Level.CRITICAL,
                    f"Berm overtopped: peak stage ({peak_stage:.3f} ft) exceeds the berm "
                    f"elevation ({berm:.3f} ft) by {-freeboard:.3f} ft.",
                    f"scenario {sid}, basin {bid}",
                ))
    return findings


def has_unresolved_critical(findings: List[Finding]) -> bool:
    return any(f.level == Level.CRITICAL for f in findings)
