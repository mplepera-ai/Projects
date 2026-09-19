"""
reports/generator.py

Two report styles, matching Section 43's modular requirement:

  cascade_style_output()   -- Section 45's familiar detailed routing
                               dump, one scenario/basin at a time.
  permit_summary_markdown() -- Section 57's one-page-scannable summary
                               plus the narrative sections engineers
                               and reviewers expect (Sections 47-56).

Both read from already-computed ScenarioRunResult objects -- neither
function performs any calculation of its own (Section 71: never
recalculate numbers inside report templates).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from project.model import Project, ScenarioRunResult, compare_existing_vs_proposed
from qa.validation import Finding, Level
from reports.cascade_format import cascade_style_output_full


@dataclass
class ReportOptions:
    """Section 44's report toggle screen. Every section is a checkbox
    the user can flip; a report generator that ignores these and always
    prints everything defeats the point (Section 43: "the engineer must
    be able to choose exactly which report sections are generated")."""
    include_cover: bool = True
    include_permit_criteria: bool = True
    include_final_summary_table: bool = True
    include_comparison_table: bool = True
    include_qa_summary: bool = True
    # Which scenarios get full Cascade-style detailed output. None = all
    # enabled scenarios; empty set = none; otherwise a set of scenario_ids.
    cascade_detail_scenario_ids: Optional[Set[str]] = None

    def wants_cascade_detail(self, scenario_id: str) -> bool:
        if self.cascade_detail_scenario_ids is None:
            return True
        return scenario_id in self.cascade_detail_scenario_ids


def cascade_style_output(result: ScenarioRunResult, basins_by_id: Dict[str, "object"]) -> str:
    """Full Cascade-style detailed output (per-structure time-history
    tables, structure/basin max-min summaries, water budget) -- see
    reports/cascade_format.py for the implementation. basins_by_id
    should be project.conditions[result.scenario.condition_name].network.basins.
    """
    return cascade_style_output_full(result, basins_by_id)


def qa_summary_markdown(findings: List[Finding]) -> str:
    lines = ["## QA/QC Summary", ""]
    if not findings:
        lines.append("No findings.")
        return "\n".join(lines)
    for level in (Level.CRITICAL, Level.WARNING, Level.INFO):
        subset = [f for f in findings if f.level == level]
        if not subset:
            continue
        lines.append(f"**{level.value}** ({len(subset)})")
        for f in subset:
            lines.append(f"- [{f.object_id}] {f.message}")
        lines.append("")
    return "\n".join(lines)


def comparison_summary_markdown(results: Dict[str, ScenarioRunResult]) -> str:
    rows = compare_existing_vs_proposed(results)
    lines = ["## Existing vs. Proposed Comparison", "",
             "| Event | Basin | Existing Stage | Proposed Stage | Difference | Result |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        ex = f"{r.existing_peak_stage_ft:.2f}" if r.existing_peak_stage_ft is not None else "—"
        diff = f"{r.difference_ft:+.2f}" if r.difference_ft is not None else "—"
        lines.append(
            f"| {r.event_code} | {r.basin_id} | {ex} | {r.proposed_peak_stage_ft:.2f} | {diff} | {r.result} |"
        )
    return "\n".join(lines)


def final_model_summary_table(project: Project, results: Dict[str, ScenarioRunResult]) -> str:
    """Section 57: a scannable one-page table across all scenarios/basins.
    Freeboard (berm elevation minus peak stage) is included as a plain
    output value only for basins with a berm elevation entered -- see
    hydraulics/basin.py's Basin.berm_elevation_ft docstring for why this
    isn't checked against a required minimum here."""
    lines = ["## Final Model Summary", "",
             "| Scenario | Condition | Event | Basin | Peak Stage (ft) | Offsite Discharge (ac-ft) | ZOD | Freeboard (ft) |",
             "|---|---|---|---|---|---|---|---|"]
    for sid, r in sorted(results.items()):
        basins = project.conditions[r.scenario.condition_name].network.basins
        for bid in r.network_result.peak_stage_ft:
            peak = r.network_result.peak_stage_ft[bid]
            offsite = r.network_result.mass_balance[bid].offsite_discharge_acre_ft
            berm = basins[bid].berm_elevation_ft
            freeboard = f"{berm - peak:.3f}" if berm is not None else "—"
            lines.append(
                f"| {sid} | {r.scenario.condition_name} | {r.scenario.event_code} | {bid} | "
                f"{peak:.3f} | {offsite:.4f} | {r.scenario.zero_offsite_discharge} | {freeboard} |"
            )
    return "\n".join(lines)


def permit_summary_markdown(
    project: Project,
    results: Dict[str, ScenarioRunResult],
    findings: List[Finding],
    options: Optional[ReportOptions] = None,
) -> str:
    """Section 46-59: the narrative-first permit-style report. Every
    section below is gated on `options` (Section 44's checkboxes) --
    default options include everything, matching the old behavior."""
    if options is None:
        options = ReportOptions()
    m = project.metadata
    lines: List[str] = []

    if options.include_cover:
        lines.extend([
            f"# DRAINAGE CALCULATIONS",
            "",
            f"**{m.project_name}**",
            f"{m.project_address}",
            f"Project No. {m.project_number}",
            f"Report Date: {m.report_date}",
            "",
            f"Prepared For: {m.client}",
            f"Prepared By: {m.engineer_name}, P.E. — License No. {m.pe_license_number}",
            f"{m.engineering_firm}",
            "",
            "---",
            "",
        ])

    if options.include_permit_criteria:
        lines.extend([
            "## Permit Criteria",
            "",
            f"The proposed stormwater management system was analyzed using the applicable "
            f"methods of {m.regulatory_agency or 'the governing regulatory agency'} under the "
            f"{project.regulatory_profile.name} profile (version {project.regulatory_profile.version}). "
            "The system was evaluated for the applicable water quantity and off-site discharge "
            "criteria summarized below.",
            "",
        ])

    if options.include_final_summary_table:
        lines.extend([final_model_summary_table(project, results), ""])

    if options.include_comparison_table:
        lines.extend([comparison_summary_markdown(results), ""])

    if options.include_qa_summary:
        lines.extend([qa_summary_markdown(findings), ""])

    detail_scenarios = {sid: r for sid, r in results.items() if options.wants_cascade_detail(sid)}
    if detail_scenarios:
        lines.extend(["---", "", "## Detailed Routing Output", ""])
        for sid, r in sorted(detail_scenarios.items()):
            basins_by_id = project.conditions[r.scenario.condition_name].network.basins
            lines.append(f"### Scenario {sid}")
            lines.append("```")
            lines.append(cascade_style_output(r, basins_by_id))
            lines.append("```")
            lines.append("")

    return "\n".join(lines)
