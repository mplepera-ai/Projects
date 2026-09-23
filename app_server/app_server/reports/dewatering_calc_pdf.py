"""
reports/dewatering_calc_pdf.py

Dewatering Design Report: a permit-submittal-style report, not a
calculation dump. Structure follows a permit-review audit of the
original all-appendix version of this report:

    1. Design Summary          (cover-sheet cheat-sheet: what/how much/
                                 how it's handled/does it have capacity)
    2. Design Criteria & Input Data (with a Source/Basis column)
    3. Design Assumptions
    4. Methodology              (formulas only, no substitution)
    5. Zone-by-Zone Results     (results table, not full algebra)
    6. Water Balance Summary
    7. Settling Tank Design     (required vs. selected/provided, always
                                 kept as two distinct numbers)
    8. Receiving System Capacity (optional; exfiltration trench today)
    9. Design Checks            (pass/fail table)
   10. General-Permit Screening (omitted entirely if nothing configured
                                 -- never an ambiguous "maybe" statement)
   11. Appendix -- Backup Calculations (every formula, substituted, per
                                 zone and for the tank -- this IS the
                                 old report, kept in full as audit
                                 material, just demoted from "the
                                 report" to "the appendix")

This module only formats already-computed dataclass results from
dewatering/calculations.py (and storage/exfiltration.py for the
optional receiving-system check) -- it never recomputes or re-derives
a number independently of the engine.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors

from dewatering.calculations import (
    AquiferParams, ZoneResult, WaterBalanceSummary, TankInputs, TankResult, MinTankResult,
    PermitScreeningRow, SelectedTankCheck,
)


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CalcBody", parent=styles["Normal"], fontSize=10, leading=14))
    styles.add(ParagraphStyle(name="CalcHeading", parent=styles["Heading2"], spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="CalcSubHeading", parent=styles["Heading3"], spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="CalcMono", parent=styles["Normal"], fontName="Courier", fontSize=9, leading=13))
    styles.add(ParagraphStyle(name="CalcNote", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#555555")))
    styles.add(ParagraphStyle(name="ResultBig", parent=styles["Normal"], fontSize=15, leading=19, fontName="Helvetica-Bold",
                               textColor=colors.HexColor("#1B4F72")))
    return styles


def _table_style(header_bg="#2C3E50"):
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])


def _status_color(status: str) -> colors.Color:
    s = (status or "").upper()
    if s in ("PASS",):
        return colors.HexColor("#1E7B34")
    if s in ("FAIL", "EXCEEDS", "CRITICAL"):
        return colors.HexColor("#B03A2E")
    return colors.HexColor("#8A6D0B")


def _status_table_style(rows: List[list], status_col: int, header_bg="#2C3E50"):
    style = _table_style(header_bg)
    for i, row in enumerate(rows[1:], start=1):
        color = _status_color(str(row[status_col]))
        style.add("TEXTCOLOR", (status_col, i), (status_col, i), color)
        style.add("FONTNAME", (status_col, i), (status_col, i), "Helvetica-Bold")
    return style


# ---------------------------------------------------------------------------
# Section 1: Design Summary (cover sheet)
# ---------------------------------------------------------------------------

def _design_summary_section(
    story, styles, project_name, project_address, project_info,
    zone_results, summary, tank_result, min_tank_result, tank_mode,
    selected_check, receiving_checks,
):
    pi = project_info or {}
    story.append(Paragraph("Dewatering Design Report", styles["Title"]))
    story.append(Spacer(1, 0.05 * inch))

    proj_rows = [["Item", "Value"]]
    proj_rows.append(["Project", project_name or ""])
    if project_address:
        proj_rows.append(["Address", project_address])
    for label, key in [
        ("Project No.", "projectNo"), ("Prepared By", "preparedBy"), ("Date", "date"),
        ("Dewatering Activity", "dewateringActivity"), ("Dewatering Method", "dewateringMethod"),
        ("Discharge Method", "dischargeMethod"),
    ]:
        val = pi.get(key)
        if val:
            proj_rows.append([label, val])
    t = Table(proj_rows, hAlign="LEFT", colWidths=[1.8 * inch, 4.4 * inch])
    t.setStyle(_table_style())
    story.append(t)
    story.append(Spacer(1, 0.18 * inch))

    story.append(Paragraph("Design Summary", styles["CalcHeading"]))
    total_area = ", ".join(f"{r.zone.width_ft:.0f} x {r.zone.length_ft:.0f} ft" for r in zone_results[:4])
    if len(zone_results) > 4:
        total_area += f", +{len(zone_results) - 4} more"
    max_zone = max(zone_results, key=lambda r: r.flow_gpm) if zone_results else None

    rows = [["Design Parameter", "Result"]]
    if max_zone:
        rows.append(["Excavation area(s)", total_area])
        rows.append(["Governing drawdown", f"{max_zone.zone.drawdown_ft:.2f} ft ({max_zone.zone.name})"])
        rows.append(["Governing radius of influence", f"{max_zone.radius_of_influence_ft:.0f} ft"])
    if summary:
        rows.append(["Calculated dewatering rate (max)", f"{summary.max_flow_gpm:,.1f} gpm"])
        rows.append(["Operating duration", f"{max_zone.zone.hours_per_day:.0f} hr/day" if max_zone else "--"])
        rows.append(["Estimated duration", f"{summary.total_pumping_days:,.0f} days"])
        rows.append(["Maximum daily pumpage", f"{summary.max_daily_pumpage_mgd:.4f} MGD"])
        rows.append(["Estimated total pumpage", f"{summary.total_project_pumpage_mg:,.2f} MG"])
    if tank_result is not None:
        rows.append(["Treatment required", "Sedimentation (Type 1 settling)"])
        if tank_mode == "auto" and min_tank_result is not None:
            rows.append(["Minimum required settling tank",
                         f"{min_tank_result.length_ft:.0f} x {min_tank_result.width_ft:.0f} x {min_tank_result.depth_ft:.0f} ft"])
        else:
            rows.append(["Settling tank checked",
                         f"{tank_result.volume_cf / (tank_result.effective_depth_ft or 1):.0f} ft&sup2; footprint, "
                         f"{tank_result.effective_depth_ft:.1f} ft effective depth"])
        if selected_check is not None:
            rows.append(["Selected settling system", f"{selected_check.name} ({selected_check.provided_volume_gal:,.0f} gal)"])
    if receiving_checks:
        for rc in receiving_checks:
            rows.append([f"Receiving system: {rc['name']}", f"{rc['capacity_gpm']:,.0f} gpm capacity ({rc['status']})"])

    t = Table(rows, hAlign="LEFT", colWidths=[3.0 * inch, 3.2 * inch])
    t.setStyle(_table_style())
    story.append(t)

    if summary:
        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph(
            f"<b>GOVERNING DEWATERING RATE: {summary.max_flow_gpm:,.1f} GPM ({summary.max_flow_gpm * 0.002228009:.3f} CFS)</b>",
            styles["ResultBig"],
        ))


# ---------------------------------------------------------------------------
# Section 2: Design Criteria & Input Data (with Source/Basis)
# ---------------------------------------------------------------------------

def _criteria_section(story, styles, aquifer, zone_results, tank_inputs, criteria_basis):
    basis = criteria_basis or {}
    story.append(PageBreak())
    story.append(Paragraph("Design Criteria and Input Data", styles["CalcHeading"]))

    rows = [["Parameter", "Symbol", "Value", "Units", "Source / Basis"]]
    z0 = zone_results[0].zone if zone_results else None
    if z0:
        rows.append(["Groundwater elevation", "GW", f"{z0.groundwater_elev_ft:.2f}", "ft", basis.get("gwElev", "")])
        rows.append(["Excavation bottom elevation", "EL_b", f"{z0.excavation_bottom_elev_ft:.2f}", "ft", basis.get("excBottom", "")])
        rows.append(["Operating margin", "M", f"{z0.operating_margin_ft:.2f}", "ft", basis.get("operatingMargin", "")])
        rows.append(["Pumping duration", "--", f"{z0.hours_per_day:.1f}", "hr/day", basis.get("hoursPerDay", "")])
    rows.append(["Hydraulic conductivity", "K", f"{aquifer.hydraulic_conductivity_ft_day:,.3g}", "ft/day", basis.get("k", "")])
    rows.append(["Aquifer thickness", "H / b", f"{aquifer.aquifer_thickness_ft:,.1f}", "ft", basis.get("aquiferThickness", "")])
    rows.append(["Aquifer condition", "--", aquifer.aquifer_type.value.capitalize(), "--", basis.get("aquiferType", "")])
    if tank_inputs is not None:
        rows.append(["Design particle size", "d", f"{tank_inputs.particle_diameter_ft * 304800:.0f}", "μm", basis.get("particleSize", "")])
        rows.append(["Particle specific gravity", "SG", f"{tank_inputs.specific_gravity:.2f}", "--", basis.get("specificGravity", "")])

    t = Table(rows, hAlign="LEFT", colWidths=[1.75 * inch, 0.55 * inch, 0.75 * inch, 0.65 * inch, 2.3 * inch])
    t.setStyle(_table_style())
    story.append(t)
    story.append(Paragraph(
        "The Source/Basis column records where each input came from (survey, geotechnical report, "
        "construction plans, or a stated design assumption) so a reviewer never has to ask where a "
        "number came from.",
        styles["CalcNote"],
    ))


# ---------------------------------------------------------------------------
# Section 3: Assumptions
# ---------------------------------------------------------------------------

def _assumptions_section(story, styles, aquifer, tank_result, extra_assumptions):
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Design Assumptions", styles["CalcHeading"]))
    bullets = [
        f"Groundwater conditions are assumed to be {aquifer.aquifer_type.value}.",
        "Dewatering calculations represent steady-state groundwater inflow (Sichardt / "
        "Dupuit-Forchheimer or Thiem).",
        "Each excavation footprint is represented as an equivalent circular well for radial-flow purposes.",
        "Calculations exclude direct rainfall inflow; active dewatering is assumed to be suspended or "
        "adjusted during significant rainfall unless otherwise noted.",
    ]
    if tank_result is not None:
        bullets.append(
            "Turbidity-control (settling tank) calculations assume discrete, non-flocculating mineral "
            "particles settling under Stokes' Law (Type 1 settling)."
        )
    for extra in (extra_assumptions or []):
        if extra:
            bullets.append(extra)
    for b in bullets:
        story.append(Paragraph(f"&bull; {b}", styles["CalcBody"]))


# ---------------------------------------------------------------------------
# Section 4: Methodology (formulas only)
# ---------------------------------------------------------------------------

def _methodology_section(story, styles, aquifer):
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Dewatering Methodology", styles["CalcHeading"]))
    story.append(Paragraph("4.1 Required Drawdown", styles["CalcSubHeading"]))
    story.append(Paragraph("s = EL_GW &minus; (EL_exc &minus; M)", styles["CalcMono"]))
    story.append(Paragraph("4.2 Radius of Influence (Sichardt)", styles["CalcSubHeading"]))
    story.append(Paragraph("R (m) = 3000 &middot; s (m) &middot; &radic;K (m/s)", styles["CalcMono"]))
    story.append(Paragraph("4.3 Equivalent Excavation Radius", styles["CalcSubHeading"]))
    story.append(Paragraph("re (m) = &radic;(L&middot;W (m&sup2;) / &pi;)", styles["CalcMono"]))
    story.append(Paragraph("4.4 Groundwater Inflow", styles["CalcSubHeading"]))
    if aquifer.aquifer_type.value == "unconfined":
        story.append(Paragraph(
            "Unconfined (Dupuit-Forchheimer):  Q = &pi;&middot;K&middot;(H&sup2; &minus; h&sup2;) / ln(R / re),  "
            "h = H &minus; s", styles["CalcMono"]))
    else:
        story.append(Paragraph(
            "Confined (Thiem):  Q = 2&middot;&pi;&middot;T&middot;s / ln(R / re),  T = K&middot;b", styles["CalcMono"]))
    var_rows = [
        ["Symbol", "Description", "Units"],
        ["Q", "Groundwater inflow", "m³/s"],
        ["K", "Hydraulic conductivity", "m/s"],
        ["H", "Initial saturated thickness", "m"],
        ["h", "Saturated thickness after drawdown", "m"],
        ["R", "Radius of influence", "m"],
        ["re", "Equivalent excavation radius", "m"],
    ]
    t = Table(var_rows, hAlign="LEFT", colWidths=[0.8 * inch, 3.2 * inch, 1.0 * inch])
    t.setStyle(_table_style())
    story.append(t)
    story.append(Paragraph(
        "Full substituted calculations for every zone, and for the settling tank, are provided in the "
        "Appendix.", styles["CalcNote"],
    ))


# ---------------------------------------------------------------------------
# Section 5-6: Zone summary + water balance
# ---------------------------------------------------------------------------

def _zone_summary_and_water_balance(story, styles, zone_results, zone_errors, overlap_notes, summary):
    story.append(PageBreak())
    story.append(Paragraph("Zone-by-Zone Results", styles["CalcHeading"]))
    rows = [["Zone", "Description", "Drawdown (ft)", "Flow (gpm)", "Daily (MGD)", "Total (MG)", "Days"]]
    for r in zone_results:
        rows.append([
            r.zone.name, r.zone.description or "", f"{r.zone.drawdown_ft:.2f}", f"{r.flow_gpm:,.1f}",
            f"{r.daily_pumpage_mgd:.4f}", f"{r.total_pumpage_mg:.2f}", f"{r.zone.total_days:.1f}",
        ])
    if summary is not None and len(zone_results) > 1:
        rows.append(["DESIGN MAXIMUM", "", "", f"{summary.max_flow_gpm:,.1f}", f"{summary.max_daily_pumpage_mgd:.4f}",
                     f"{summary.total_project_pumpage_mg:.2f}", f"{summary.total_pumping_days:.0f}"])
    t = Table(rows, hAlign="LEFT", colWidths=[0.75 * inch, 1.35 * inch, 1.0 * inch, 0.8 * inch, 0.8 * inch, 0.75 * inch, 0.55 * inch])
    t.setStyle(_table_style())
    story.append(t)

    if zone_errors:
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("Zones Not Computed", styles["CalcSubHeading"]))
        for e in zone_errors:
            story.append(Paragraph(f"&bull; {e}", styles["CalcNote"]))
    if overlap_notes:
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("Zone Overlap / Superposition Notes", styles["CalcSubHeading"]))
        for n in overlap_notes:
            story.append(Paragraph(f"&bull; {n}", styles["CalcNote"]))

    if summary is not None:
        story.append(Spacer(1, 0.18 * inch))
        story.append(Paragraph("Water Balance Summary", styles["CalcHeading"]))
        wb_rows = [
            ["Metric", "Value"],
            ["Average flow (all zones)", f"{summary.average_flow_gpm:,.1f} gpm"],
            ["Maximum single-zone flow", f"{summary.max_flow_gpm:,.1f} gpm"],
            ["Maximum daily pumpage", f"{summary.max_daily_pumpage_mgd:.4f} MGD"],
            ["Total project pumpage", f"{summary.total_project_pumpage_mg:,.2f} MG"],
            ["Total pumping duration", f"{summary.total_pumping_days:,.0f} days"],
        ]
        t = Table(wb_rows, hAlign="LEFT", colWidths=[3.2 * inch, 3.0 * inch])
        t.setStyle(_table_style())
        story.append(t)
        story.append(Paragraph(
            "Rainfall contribution: not included; active dewatering is assumed to be suspended or "
            "adjusted during significant rainfall unless a project-specific rainfall allowance is noted above.",
            styles["CalcNote"],
        ))


# ---------------------------------------------------------------------------
# Section 7: Settling tank -- required vs. selected/provided
# ---------------------------------------------------------------------------

def _tank_section(story, styles, tank_inputs, tank_result, min_tank_result, tank_mode, selected_check):
    if tank_result is None:
        return
    story.append(PageBreak())
    story.append(Paragraph("Settling Tank / Turbidity Control Design", styles["CalcHeading"]))
    story.append(Paragraph(
        "Type 1 (discrete-particle) settling design per Stokes' Law and the surface overflow rate "
        "(SOR) method. Full substituted calculations are provided in the Appendix.",
        styles["CalcBody"],
    ))

    if tank_mode == "auto" and min_tank_result is not None:
        mtr = min_tank_result
        rows = [["Item", "Value"]]
        rows.append(["Design flow", f"{tank_inputs.flow_gpm:,.1f} gpm"])
        rows.append(["Required surface area (A = Q/Vs)", f"{mtr.required_surface_area_ft2:.1f} ft²"])
        rows.append(["Minimum required tank (L x W x D)",
                     f"{mtr.length_ft:.1f} x {mtr.width_ft:.1f} x {mtr.depth_ft:.1f} ft"])
        rows.append(["Minimum required volume (effective)", f"{tank_result.effective_volume_gal:,.0f} gal"])
        t = Table(rows, hAlign="LEFT", colWidths=[3.0 * inch, 3.2 * inch])
        t.setStyle(_table_style())
        story.append(t)
    else:
        rows = [["Item", "Value"]]
        rows.append(["Design flow", f"{tank_inputs.flow_gpm:,.1f} gpm"])
        rows.append(["Tank checked (L x W x D)",
                     f"{tank_inputs.length_ft:.1f} x {tank_inputs.width_ft:.1f} x {tank_inputs.depth_ft:.1f} ft"])
        t = Table(rows, hAlign="LEFT", colWidths=[3.0 * inch, 3.2 * inch])
        t.setStyle(_table_style())
        story.append(t)

    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph("Selected / Provided System", styles["CalcSubHeading"]))
    if selected_check is not None:
        sc: SelectedTankCheck = selected_check
        rows = [["Item", "Value"]]
        rows.append(["Selected system", sc.name])
        rows.append(["Required (minimum)", f"{sc.required_volume_gal:,.0f} gal"])
        rows.append(["Provided", f"{sc.provided_volume_gal:,.0f} gal"])
        rows.append(["Capacity ratio (provided / required)", f"{sc.capacity_ratio:.2f}"])
        rows.append(["Detention time (provided)", f"{sc.detention_time_min:.1f} min"])
        rows.append(["Status", sc.status])
        if sc.notes:
            rows.append(["Notes", sc.notes])
        t = Table(rows, hAlign="LEFT", colWidths=[3.0 * inch, 3.2 * inch])
        t.setStyle(_status_table_style(rows, 1))
        story.append(t)
    else:
        story.append(Paragraph(
            f"As designed/checked above -- {tank_result.volume_gal:,.0f} gal gross "
            f"({tank_result.effective_volume_gal:,.0f} gal effective).",
            styles["CalcBody"],
        ))
        if tank_result.effective_volume_gal != tank_result.volume_gal:
            fraction = 1 - tank_result.effective_volume_gal / tank_result.volume_gal
            story.append(Paragraph(
                f"{fraction*100:.0f}% of gross depth reserved for freeboard and accumulated sediment "
                f"(effective depth {tank_result.effective_depth_ft:.2f} ft of "
                f"{tank_result.effective_depth_ft / (1 - fraction):.2f} ft gross).",
                styles["CalcNote"],
            ))

    story.append(Spacer(1, 0.12 * inch))
    perf_rows = [
        ["Check", "Value"],
        ["Detention time (effective)", f"{tank_result.effective_detention_time_min:.1f} min"],
        ["Surface overflow rate (SOR)", f"{tank_result.surface_overflow_rate_gpd_ft2:,.0f} gal/day/ft²"],
        ["Mean horizontal velocity, Vm", f"{tank_result.mean_horizontal_velocity_ft_s:.5f} ft/s"],
        ["Settling velocity, Vs", f"{tank_result.settling_velocity_ft_s:.5f} ft/s"],
        ["Particle Reynolds number", f"{tank_result.reynolds_number:.3f}"],
    ]
    t = Table(perf_rows, hAlign="LEFT", colWidths=[3.0 * inch, 3.2 * inch])
    t.setStyle(_table_style())
    story.append(t)


# ---------------------------------------------------------------------------
# Section 8: Receiving system capacity (optional)
# ---------------------------------------------------------------------------

def _receiving_system_section(story, styles, receiving_checks, design_flow_gpm):
    if not receiving_checks:
        return
    story.append(Spacer(1, 0.18 * inch))
    story.append(Paragraph("Receiving / Discharge System Capacity", styles["CalcHeading"]))
    story.append(Paragraph(
        f"Design dewatering discharge flow: {design_flow_gpm:,.1f} gpm ({design_flow_gpm * 0.002228009:.3f} cfs), "
        "checked against the steady-state hydraulic capacity of each receiving component below.",
        styles["CalcBody"],
    ))
    rows = [["Component", "Required Flow (gpm)", "Available Capacity (gpm)", "Ratio", "Status"]]
    for rc in receiving_checks:
        ratio = rc["capacity_gpm"] / design_flow_gpm if design_flow_gpm else float("inf")
        rows.append([rc["name"], f"{design_flow_gpm:,.1f}", f"{rc['capacity_gpm']:,.1f}", f"{ratio:.2f}", rc["status"]])
    t = Table(rows, hAlign="LEFT", colWidths=[1.8 * inch, 1.3 * inch, 1.5 * inch, 0.7 * inch, 0.8 * inch])
    t.setStyle(_status_table_style(rows, 4))
    story.append(t)
    for rc in receiving_checks:
        if rc.get("notes"):
            story.append(Paragraph(f"&bull; <b>{rc['name']}:</b> {rc['notes']}", styles["CalcNote"]))


# ---------------------------------------------------------------------------
# Section 9: Design Checks (pass/fail)
# ---------------------------------------------------------------------------

def _design_checks_section(story, styles, tank_result, receiving_checks, selected_check):
    checks = []
    if tank_result is not None:
        checks.append(["Stokes' Law applicability", "Re < 1", f"{tank_result.reynolds_number:.3f}",
                        "PASS" if tank_result.stokes_law_valid else "FAIL"])
        checks.append(["Settling / capture", "Vs ≥ SOR", "--",
                        "PASS" if tank_result.capture_check_pass else "FAIL"])
        scour_ratio = (tank_result.settling_velocity_ft_s / tank_result.mean_horizontal_velocity_ft_s
                       if tank_result.mean_horizontal_velocity_ft_s else float("inf"))
        scour_status = "PASS" if tank_result.velocity_check_pass else "FAIL"
        if tank_result.velocity_check_pass and scour_ratio < 2.0:
            scour_status = "LOW MARGIN"
        checks.append(["Scour", "Vs > Vm", f"{scour_ratio:.2f} ratio", scour_status])
    if selected_check is not None:
        checks.append([f"Selected system capacity ({selected_check.name})", "Provided ≥ Required",
                        f"{selected_check.capacity_ratio:.2f}x", selected_check.status])
    for rc in (receiving_checks or []):
        checks.append([f"Receiving capacity: {rc['name']}", "Q_cap ≥ Q_design",
                        f"{rc['capacity_gpm']:,.0f} gpm", rc["status"]])

    if not checks:
        return
    story.append(Spacer(1, 0.18 * inch))
    story.append(Paragraph("Design Checks", styles["CalcHeading"]))
    # Wrap the "Check" column in Paragraphs so long labels (e.g. a selected
    # system's name, or a receiving component's name) wrap within the column
    # instead of overflowing into the "Criterion" column.
    header = ["Check", "Criterion", "Result", "Status"]
    body_rows = [[Paragraph(row[0], styles["CalcBody"]), row[1], row[2], row[3]] for row in checks]
    rows = [header] + body_rows
    t = Table(rows, hAlign="LEFT", colWidths=[2.6 * inch, 1.3 * inch, 1.2 * inch, 1.0 * inch])
    t.setStyle(_status_table_style([header] + checks, 3))
    story.append(t)

    advisories = []
    if tank_result is not None and tank_result.warnings:
        advisories.extend(tank_result.warnings)
    if advisories:
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("Engineering Advisory", styles["CalcSubHeading"]))
        for a in advisories:
            story.append(Paragraph(f"&bull; {a}", styles["CalcBody"]))


# ---------------------------------------------------------------------------
# Section 10: Permit screening -- omitted entirely if nothing configured
# ---------------------------------------------------------------------------

def _permit_screening_section(story, styles, permit_screening, regulatory_basis):
    if permit_screening is None:
        return
    story.append(Spacer(1, 0.18 * inch))
    story.append(Paragraph("General-Permit Threshold Screening", styles["CalcHeading"]))
    rows = [["Criterion", "Project Value", "Threshold", "Status"]]
    for row in permit_screening:
        rows.append([row.criterion, row.value_label, row.threshold_label, row.status])
    t = Table(rows, hAlign="LEFT", colWidths=[2.0 * inch, 1.5 * inch, 1.5 * inch, 1.1 * inch])
    t.setStyle(_status_table_style(rows, 3))
    story.append(t)
    if regulatory_basis:
        story.append(Paragraph(f"<b>Regulatory basis:</b> {regulatory_basis}", styles["CalcBody"]))
    story.append(Paragraph(
        "Screening only -- thresholds are whatever was entered in the tool and are not hardcoded "
        "regulatory values. Confirm the governing agency's current general-permit criteria before "
        "relying on this table.",
        styles["CalcNote"],
    ))


# ---------------------------------------------------------------------------
# Appendix: full substituted backup calculations (unchanged content,
# demoted from "the report" to "the appendix" per the audit).
# ---------------------------------------------------------------------------

def _appendix(story, styles, aquifer, zone_results, tank_inputs, tank_result, min_tank_result):
    story.append(PageBreak())
    story.append(Paragraph("Appendix -- Backup Calculations", styles["Title"]))
    story.append(Paragraph(
        "Every formula used above, with the project's actual numbers substituted in, so results can "
        "be checked by hand.",
        styles["CalcNote"],
    ))

    story.append(Paragraph("Zone-by-Zone Backup Calculations", styles["CalcHeading"]))
    for r in zone_results:
        z = r.zone
        k_m_s = aquifer.k_m_s
        story.append(Paragraph(f"{z.name}" + (f" -- {z.description}" if z.description else ""),
                                styles["CalcSubHeading"]))
        lines = []
        lines.append(f"Groundwater elev = {z.groundwater_elev_ft:.2f} ft   "
                      f"Excavation bottom elev = {z.excavation_bottom_elev_ft:.2f} ft   "
                      f"Operating margin = {z.operating_margin_ft:.2f} ft")
        lines.append(f"Dewatered elev = {z.excavation_bottom_elev_ft:.2f} - {z.operating_margin_ft:.2f} "
                      f"= {z.dewatered_elev_ft:.2f} ft")
        lines.append(f"s = {z.groundwater_elev_ft:.2f} - ({z.dewatered_elev_ft:.2f}) "
                      f"= {z.drawdown_ft:.2f} ft  ( = {z.drawdown_m:.4f} m )")
        lines.append("")
        lines.append(f"R = 3000 x {z.drawdown_m:.4f} x sqrt({k_m_s:.3e})"
                      f" = {r.radius_of_influence_m:.3f} m = {r.radius_of_influence_ft:.2f} ft")
        lines.append(f"re = sqrt({z.width_ft:.2f}ft x {z.length_ft:.2f}ft / pi)"
                      f" = {r.effective_radius_m:.3f} m = {r.effective_radius_ft:.2f} ft")
        lines.append("")
        H_m = aquifer.thickness_m
        h_m = H_m - z.drawdown_m
        if aquifer.aquifer_type.value == "unconfined":
            lines.append(f"h = H - s = {H_m:.3f} - {z.drawdown_m:.4f} = {h_m:.3f} m")
            lines.append(f"Q = pi x {k_m_s:.3e} x ({H_m:.3f}^2 - {h_m:.3f}^2) / "
                         f"ln({r.radius_of_influence_m:.3f} / {r.effective_radius_m:.3f})")
        else:
            T = k_m_s * H_m
            lines.append(f"T = K x b = {k_m_s:.3e} x {H_m:.3f} = {T:.3e} m^2/s")
            lines.append(f"Q = 2 x pi x {T:.3e} x {z.drawdown_m:.4f} / "
                         f"ln({r.radius_of_influence_m:.3f} / {r.effective_radius_m:.3f})")
        lines.append(f"Q = {r.flow_cms:.5f} m^3/s = {r.flow_gpm:,.2f} gpm")
        lines.append("")
        lines.append(f"Daily pumpage = {r.flow_gpm:,.2f} gpm x {z.hours_per_day:.1f} hr/day x 60 min/hr "
                      f"/ 1,000,000 = {r.daily_pumpage_mgd:.4f} MGD")
        lines.append(f"Total pumpage = {r.daily_pumpage_mgd:.4f} MGD x {z.total_days:.1f} days "
                      f"= {r.total_pumpage_mg:.3f} MG")
        story.append(Paragraph("<br/>".join(lines), styles["CalcMono"]))
        if r.warnings:
            for w in r.warnings:
                story.append(Paragraph(f"&bull; {w}", styles["CalcNote"]))
        story.append(Spacer(1, 0.12 * inch))

    if tank_result is not None:
        story.append(PageBreak())
        story.append(Paragraph("Settling Tank / Turbidity Control -- Backup Calculations", styles["CalcHeading"]))
        story.append(Paragraph(
            "Vs = g&middot;(SG&minus;1)&middot;d&sup2; / (18&middot;&nu;)   "
            "(Stokes' Law, laminar range, Re &lt; 1)<br/>"
            "Re = Vs&middot;d / &nu;<br/>"
            "SOR = Q / (L&middot;W)   Vm = Q / (W&middot;D)",
            styles["CalcMono"],
        ))

        if min_tank_result is not None:
            mtr = min_tank_result
            story.append(Paragraph("Minimum Required Tank Size (auto-sized)", styles["CalcSubHeading"]))
            lines = []
            lines.append(f"Design particle: d = {tank_inputs.particle_diameter_ft:.6f} ft, "
                          f"SG = {tank_inputs.specific_gravity:.2f}, "
                          f"nu = {tank_inputs.kinematic_viscosity_ft2_s:.3e} ft^2/s")
            lines.append(f"Vs = 32.2 x ({tank_inputs.specific_gravity:.2f}-1) x "
                         f"{tank_inputs.particle_diameter_ft:.6f}^2 / (18 x {tank_inputs.kinematic_viscosity_ft2_s:.3e})"
                         f" = {mtr.settling_velocity_ft_s:.5f} ft/s")
            lines.append(f"Re = {mtr.settling_velocity_ft_s:.5f} x {tank_inputs.particle_diameter_ft:.6f} / "
                         f"{tank_inputs.kinematic_viscosity_ft2_s:.3e} = {mtr.reynolds_number:.3f} "
                         f"({'Stokes valid, Re < 1' if mtr.stokes_law_valid else 'OUTSIDE Stokes range'})")
            lines.append("")
            lines.append(f"Required surface area:  A_min = Q / Vs")
            lines.append(f"A_min = {mtr.required_surface_area_ft2:.2f} ft^2")
            lines.append(f"At L:W = {mtr.min_length_ft/mtr.min_width_ft if mtr.min_width_ft else 0:.2f}:1 -> "
                         f"exact min. W = {mtr.min_width_ft:.2f} ft, min. L = {mtr.min_length_ft:.2f} ft")
            lines.append(f"Rounded up to buildable dimensions: W = {mtr.width_ft:.1f} ft, L = {mtr.length_ft:.1f} ft")
            lines.append(f"Scour check at this width: min. depth for Vm <= Vs = {mtr.min_depth_for_scour_ft:.2f} ft "
                         f"(requested {mtr.requested_depth_ft:.1f} ft)")
            lines.append(f"Depth used: D = {mtr.depth_ft:.1f} ft"
                         + (" (increased to satisfy the scour check)" if mtr.depth_was_increased else ""))
            if tank_result.effective_volume_gal != tank_result.volume_gal:
                lines.append(f"Effective (freeboard/sediment-adjusted) volume = {tank_result.effective_volume_gal:,.0f} gal "
                             f"of {tank_result.volume_gal:,.0f} gal gross")
            lines.append("")
            lines.append(f"SMALLEST REQUIRED TANK: {mtr.length_ft:.1f} ft (L) x {mtr.width_ft:.1f} ft (W) "
                         f"x {mtr.depth_ft:.1f} ft (D)")
            story.append(Paragraph("<br/>".join(lines), styles["CalcMono"]))
            if mtr.warnings:
                for w in mtr.warnings:
                    story.append(Paragraph(f"&bull; {w}", styles["CalcNote"]))
            story.append(Spacer(1, 0.12 * inch))

        if tank_inputs is not None:
            story.append(Paragraph(
                "Provided Tank Check" if min_tank_result is None else "Resulting Tank -- Full Check",
                styles["CalcSubHeading"],
            ))
            q_cfs = tank_inputs.flow_gpm * 0.002228009
            lines = []
            lines.append(f"Q = {tank_inputs.flow_gpm:,.2f} gpm = {q_cfs:.5f} cfs")
            lines.append(f"Tank: L = {tank_inputs.length_ft:.1f} ft, W = {tank_inputs.width_ft:.1f} ft, "
                          f"D = {tank_inputs.depth_ft:.1f} ft")
            lines.append(f"Volume = {tank_inputs.length_ft:.1f} x {tank_inputs.width_ft:.1f} x "
                          f"{tank_inputs.depth_ft:.1f} = {tank_result.volume_cf:,.0f} ft^3 "
                          f"= {tank_result.volume_gal:,.0f} gal")
            lines.append(f"Detention time = {tank_result.volume_gal:,.0f} gal / {tank_inputs.flow_gpm:,.2f} gpm "
                          f"= {tank_result.detention_time_min:.1f} min")
            lines.append(f"Vm = Q / (W x D) = {q_cfs:.5f} / ({tank_inputs.width_ft:.1f} x {tank_inputs.depth_ft:.1f}) "
                          f"= {tank_result.mean_horizontal_velocity_ft_s:.5f} ft/s")
            lines.append(f"Vs = {tank_result.settling_velocity_ft_s:.5f} ft/s "
                          f"({'Vs > Vm -- PASS' if tank_result.velocity_check_pass else 'Vs <= Vm -- FAIL (scour risk)'})")
            lines.append(f"SOR = {tank_result.surface_overflow_rate_gpd_ft2:,.0f} gal/day/ft^2")
            lines.append(f"Particle Reynolds number = {tank_result.reynolds_number:.3f} "
                          f"({'Stokes valid' if tank_result.stokes_law_valid else 'outside Stokes range'})")
            story.append(Paragraph("<br/>".join(lines), styles["CalcMono"]))
            if tank_result.warnings:
                for w in tank_result.warnings:
                    story.append(Paragraph(f"&bull; {w}", styles["CalcNote"]))


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------

def export_dewatering_calc_pdf(
    output_path: str,
    project_name: str,
    project_address: str,
    aquifer: AquiferParams,
    zone_results: List[ZoneResult],
    zone_errors: Optional[List[str]] = None,
    overlap_notes: Optional[List[str]] = None,
    summary: Optional[WaterBalanceSummary] = None,
    permit_screening: Optional[List[PermitScreeningRow]] = None,
    tank_inputs: Optional[TankInputs] = None,
    tank_result: Optional[TankResult] = None,
    min_tank_result: Optional[MinTankResult] = None,
    tank_mode: str = "manual",
    selected_check: Optional[SelectedTankCheck] = None,
    receiving_checks: Optional[List[Dict[str, Any]]] = None,
    project_info: Optional[Dict[str, str]] = None,
    criteria_basis: Optional[Dict[str, str]] = None,
    extra_assumptions: Optional[List[str]] = None,
    regulatory_basis: Optional[str] = None,
) -> str:
    styles = _styles()
    doc = SimpleDocTemplate(output_path, pagesize=letter,
                             leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                             topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                             title=f"Dewatering Design Report - {project_name}")
    story = []

    _design_summary_section(story, styles, project_name, project_address, project_info,
                             zone_results, summary, tank_result, min_tank_result, tank_mode,
                             selected_check, receiving_checks)
    _criteria_section(story, styles, aquifer, zone_results, tank_inputs, criteria_basis)
    _assumptions_section(story, styles, aquifer, tank_result, extra_assumptions)
    _methodology_section(story, styles, aquifer)
    _zone_summary_and_water_balance(story, styles, zone_results, zone_errors, overlap_notes, summary)
    _tank_section(story, styles, tank_inputs, tank_result, min_tank_result, tank_mode, selected_check)
    _receiving_system_section(story, styles, receiving_checks, summary.max_flow_gpm if summary else 0.0)
    _design_checks_section(story, styles, tank_result, receiving_checks, selected_check)
    _permit_screening_section(story, styles, permit_screening, regulatory_basis)
    _appendix(story, styles, aquifer, zone_results, tank_inputs, tank_result, min_tank_result)

    doc.build(story)
    return output_path
