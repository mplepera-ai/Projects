"""
reports/calc_report_pdf.py

A standalone "Drainage Calculations" backup sheet -- site area
breakdown, SCS storage-method runoff volume (existing vs proposed),
swale storage calculations, and exfiltration trench L1/L2 sizing --
matching the narrative calc-sheet style engineers keep as backup to
the Cascade model (independent of the main permit report/PDF).
"""

from __future__ import annotations
from typing import List, Optional
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

from storage.runoff_volume import RunoffVolumeResult
from storage.swale import Swale
from storage.exfiltration import ExfiltrationTrench


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CalcBody", parent=styles["Normal"], fontSize=10, leading=14))
    styles.add(ParagraphStyle(name="CalcHeading", parent=styles["Heading2"], spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="CalcMono", parent=styles["Normal"], fontName="Courier", fontSize=9, leading=13))
    return styles


def _table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])


def export_swale_exfiltration_calc_pdf(
    output_path: str,
    project_name: str,
    project_address: str,
    existing_site_sqft: float,
    existing_pervious_sqft: float,
    proposed_site_sqft: float,
    proposed_pervious_sqft: float,
    existing_runoff: RunoffVolumeResult,
    proposed_runoff: RunoffVolumeResult,
    swales: List[Swale],
    exfiltration_trench: Optional[ExfiltrationTrench],
    required_wq_volume_cuft: float,
) -> str:
    styles = _styles()
    doc = SimpleDocTemplate(output_path, pagesize=letter,
                             leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                             topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                             title=f"Drainage Calculations - {project_name}")
    story = []

    story.append(Paragraph("Drainage Calculations", styles["Title"]))
    story.append(Paragraph(project_name, styles["Heading3"]))
    story.append(Paragraph(project_address, styles["CalcBody"]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Existing Conditions", styles["CalcHeading"]))
    ex_impervious = existing_site_sqft - existing_pervious_sqft
    story.append(Paragraph(
        f"SITE AREA = {existing_site_sqft:,.0f} SF<br/>"
        f"PERVIOUS AREA = {existing_pervious_sqft:,.0f} SF "
        f"({existing_pervious_sqft/existing_site_sqft*100:.1f}%)<br/>"
        f"IMPERVIOUS AREA = {ex_impervious:,.0f} SF ({ex_impervious/existing_site_sqft*100:.1f}%)",
        styles["CalcMono"],
    ))

    story.append(Paragraph("Proposed Site Distribution", styles["CalcHeading"]))
    pr_impervious = proposed_site_sqft - proposed_pervious_sqft
    story.append(Paragraph(
        f"SITE AREA = {proposed_site_sqft:,.0f} SF<br/>"
        f"PERVIOUS AREA = {proposed_pervious_sqft:,.0f} SF "
        f"({proposed_pervious_sqft/proposed_site_sqft*100:.1f}%)<br/>"
        f"IMPERVIOUS AREA = {pr_impervious:,.0f} SF ({pr_impervious/proposed_site_sqft*100:.1f}%)",
        styles["CalcMono"],
    ))

    story.append(Paragraph("Required Storage (SCS Storage Method)", styles["CalcHeading"]))
    story.append(Paragraph(
        "R = (P - 0.2S)^2 / (P + 0.8S)         V = Area &times; R / 12",
        styles["CalcBody"],
    ))
    table_data = [
        ["", "Existing", "Proposed"],
        ["Rainfall Depth, P (in)", f"{existing_runoff.rainfall_depth_inches:.2f}", f"{proposed_runoff.rainfall_depth_inches:.2f}"],
        ["Compacted Soil Storage (in)", f"{existing_runoff.compacted_soil_storage_inches:.2f}", f"{proposed_runoff.compacted_soil_storage_inches:.2f}"],
        ["Effective Storage, S (in)", f"{existing_runoff.effective_storage_inches:.2f}", f"{proposed_runoff.effective_storage_inches:.2f}"],
        ["Runoff, R (in)", f"{existing_runoff.runoff_inches:.2f}", f"{proposed_runoff.runoff_inches:.2f}"],
        ["Runoff Volume, V (CF)", f"{existing_runoff.runoff_volume_cuft:,.0f}", f"{proposed_runoff.runoff_volume_cuft:,.0f}"],
    ]
    t = Table(table_data, hAlign="LEFT", colWidths=[2.4 * inch, 1.6 * inch, 1.6 * inch])
    t.setStyle(_table_style())
    story.append(t)
    net_increase = proposed_runoff.runoff_volume_cuft - existing_runoff.runoff_volume_cuft
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph(
        f"<b>Net increase in runoff volume (Proposed - Existing) = {net_increase:,.0f} CF</b>",
        styles["CalcBody"],
    ))

    if swales:
        story.append(Paragraph("Swale Calculations", styles["CalcHeading"]))
        swale_rows = [["Swale", "Max Depth (ft)", "Length (ft)", "Max Storage (CF)"]]
        total_swale_cf = 0.0
        for sw in swales:
            summ = sw.summary()
            swale_rows.append([
                sw.name, f"{summ['max_depth_ft']:.2f}", f"{summ['length_ft']:.0f}",
                f"{summ['max_storage_cuft']:,.0f}",
            ])
            total_swale_cf += summ["max_storage_cuft"]
        t2 = Table(swale_rows, hAlign="LEFT", colWidths=[1.8 * inch, 1.4 * inch, 1.2 * inch, 1.6 * inch])
        t2.setStyle(_table_style())
        story.append(t2)
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph(f"<b>Total dry-retention swale storage = {total_swale_cf:,.0f} CF</b>", styles["CalcBody"]))

        if exfiltration_trench is not None:
            vwq_exf_cf = max(proposed_runoff.runoff_volume_cuft - total_swale_cf, 0.0)
            story.append(Paragraph(
                f"Vwq (exfiltration trench) = Proposed Runoff Volume - Swale Storage "
                f"= {proposed_runoff.runoff_volume_cuft:,.0f} - {total_swale_cf:,.0f} = {vwq_exf_cf:,.0f} CF",
                styles["CalcBody"],
            ))

    if exfiltration_trench is not None:
        t = exfiltration_trench
        story.append(Paragraph("Exfiltration Trench Calculations", styles["CalcHeading"]))
        story.append(Paragraph(
            "L1 = FS&middot;%WQ&middot;Vwq / [K(H2&middot;W + 2&middot;Heff&middot;Du - Du&sup2; + 2&middot;Heff&middot;Ds) + 0.000139&middot;W&middot;Du]<br/>"
            "L2 = FS&middot;%WQ&middot;Vwq / [K(2&middot;Heff&middot;Du - Du&sup2; + 2&middot;Heff&middot;Ds) + 0.000139&middot;W&middot;Du]  "
            "(conservative -- governs when Ds&gt;Du or W&gt;2(Du+Ds))",
            styles["CalcBody"],
        ))
        report = t.report(required_wq_volume_cuft)
        rows = [
            ["K (cfs/ft^2-ft)", f"{report['K']:.6f}"],
            ["FS", f"{report['factor_of_safety']:.2f}"],
            ["%WQ", f"{report['percent_wq_required']:.2f}"],
            ["H2 (ft)", f"{report['H2_ft']:.2f}"],
            ["Heff (ft)", f"{report['Heff_ft']:.2f}"],
            ["Du (ft)", f"{report['Du_ft']:.2f}"],
            ["Ds (ft)", f"{report['Ds_ft']:.2f}"],
            ["W (ft)", f"{report['width_ft']:.2f}"],
            ["Equation Used", report["equation_used"]],
            ["Required Length (LF)", f"{report['required_length_ft']:.1f}"],
            ["Provided Length (LF)", f"{report['provided_length_ft']:.1f}"],
            ["STATUS", report["status"]],
        ]
        t3 = Table([["Parameter", "Value"]] + rows, hAlign="LEFT", colWidths=[2.6 * inch, 2.6 * inch])
        style_cmds = _table_style().getCommands() if hasattr(_table_style(), "getCommands") else []
        t3.setStyle(_table_style())
        story.append(t3)
        if report["warnings"]:
            story.append(Spacer(1, 0.1 * inch))
            for w in report["warnings"]:
                story.append(Paragraph(f"&bull; {w}", styles["CalcBody"]))

    doc.build(story)
    return output_path
