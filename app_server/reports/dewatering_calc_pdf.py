"""
reports/dewatering_calc_pdf.py

"Backup Calculations" PDF for the Dewatering tool -- shows each formula
used (Sichardt radius of influence, Dupuit-Forchheimer / Thiem radial
flow, water balance roll-up, and settling-tank sizing via Stokes' Law /
surface overflow rate) with the actual project numbers substituted in,
per zone, plus summary and permit-threshold-screening tables. Matches
the narrative "show your work" backup-calc style of
reports/calc_report_pdf.py (the swale/exfiltration report) so a
reviewing engineer or regulator can follow the math by hand.

This module only formats already-computed dataclass results from
dewatering/calculations.py -- it never recomputes or re-derives a
number independently of the engine.
"""

from __future__ import annotations
from typing import List, Optional
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors

from dewatering.calculations import (
    AquiferParams, ZoneResult, WaterBalanceSummary, TankInputs, TankResult, MinTankResult,
)


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CalcBody", parent=styles["Normal"], fontSize=10, leading=14))
    styles.add(ParagraphStyle(name="CalcHeading", parent=styles["Heading2"], spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="CalcSubHeading", parent=styles["Heading3"], spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="CalcMono", parent=styles["Normal"], fontName="Courier", fontSize=9, leading=13))
    styles.add(ParagraphStyle(name="CalcNote", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#555555")))
    return styles


def _table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])


def export_dewatering_calc_pdf(
    output_path: str,
    project_name: str,
    project_address: str,
    aquifer: AquiferParams,
    zone_results: List[ZoneResult],
    zone_errors: Optional[List[str]] = None,
    overlap_notes: Optional[List[str]] = None,
    summary: Optional[WaterBalanceSummary] = None,
    permit_flags: Optional[List[str]] = None,
    tank_inputs: Optional[TankInputs] = None,
    tank_result: Optional[TankResult] = None,
    min_tank_result: Optional[MinTankResult] = None,
) -> str:
    styles = _styles()
    doc = SimpleDocTemplate(output_path, pagesize=letter,
                             leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                             topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                             title=f"Dewatering Calculations - {project_name}")
    story = []

    # --- Header -------------------------------------------------------
    story.append(Paragraph("Dewatering Design Calculations", styles["Title"]))
    story.append(Paragraph(project_name, styles["Heading3"]))
    if project_address:
        story.append(Paragraph(project_address, styles["CalcBody"]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "This backup sheet reproduces every formula used by the Dewatering tool with the "
        "project's actual numbers substituted in, so the results can be checked by hand. "
        "It is a calculation aid, not a substitute for a signed and sealed engineering "
        "analysis where one is required.",
        styles["CalcNote"],
    ))

    # --- Aquifer parameters --------------------------------------------
    story.append(Paragraph("Aquifer Parameters", styles["CalcHeading"]))
    aq_rows = [
        ["Parameter", "Value"],
        ["Aquifer type", aquifer.aquifer_type.value.capitalize()],
        ["Hydraulic conductivity, K", f"{aquifer.hydraulic_conductivity_ft_day:,.3g} ft/day  "
                                       f"({aquifer.k_m_s:.3e} m/s)"],
        ["Aquifer thickness, H (unconfined) / b (confined)", f"{aquifer.aquifer_thickness_ft:,.2f} ft "
                                                                f"({aquifer.thickness_m:.3f} m)"],
    ]
    t = Table(aq_rows, hAlign="LEFT", colWidths=[3.2 * inch, 3.0 * inch])
    t.setStyle(_table_style())
    story.append(t)

    # --- Methodology / formulas -----------------------------------------
    story.append(Paragraph("Methodology", styles["CalcHeading"]))
    story.append(Paragraph(
        "For each excavation zone: drawdown s is the static groundwater elevation minus the "
        "dewatered (pumping) elevation (excavation bottom minus the operating margin). "
        "Sichardt's empirical formula gives the radius of influence, and the excavation "
        "footprint is converted to an equivalent circular well radius. Steady radial "
        "groundwater flow to that equivalent well is then computed with the "
        "Dupuit-Forchheimer equation (unconfined aquifer) or the Thiem equation (confined "
        "aquifer). Radial-flow math is carried out in SI units (m, m/s) to match how the "
        "formulas are conventionally published, then converted back to ft / gpm for reporting, "
        "using the exact factor 1 m = 3.280839895 ft.",
        styles["CalcBody"],
    ))
    story.append(Paragraph(
        "s = GW elev &minus; (Excavation Bottom elev &minus; Operating Margin)", styles["CalcMono"]))
    story.append(Paragraph(
        "Sichardt:  R (m) = 3000 &middot; s (m) &middot; &radic;K (m/s)", styles["CalcMono"]))
    story.append(Paragraph(
        "Equivalent well radius:  re (m) = &radic;(L&middot;W (m&sup2;) / &pi;)", styles["CalcMono"]))
    story.append(Paragraph(
        "Unconfined (Dupuit-Forchheimer):  Q = &pi;&middot;K&middot;(H&sup2; &minus; h&sup2;) / ln(R / re),  "
        "h = H &minus; s", styles["CalcMono"]))
    story.append(Paragraph(
        "Confined (Thiem):  Q = 2&middot;&pi;&middot;T&middot;s / ln(R / re),  T = K&middot;b", styles["CalcMono"]))

    # --- Per-zone backup calcs -------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Zone-by-Zone Backup Calculations", styles["CalcHeading"]))

    flow_label = "Q = π·K·(H² - h²) / ln(R/re)" if aquifer.aquifer_type.value == "unconfined" \
        else "Q = 2·π·T·s / ln(R/re)"

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

    if zone_errors:
        story.append(Paragraph("Zones Not Computed", styles["CalcSubHeading"]))
        for e in zone_errors:
            story.append(Paragraph(f"&bull; {e}", styles["CalcNote"]))

    if overlap_notes:
        story.append(Paragraph("Zone Overlap / Superposition Notes", styles["CalcSubHeading"]))
        for n in overlap_notes:
            story.append(Paragraph(f"&bull; {n}", styles["CalcNote"]))

    # --- Water balance summary -------------------------------------------
    if summary is not None:
        story.append(PageBreak())
        story.append(Paragraph("Water Balance Summary", styles["CalcHeading"]))
        rows = [
            ["Metric", "Value"],
            ["Average flow (all zones)", f"{summary.average_flow_gpm:,.1f} gpm"],
            ["Maximum single-zone flow", f"{summary.max_flow_gpm:,.1f} gpm"],
            ["Maximum daily pumpage", f"{summary.max_daily_pumpage_mgd:.4f} MGD"],
            ["Total project pumpage", f"{summary.total_project_pumpage_mg:,.2f} MG"],
            ["Total pumping duration", f"{summary.total_pumping_days:,.0f} days"],
        ]
        t = Table(rows, hAlign="LEFT", colWidths=[3.2 * inch, 3.0 * inch])
        t.setStyle(_table_style())
        story.append(t)

        if permit_flags:
            story.append(Spacer(1, 0.15 * inch))
            story.append(Paragraph("General-Permit Threshold Screening", styles["CalcSubHeading"]))
            for f in permit_flags:
                story.append(Paragraph(f"&bull; {f}", styles["CalcBody"]))
            story.append(Paragraph(
                "Screening only -- thresholds are whatever was entered in the tool and are not "
                "hardcoded regulatory values. Confirm the governing agency's current general-permit "
                "criteria before relying on this flag.",
                styles["CalcNote"],
            ))

    # --- Settling tank -----------------------------------------------------
    if tank_result is not None:
        story.append(PageBreak())
        story.append(Paragraph("Settling Tank / Turbidity Control Calculations", styles["CalcHeading"]))
        story.append(Paragraph(
            "Type 1 (discrete-particle) settling design, per Stokes' Law and the surface "
            "overflow rate (SOR) method. Settling velocity Vs is compared against the mean "
            "horizontal (flow-through) velocity Vm as a scour check, and against the SOR as a "
            "capture check.",
            styles["CalcBody"],
        ))
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

    doc.build(story)
    return output_path
