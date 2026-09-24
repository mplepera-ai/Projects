"""
reports/trench_section.py

Renders the firm's standard "TYP. EXFILTRATION TRENCH SECTION" detail
(the one drawn by hand on the plans, N.T.S.) driven by a real
ExfiltrationTrench's numbers instead of being redrawn by hand each
time. The layout -- pavement line, rock-filled trench, perforated pipe,
water-table tick, dimension arrows, title block -- mirrors the firm's
usual detail; only the elevations/labels and the water-table line's
position change project to project.

Produced as a PNG (matplotlib) sized for either a standalone image or
embedding in the calculations PDF (reports/calc_report_pdf.py).
"""

from __future__ import annotations
from typing import Optional
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle
from matplotlib.lines import Line2D

from storage.exfiltration import ExfiltrationTrench

# Typical pavement section above the top of rock, used only when the
# caller doesn't supply an actual finish-grade/pavement elevation --
# this project doesn't currently collect one as a distinct input.
_DEFAULT_COVER_FT = 2.5


def draw_exfiltration_trench_section(
    trench: ExfiltrationTrench,
    output_path: str,
    pavement_elevation_ft: Optional[float] = None,
    title: str = "TYP. EXFILTRATION TRENCH SECTION",
) -> str:
    """Draws the trench cross-section to output_path (.png) and returns it.

    pavement_elevation_ft: finish-grade elevation shown as the top
    "PROP. ASPHALT" line. If not supplied, assumed to be
    trench_top_elevation_ft + _DEFAULT_COVER_FT (typical cover) and
    the drawing notes it as assumed.
    """
    assumed_pavement = pavement_elevation_ft is None
    if pavement_elevation_ft is None:
        pavement_elevation_ft = trench.trench_top_elevation_ft + _DEFAULT_COVER_FT

    top = trench.trench_top_elevation_ft
    bottom = trench.trench_bottom_elevation_ft
    width = trench.trench_width_ft
    pipe_d_ft = (trench.pipe_diameter_in / 12.0) if trench.pipe_diameter_in else (width * 0.35)
    pipe_invert = trench.pipe_invert_elevation_ft
    pipe_center_elev = pipe_invert + pipe_d_ft / 2.0
    wt = trench.design_water_table_ft
    ws = trench.design_water_surface_ft

    cx = 0.0  # trench horizontal centerline
    half_w = width / 2.0

    fig_h = max(6.5, (pavement_elevation_ft - bottom) * 0.7 + 3.0)
    fig, ax = plt.subplots(figsize=(7.5, fig_h), dpi=200)
    ax.set_aspect("equal")

    label_x = -half_w - 3.2   # left edge for elevation call-out labels
    dim_x = half_w + 2.2      # right-edge vertical dimension line

    # --- Rock-filled trench body -------------------------------------
    trench_rect = Rectangle((-half_w, bottom), width, top - bottom,
                             facecolor="none", edgecolor="black", linewidth=1.4, zorder=2)
    ax.add_patch(trench_rect)
    # hand-drawn-aggregate look: matplotlib's built-in circle hatch
    trench_rect.set_hatch("o")
    trench_rect.set_linewidth(1.4)

    # --- Perforated pipe ------------------------------------------------
    ax.add_patch(Circle((cx, pipe_center_elev), pipe_d_ft / 2.0,
                         facecolor="white", edgecolor="black", linewidth=1.3, zorder=4))

    pipe_label = f'{trench.pipe_diameter_in:.0f}" PERF.\nH.D.P.E. PIPE' if trench.pipe_diameter_in else "PERF. PIPE"
    ax.annotate(pipe_label, xy=(cx, pipe_center_elev), xytext=(half_w * 0.55, pavement_elevation_ft - 0.15),
                fontsize=8, ha="left", va="top",
                arrowprops=dict(arrowstyle="-", lw=0.8, color="black"))

    # --- Pavement line + callout ----------------------------------------
    pave_half = half_w + 1.6
    ax.plot([-pave_half, pave_half], [pavement_elevation_ft, pavement_elevation_ft],
            color="black", linewidth=1.3, zorder=3)
    ax.annotate("PROP. ASPHALT", xy=(-half_w * 0.35, pavement_elevation_ft), xytext=(-half_w * 0.15, pavement_elevation_ft + 1.0),
                fontsize=8, ha="left",
                arrowprops=dict(arrowstyle="->", lw=0.8, color="black"))
    # downward arrow into the trench (surcharge/runoff entry symbol)
    for fx in (-half_w * 0.15, half_w * 0.15):
        ax.annotate("", xy=(fx, top + 0.05), xytext=(fx, pavement_elevation_ft),
                    arrowprops=dict(arrowstyle="->", lw=1.0, color="black"))

    # --- Water table tick + triangle symbol ------------------------------
    ax.plot([-half_w, half_w * 0.15], [ws, ws], color="black", linewidth=0.9, linestyle="-", zorder=3)
    ax.plot([-half_w * 0.55, -half_w * 0.4, -half_w * 0.475], [ws, ws, ws - 0.28],
            color="black", linewidth=0.9, zorder=3)  # small triangle (WT symbol), apex down
    ax.fill([-half_w * 0.55, -half_w * 0.4, -half_w * 0.475], [ws, ws, ws - 0.28],
            color="black", zorder=3)
    ax.plot([-half_w, half_w * 0.15], [wt, wt], color="black", linewidth=0.9, linestyle="--", zorder=3)

    # --- Left-edge elevation call-outs -----------------------------------
    def elev_label(elev_ft: float, text: Optional[str] = None, dy: float = 0.0):
        ax.plot([label_x + 0.55, -half_w - 0.15], [elev_ft, elev_ft], color="black", linewidth=0.7, zorder=1)
        ax.text(label_x + 0.4, elev_ft + dy, text if text else f"{elev_ft:.2f}'",
                fontsize=9, ha="right", va="center")

    elev_label(pavement_elevation_ft, f"{pavement_elevation_ft:.2f}'" + ("  (assumed)" if assumed_pavement else ""))
    elev_label(top, f"{top:.2f}'")
    if abs(ws - wt) < 0.05:
        elev_label(ws, f"{ws:.2f}'  D.W.S. / D.W.T.")
    else:
        elev_label(ws, f"{ws:.2f}'  D.W.S.")
        elev_label(wt, f"{wt:.2f}'  D.W.T.", dy=0.0)
    elev_label(bottom, f"{bottom:.2f}'")

    # --- Cover dimension (pavement -> top of trench) ----------------------
    cover = pavement_elevation_ft - top
    cover_x = half_w + 0.55
    ax.annotate("", xy=(cover_x, top), xytext=(cover_x, pavement_elevation_ft),
                arrowprops=dict(arrowstyle="<->", lw=0.9, color="black"))
    ax.text(cover_x + 0.15, (top + pavement_elevation_ft) / 2.0, f"{cover:.2f}'",
            fontsize=8, ha="left", va="center", rotation=90)

    # --- Height dimension (top -> bottom of trench) ------------------------
    ax.annotate("", xy=(dim_x, bottom), xytext=(dim_x, top),
                arrowprops=dict(arrowstyle="<->", lw=1.0, color="black"))
    ax.text(dim_x + 0.2, (top + bottom) / 2.0, f"{top - bottom:.2f}'",
            fontsize=9, ha="left", va="center", rotation=90)

    # --- Width dimension (bottom of drawing) --------------------------------
    dim_y = bottom - 1.1
    ax.annotate("", xy=(half_w, dim_y), xytext=(-half_w, dim_y),
                arrowprops=dict(arrowstyle="<->", lw=1.0, color="black"))
    ax.text(0, dim_y - 0.35, f"{width:.1f}'", fontsize=9, ha="center", va="top")

    # --- Title block ----------------------------------------------------
    title_y = dim_y - 1.3
    ax.text(0, title_y, title, fontsize=11, ha="center", va="top",
            style="italic", weight="bold", family="serif")
    ax.plot([-2.3, 2.3], [title_y - 0.35, title_y - 0.35], color="black", linewidth=0.6)
    ax.text(0, title_y - 0.55, "N.T.S.", fontsize=8, ha="center", va="top", style="italic", family="serif")

    ax.set_xlim(label_x - 0.4, dim_x + 1.4)
    ax.set_ylim(title_y - 1.0, pavement_elevation_ft + 1.6)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path
