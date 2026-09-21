"""
reports/pdf_export.py

Renders the same data as reports/generator.py's Markdown report into
an actual PDF (Section 59). Per Section 71 ("do not independently
recalculate numbers inside report templates"), every number here is
read from the already-computed ScenarioRunResult / Finding objects --
this module only formats and lays out, never calculates.

Includes: page numbers, project name/number, report date, and units
(Section 59's explicit PDF requirements).
"""

from __future__ import annotations
from typing import Dict, List
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether,
)
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate
from reportlab.platypus.frames import Frame

from project.model import Project, ScenarioRunResult, compare_existing_vs_proposed
from qa.validation import Finding, Level


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportBody", parent=styles["Normal"], fontSize=10, leading=14))
    styles.add(ParagraphStyle(name="SectionHeading", parent=styles["Heading2"],
                               spaceBefore=14, spaceAfter=6))
    styles.add(ParagraphStyle(name="MonoSmall", parent=styles["Normal"],
                               fontName="Courier", fontSize=7.5, leading=9))
    return styles


def _footer(canvas, doc, project: Project):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    m = project.metadata
    footer_text = f"{m.project_name}  |  Project No. {m.project_number}  |  {m.report_date}"
    canvas.drawString(0.75 * inch, 0.5 * inch, footer_text)
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _cover_page(project: Project, styles) -> List:
    m = project.metadata
    story = []
    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph("DRAINAGE CALCULATIONS", styles["Title"]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(f"for", styles["Normal"]))
    story.append(Paragraph(f"<b>{m.project_name}</b>", styles["Heading1"]))
    story.append(Paragraph(m.project_address, styles["Normal"]))
    story.append(Paragraph(f"Project No. {m.project_number}", styles["Normal"]))
    story.append(Paragraph(f"Report Date: {m.report_date}", styles["Normal"]))
    story.append(Spacer(1, 0.6 * inch))
    story.append(Paragraph(f"Prepared For: {m.client}", styles["Normal"]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(f"Prepared By: {m.engineer_name}, P.E.", styles["Normal"]))
    story.append(Paragraph(f"Florida P.E. License No. {m.pe_license_number}", styles["Normal"]))
    story.append(Paragraph(m.engineering_firm, styles["Normal"]))
    story.append(PageBreak())
    return story


def _permit_criteria_section(project: Project, styles) -> List:
    story = [Paragraph("Permit Criteria", styles["SectionHeading"])]
    text = (
        f"The proposed stormwater management system was analyzed using the applicable "
        f"methods of {project.metadata.regulatory_agency or 'the governing regulatory agency'} "
        f"under the <b>{project.regulatory_profile.name}</b> profile "
        f"(version {project.regulatory_profile.version}). The system was evaluated for the "
        f"applicable water quantity and off-site discharge criteria summarized below. "
        f"Vertical datum: {project.metadata.vertical_datum}."
    )
    story.append(Paragraph(text, styles["ReportBody"]))
    return story


def _summary_table(project: Project, results: Dict[str, ScenarioRunResult], styles) -> List:
    # Freeboard (berm elevation minus peak stage) is a plain output value,
    # only shown for basins with a berm elevation entered -- see
    # hydraulics/basin.py's Basin.berm_elevation_ft docstring for why
    # this isn't checked against a required minimum here.
    story = [Paragraph("Final Model Summary", styles["SectionHeading"])]
    header = ["Scenario", "Condition", "Event", "Basin", "Peak Stage (ft)", "Offsite Disch. (ac-ft)", "ZOD", "Freeboard (ft)"]
    rows = [header]
    for sid, r in sorted(results.items()):
        basins = project.conditions[r.scenario.condition_name].network.basins
        for bid in r.network_result.peak_stage_ft:
            peak = r.network_result.peak_stage_ft[bid]
            offsite = r.network_result.mass_balance[bid].offsite_discharge_acre_ft
            berm = basins[bid].berm_elevation_ft
            freeboard = f"{berm - peak:.3f}" if berm is not None else "—"
            rows.append([
                sid, r.scenario.condition_name, r.scenario.event_code, bid,
                f"{peak:.3f}", f"{offsite:.4f}", "Yes" if r.scenario.zero_offsite_discharge else "No", freeboard,
            ])
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    style_cmds = _table_style_commands()
    for i, row in enumerate(rows[1:], start=1):
        freeboard_str = row[-1]
        if freeboard_str != "—" and float(freeboard_str) < 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F8D7DA")))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    return story


def _comparison_table(results: Dict[str, ScenarioRunResult], styles) -> List:
    story = [Paragraph("Existing vs. Proposed Comparison", styles["SectionHeading"])]
    rows_data = compare_existing_vs_proposed(results)
    header = ["Event", "Basin", "Existing (ft)", "Proposed (ft)", "Difference (ft)", "Result"]
    rows = [header]
    for r in rows_data:
        ex = f"{r.existing_peak_stage_ft:.2f}" if r.existing_peak_stage_ft is not None else "—"
        diff = f"{r.difference_ft:+.2f}" if r.difference_ft is not None else "—"
        rows.append([r.event_code, r.basin_id, ex, f"{r.proposed_peak_stage_ft:.2f}", diff, r.result])
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    style_cmds = _table_style_commands()
    for i, r in enumerate(rows_data, start=1):
        if r.result == "REVIEW REQUIRED":
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FFF3CD")))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    return story


def _design_narrative_section(section: Dict, styles) -> List:
    """Renders one {"heading", "paragraphs"} dict from
    reports/generator.py's design_narrative_* functions -- those
    functions are the single source of the actual wording, shared with
    the Markdown report, so this PDF path only formats them and never
    restates the boilerplate text itself (avoids the two report formats
    drifting apart)."""
    story = [Paragraph(section["heading"], styles["SectionHeading"])]
    for p in section["paragraphs"]:
        story.append(Paragraph(p, styles["ReportBody"]))
        story.append(Spacer(1, 0.08 * inch))
    return story


def _qa_section(findings: List[Finding], styles) -> List:
    story = [Paragraph("QA/QC Summary", styles["SectionHeading"])]
    if not findings:
        story.append(Paragraph("No findings.", styles["ReportBody"]))
        return story
    for level in (Level.CRITICAL, Level.WARNING, Level.INFO):
        subset = [f for f in findings if f.level == level]
        if not subset:
            continue
        color = {"CRITICAL": "#C0392B", "WARNING": "#B7950B", "INFO": "#555555"}[level.value]
        story.append(Paragraph(f'<font color="{color}"><b>{level.value}</b> ({len(subset)})</font>',
                                styles["ReportBody"]))
        for f in subset:
            story.append(Paragraph(f"&bull; [{f.object_id}] {f.message}", styles["ReportBody"]))
        story.append(Spacer(1, 0.1 * inch))
    return story


def _detailed_output_section(project: Project, results: Dict[str, ScenarioRunResult], styles,
                              options=None) -> List:
    from reports.generator import cascade_style_output, ReportOptions
    if options is None:
        options = ReportOptions()
    story = [Paragraph("Detailed Routing Output", styles["SectionHeading"])]
    for sid, r in sorted(results.items()):
        if not options.wants_cascade_detail(sid):
            continue
        basins_by_id = project.conditions[r.scenario.condition_name].network.basins
        story.append(Paragraph(f"Scenario {sid}", styles["Heading3"]))
        text = cascade_style_output(r, basins_by_id).replace("\n", "<br/>")
        story.append(Paragraph(text, styles["MonoSmall"]))
        story.append(Spacer(1, 0.15 * inch))
    return story


def _table_style_commands():
    return [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]


def _table_style():
    return TableStyle(_table_style_commands())


def export_permit_report_pdf(
    project: Project,
    results: Dict[str, ScenarioRunResult],
    findings: List[Finding],
    output_path: str,
    options=None,
    storage_ctx: Dict = None,
) -> str:
    from reports.generator import ReportOptions, design_narrative_intro_sections, design_narrative_conclusion_section
    if options is None:
        options = ReportOptions()
    styles = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=f"Drainage Calculations - {project.metadata.project_name}",
    )

    story: List = []
    if options.include_cover:
        story.extend(_cover_page(project, styles))
    if options.include_permit_criteria:
        story.extend(_permit_criteria_section(project, styles))
        story.append(Spacer(1, 0.2 * inch))
    if options.include_design_narrative:
        for section in design_narrative_intro_sections(project, storage_ctx):
            story.extend(_design_narrative_section(section, styles))
        story.append(Spacer(1, 0.1 * inch))
    if options.include_final_summary_table:
        story.extend(_summary_table(project, results, styles))
        story.append(Spacer(1, 0.25 * inch))
    if options.include_comparison_table:
        story.extend(_comparison_table(results, styles))
        story.append(Spacer(1, 0.25 * inch))
    if options.include_design_narrative:
        story.extend(_design_narrative_section(design_narrative_conclusion_section(project), styles))
        story.append(Spacer(1, 0.15 * inch))
    if options.include_qa_summary:
        story.extend(_qa_section(findings, styles))

    detail_scenarios = {sid: r for sid, r in results.items() if options.wants_cascade_detail(sid)}
    if detail_scenarios:
        story.append(PageBreak())
        story.extend(_detailed_output_section(project, detail_scenarios, styles, options))

    def on_page(canvas, doc_):
        _footer(canvas, doc_, project)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return output_path
