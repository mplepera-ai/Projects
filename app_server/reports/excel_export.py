"""
reports/excel_export.py

Builds a drainage-calculation workbook matching the style of the
independent Excel calc sheet engineers keep alongside a Cascade model
(site areas, soil storage, stage-storage tables, exfiltration trench
sizing). Cells that are genuine calculations are written as live
Excel formulas (never pre-computed Python values baked in), per the
xlsx skill's "use formulas, never hardcoded results" rule -- so an
engineer can tweak an input cell and see the sheet recompute.
"""

from __future__ import annotations
from typing import Optional
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from hydraulics.basin import Basin
from storage.exfiltration import ExfiltrationTrench

BLUE_INPUT = Font(color="0000FF")
BLACK_FORMULA = Font(color="000000")
HEADER_FILL = PatternFill(start_color="2C3E50", end_color="2C3E50", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
BOLD = Font(bold=True)


def _write_header_row(ws, row: int, headers: list, start_col: int = 1):
    for i, h in enumerate(headers):
        cell = ws.cell(row=row, column=start_col + i, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def _autosize(ws, n_cols: int, width: int = 16):
    for c in range(1, n_cols + 1):
        ws.column_dimensions[get_column_letter(c)].width = width


def _add_site_area_sheet(wb: Workbook, project_name: str,
                          existing_site_sqft: float, existing_pervious_sqft: float,
                          proposed_site_sqft: float, proposed_pervious_sqft: float):
    ws = wb.create_sheet("Site Areas")
    ws["A1"] = f"Drainage Calculations -- {project_name}"
    ws["A1"].font = Font(bold=True, size=14)

    ws["A3"] = "EXISTING CONDITIONS"
    ws["A3"].font = BOLD
    ws["A4"], ws["B4"] = "Site Area (SF)", existing_site_sqft
    ws["A5"], ws["B5"] = "Pervious Area (SF)", existing_pervious_sqft
    ws["A6"], ws["B6"] = "Impervious Area (SF)", "=B4-B5"
    ws["A7"], ws["B7"] = "Percent Pervious", "=B5/B4"
    ws["B7"].number_format = "0.0%"
    for r in (4, 5):
        ws[f"B{r}"].font = BLUE_INPUT
    for r in (6, 7):
        ws[f"B{r}"].font = BLACK_FORMULA

    ws["A9"] = "PROPOSED CONDITIONS"
    ws["A9"].font = BOLD
    ws["A10"], ws["B10"] = "Site Area (SF)", proposed_site_sqft
    ws["A11"], ws["B11"] = "Pervious Area (SF)", proposed_pervious_sqft
    ws["A12"], ws["B12"] = "Impervious Area (SF)", "=B10-B11"
    ws["A13"], ws["B13"] = "Percent Pervious", "=B11/B10"
    ws["B13"].number_format = "0.0%"
    for r in (10, 11):
        ws[f"B{r}"].font = BLUE_INPUT
    for r in (12, 13):
        ws[f"B{r}"].font = BLACK_FORMULA

    _autosize(ws, 2, 22)
    return ws


def _add_soil_storage_sheet(wb: Workbook,
                             existing_compacted_storage_in: float, proposed_compacted_storage_in: float,
                             rainfall_5yr_in: float):
    ws = wb.create_sheet("Soil Storage & Runoff")
    ws["A1"] = "Soil Storage / Runoff Volume (SCS storage method)"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "R = (P - 0.2S)^2 / (P + 0.8S)     V = Area * R / 12"
    ws["A2"].font = Font(italic=True, size=9)

    _write_header_row(ws, 4, ["", "Existing", "Proposed"])

    ws["A5"] = "Rainfall Depth, P (in)"
    ws["B5"] = rainfall_5yr_in
    ws["C5"] = rainfall_5yr_in
    ws["B5"].font = BLUE_INPUT
    ws["C5"].font = BLUE_INPUT

    ws["A6"] = "Compacted Soil Storage (in)"
    ws["B6"] = existing_compacted_storage_in
    ws["C6"] = proposed_compacted_storage_in
    ws["B6"].font = BLUE_INPUT
    ws["C6"].font = BLUE_INPUT

    ws["A7"] = "Percent Pervious"
    ws["B7"] = "='Site Areas'!B7"
    ws["C7"] = "='Site Areas'!B13"
    ws["B7"].number_format = ws["C7"].number_format = "0.0%"

    ws["A8"] = "Effective Storage, S (in)"
    ws["B8"] = "=B7*B6"
    ws["C8"] = "=C7*C6"

    ws["A9"] = "Runoff, R (in)  [=0 if P<=0.2*S]"
    ws["B9"] = '=IF(B5<=0.2*B8,0,(B5-0.2*B8)^2/(B5+0.8*B8))'
    ws["C9"] = '=IF(C5<=0.2*C8,0,(C5-0.2*C8)^2/(C5+0.8*C8))'

    ws["A10"] = "Site Area (SF)"
    ws["B10"] = "='Site Areas'!B4"
    ws["C10"] = "='Site Areas'!B10"

    ws["A11"] = "Runoff Volume (CF)"
    ws["B11"] = "=B10*B9/12"
    ws["C11"] = "=C10*C9/12"

    ws["A13"] = "Net Increase in Runoff Volume (CF), Proposed - Existing"
    ws["A13"].font = BOLD
    ws["B13"] = "=C11-B11"
    ws["B13"].font = BOLD

    _autosize(ws, 3, 30)
    return ws


def _add_stage_storage_sheet(wb: Workbook, existing_basin: Optional[Basin], proposed_basin: Optional[Basin]):
    ws = wb.create_sheet("Stage-Storage")
    ws["A1"] = "Stage-Storage Curves (from routing model -- not independently editable)"
    ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = "Source: engine stage-storage curve for each basin; edit the basin model, not this sheet, to change these."
    ws["A2"].font = Font(italic=True, size=9)

    _write_header_row(ws, 4, ["Stage (ft NAVD)", "Existing Storage (ac-ft)", "Proposed Storage (ac-ft)"])
    ex_points = {round(s, 2): v for s, v in existing_basin.stage_storage.points} if existing_basin else {}
    pr_points = {round(s, 2): v for s, v in proposed_basin.stage_storage.points} if proposed_basin else {}
    all_stages = sorted(set(ex_points) | set(pr_points))
    for i, stage in enumerate(all_stages):
        r = 5 + i
        ws.cell(row=r, column=1, value=stage)
        ws.cell(row=r, column=2, value=ex_points.get(stage))
        ws.cell(row=r, column=3, value=pr_points.get(stage))
        for c in (1, 2, 3):
            ws.cell(row=r, column=c).font = BLUE_INPUT

    _autosize(ws, 3, 22)
    return ws


def _add_exfiltration_sheet(wb: Workbook, trench: ExfiltrationTrench, required_wq_volume_cuft: float):
    ws = wb.create_sheet("Exfiltration Trench")
    ws["A1"] = f"Exfiltration Trench Sizing -- {trench.name}"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "L1 = FS*%WQ*Vwq / [K(H2*W + 2*Heff*Du - Du^2 + 2*Heff*Ds) + 0.000139*W*Du]"
    ws["A3"] = "L2 = FS*%WQ*Vwq / [K(2*Heff*Du - Du^2 + 2*Heff*Ds) + 0.000139*W*Du]   (conservative, used when Ds>Du or W>2(Du+Ds))"
    ws["A2"].font = ws["A3"].font = Font(italic=True, size=9)

    inputs = [
        ("K (hydraulic conductivity, cfs/ft^2-ft)", trench.hydraulic_conductivity_k),
        ("FS (factor of safety)", trench.factor_of_safety),
        ("%WQ (fraction of WQ volume required)", trench.percent_wq_required),
        ("H2 (ft)", trench.H2),
        ("Heff (ft)", trench.Heff),
        ("Du (ft)", trench.Du),
        ("Ds (ft)", trench.Ds),
        ("W, trench width (ft)", trench.trench_width_ft),
        ("Vwq, required WQ volume (ac-in)", required_wq_volume_cuft / 3630.0),
        ("Provided trench length (LF)", trench.actual_trench_length_ft),
        ("Trench height, H (ft)", trench.trench_height_ft),
        ("Pipe diameter (in, 0 = none)", trench.pipe_diameter_in or 0.0),
    ]
    row = 5
    for label, value in inputs:
        ws.cell(row=row, column=1, value=label)
        cell = ws.cell(row=row, column=2, value=value)
        cell.font = BLUE_INPUT
        row += 1

    K, FS, PCTWQ, H2, HEFF, DU, DS, W, VWQ, PROVIDED, HGT, PIPEDIA = (f"B{5+i}" for i in range(12))

    r = row + 1
    ws.cell(row=r, column=1, value="Denominator (L1, standard)")
    ws.cell(row=r, column=2, value=f"={K}*({H2}*{W}+2*{HEFF}*{DU}-{DU}^2+2*{HEFF}*{DS})+0.000139*{W}*{DU}")
    denom1_row = r
    r += 1
    ws.cell(row=r, column=1, value="Denominator (L2, conservative)")
    ws.cell(row=r, column=2, value=f"={K}*(2*{HEFF}*{DU}-{DU}^2+2*{HEFF}*{DS})+0.000139*{W}*{DU}")
    denom2_row = r
    r += 2
    ws.cell(row=r, column=1, value="Required Length, L1 (LF)").font = BOLD
    ws.cell(row=r, column=2, value=f"={FS}*{PCTWQ}*{VWQ}/B{denom1_row}").font = BOLD
    l1_row = r
    r += 1
    ws.cell(row=r, column=1, value="Required Length, L2 (LF)").font = BOLD
    ws.cell(row=r, column=2, value=f"={FS}*{PCTWQ}*{VWQ}/B{denom2_row}").font = BOLD
    l2_row = r
    r += 1
    ws.cell(row=r, column=1, value="Governing Required Length (LF)").font = BOLD
    ws.cell(row=r, column=2, value=f"=IF({DS}>{DU},B{l2_row},IF({W}>2*({DU}+{DS}),B{l2_row},B{l1_row}))").font = BOLD
    gov_row = r
    r += 1
    ws.cell(row=r, column=1, value="STATUS").font = BOLD
    ws.cell(row=r, column=2, value=f'=IF({PROVIDED}>=B{gov_row},"PASS","FAIL")').font = BOLD

    r += 2
    ws.cell(row=r, column=1, value="Rock Volume (construction quantity -- not part of sizing above)").font = Font(italic=True, size=9)
    r += 1
    ws.cell(row=r, column=1, value="Pipe cross-section area (SF)")
    ws.cell(row=r, column=2, value=f"=PI()*({PIPEDIA}/12/2)^2")
    pipe_area_row = r
    r += 1
    ws.cell(row=r, column=1, value="Gross trench volume, W*H*L (CF)")
    ws.cell(row=r, column=2, value=f"={W}*{HGT}*{PROVIDED}")
    gross_row = r
    r += 1
    ws.cell(row=r, column=1, value="Rock Volume (CF)").font = BOLD
    ws.cell(row=r, column=2, value=f"=MAX(B{gross_row}-B{pipe_area_row}*{PROVIDED},0)").font = BOLD
    rock_cf_row = r
    r += 1
    ws.cell(row=r, column=1, value="Rock Volume (CY)").font = BOLD
    ws.cell(row=r, column=2, value=f"=B{rock_cf_row}/27").font = BOLD

    _autosize(ws, 2, 42)
    return ws


def export_drainage_calc_workbook(
    output_path: str,
    project_name: str,
    existing_basin: Optional[Basin],
    proposed_basin: Optional[Basin],
    existing_site_sqft: float,
    existing_pervious_sqft: float,
    proposed_site_sqft: float,
    proposed_pervious_sqft: float,
    existing_compacted_soil_storage_in: float,
    proposed_compacted_soil_storage_in: float,
    rainfall_5yr_1hr_in: float,
    exfiltration_trench: Optional[ExfiltrationTrench] = None,
    required_wq_volume_cuft: float = 0.0,
) -> str:
    wb = Workbook()
    wb.remove(wb.active)  # drop the default blank sheet

    _add_site_area_sheet(wb, project_name, existing_site_sqft, existing_pervious_sqft,
                          proposed_site_sqft, proposed_pervious_sqft)
    _add_soil_storage_sheet(wb, existing_compacted_soil_storage_in, proposed_compacted_soil_storage_in,
                             rainfall_5yr_1hr_in)
    _add_stage_storage_sheet(wb, existing_basin, proposed_basin)
    if exfiltration_trench is not None:
        _add_exfiltration_sheet(wb, exfiltration_trench, required_wq_volume_cuft)

    wb.save(output_path)
    return output_path
