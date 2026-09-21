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
    # Section: design narrative. Off by default -- most reports still
    # just want the Permit Criteria paragraph. When on, adds the fuller
    # boilerplate narrative (FEMA flood zone, Water Quantity/Quality
    # Criteria detail, Proposed Design Parameters, Water Table Elevation,
    # Conclusion and Recommendations) that the firm's separate "Narrative
    # from Drainage Report" document currently carries, so it can be
    # generated alongside the Permit Criteria Narrative instead of as a
    # standalone document.
    include_design_narrative: bool = False
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


def _fill(value: str, placeholder: str) -> str:
    """Every design-narrative fact that isn't tracked by the project
    renders as a bracketed placeholder rather than being silently
    dropped -- this is what lets the generated narrative double as the
    "print it and fill in the blanks" document the engineer asked
    about, instead of only ever working when every field happens to be
    populated."""
    value = (value or "").strip()
    return value if value else f"[{placeholder}]"


def design_narrative_intro_sections(project: Project, storage_ctx: Optional[Dict] = None) -> List[Dict]:
    """The boilerplate narrative sections that precede the numeric
    results (Section: design narrative) -- Water Quantity Criteria detail,
    FEMA Flood Zone, Water Quality Criteria detail, Proposed Design
    Parameters, and Water Table Elevation. Returns a list of
    {"heading": str, "paragraphs": [str, ...]} in the order they should
    appear. storage_ctx is api.adapter.build_narrative_context(...)'s
    output (or None if no storage/WQ data was supplied); its numbers are
    only ever formatted here, never recomputed (Section 71)."""
    m = project.metadata
    ctx = storage_ctx or {}
    sections: List[Dict] = []

    sections.append({
        "heading": "Water Quantity Criteria",
        "paragraphs": [
            "**Parking Lot Flood Protection:** The proposed stormwater management system has been "
            "designed such that the 5-year/24-hour storm event peak stage remains below the lowest "
            "adjacent inlet or catch basin rim elevation, in accordance with the governing agency's "
            "flood protection criteria.",
            "**Off-Site Discharge:** The system has been analyzed for the 25-year/72-hour storm event. "
            "The proposed condition peak stage/discharge is equal to or lower than the existing condition "
            "for this event, or an equivalent berm/control has been provided where required, such that "
            "no net increase in off-site discharge results from the proposed development.",
            f"**Building Flood Protection:** Finished floor elevations have been set at or above the "
            f"greater of the 100-year/72-hour flood elevation plus 1.0 foot of freeboard, or the "
            f"Design Flood Elevation (DFE), per the governing agency's criteria.",
        ],
    })

    sections.append({
        "heading": "FEMA Flood Zone",
        "paragraphs": [
            f"The subject property is located within FEMA Flood Insurance Rate Map (FIRM) "
            f"Community-Panel Number {_fill(m.fema_community_panel, 'FEMA COMMUNITY-PANEL NUMBER')}, "
            f"Flood Zone {_fill(m.fema_flood_zone, 'FEMA FLOOD ZONE')}, with a Base Flood Elevation "
            f"(BFE) of {_fill(m.fema_bfe_navd, 'BFE, FT NAVD')} ({m.vertical_datum}).",
        ],
    })

    wq_paras = [
        "The proposed stormwater management system provides water quality treatment in accordance "
        "with the governing agency's volumetric water quality criteria: the required treatment volume "
        "is the greater of 1.0 inch of runoff over the total project area, or 2.5 inches of runoff over "
        "the impervious area, less any applicable dry detention or retention credit.",
    ]
    if "wqRequiredAcFt" in ctx and "wqProvidedAcFt" in ctx:
        wq_paras.append(
            f"Required water quality volume: {ctx['wqRequiredAcFt']:.3f} ac-ft. "
            f"Volume provided: {ctx['wqProvidedAcFt']:.3f} ac-ft via "
            f"{ctx.get('wqProvidedSource', 'on-site storage')}."
        )
    if "pretreatmentRequiredAcFt" in ctx:
        prov = ctx.get("pretreatmentProvidedAcFt")
        wq_paras.append(
            f"Pre-treatment (½-inch) volume required: {ctx['pretreatmentRequiredAcFt']:.3f} ac-ft. "
            + (f"Volume provided: {prov:.3f} ac-ft." if prov is not None else "")
        )
    if "equivalentRainfallReductionIn" in ctx:
        wq_paras.append(
            f"Exfiltration trench storage has been converted to an equivalent depth of rainfall "
            f"and subtracted from the design storm events in the proposed condition, per the "
            f"governing agency's exfiltration trench methodology: "
            f"{ctx['equivalentRainfallReductionIn']:.2f} inches."
        )
    sections.append({"heading": "Water Quality Criteria", "paragraphs": wq_paras})

    sections.append({
        "heading": "Proposed Design Parameters",
        "paragraphs": [
            _fill(m.system_narrative, "DESCRIBE THE PROPOSED STORMWATER MANAGEMENT SYSTEM — "
                  "e.g., catch basins, exfiltration trenches, dry detention/retention areas, "
                  "drainage wells, and how runoff is conveyed from the site"),
        ],
    })

    water_table_placeholder = (
        "SOURCE OF DESIGN WATER TABLE ELEVATION — e.g., Broward County 20XX Future Condition "
        "Average Wet Season Groundwater Elevation map, or a project-specific geotechnical report"
    )
    sections.append({
        "heading": "Water Table Elevation",
        "paragraphs": [
            f"The design water table elevation used in this analysis was obtained from "
            f"{_fill(m.design_water_table_source, water_table_placeholder)}.",
        ],
    })

    return sections


def design_narrative_conclusion_section(project: Project) -> Dict:
    """The closing "Conclusion and Recommendations" narrative section."""
    m = project.metadata
    return {
        "heading": "Conclusion and Recommendations",
        "paragraphs": [
            f"Based on the analysis presented herein, the proposed stormwater management system for "
            f"{m.project_name or '[PROJECT NAME]'} has been designed to meet the applicable water "
            f"quantity and water quality criteria of "
            f"{m.regulatory_agency or '[REGULATORY AGENCY]'}. The system is recommended for permitting "
            f"as designed.",
        ],
    }


def permit_summary_markdown(
    project: Project,
    results: Dict[str, ScenarioRunResult],
    findings: List[Finding],
    options: Optional[ReportOptions] = None,
    storage_ctx: Optional[Dict] = None,
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

    if options.include_design_narrative:
        for section in design_narrative_intro_sections(project, storage_ctx):
            lines.append(f"## {section['heading']}")
            lines.append("")
            for p in section["paragraphs"]:
                lines.append(p)
                lines.append("")

    if options.include_final_summary_table:
        lines.extend([final_model_summary_table(project, results), ""])

    if options.include_comparison_table:
        lines.extend([comparison_summary_markdown(results), ""])

    if options.include_design_narrative:
        concl = design_narrative_conclusion_section(project)
        lines.append(f"## {concl['heading']}")
        lines.append("")
        for p in concl["paragraphs"]:
            lines.append(p)
            lines.append("")

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
