"""
storage/exfiltration.py

SFWMD-style exfiltration trench calculator (Sections 19-24).

    L = (FS * Vwq) / [K*(H2*W + 2*H2*Du - Du^2 + 2*H2*Ds) + (1.39e-4)*W*Du]

All inputs are physical elevations (Section 20) -- Du, Ds, and H2 are
DERIVED, never entered directly, except under USER_DEFINED_H2 mode.
The exact resulting H2 is always displayed (Section 21: "Never hide
H2 in the calculation").

Units convention used throughout this module (consistent, not mixed):
    elevations, W, Du, Ds, H2, L      -> feet
    K                                  -> cfs / ft^2 per ft of head
    Vwq (required treatment volume)    -> cubic feet
Reverse conversions to acre-in / acre-ft are provided at the edges.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from core.units import ACRE_IN_TO_CUFT, CUFT_TO_ACRE_IN, CUFT_TO_ACRE_FT


class ExfiltrationError(Exception):
    pass


class H2Method(Enum):
    SFWMD_STANDARD = "SFWMD_STANDARD"
    BROWARD_POSITIVE_DISCHARGE = "BROWARD_POSITIVE_DISCHARGE"
    USER_DEFINED = "USER_DEFINED"


@dataclass
class ExfiltrationTrench:
    name: str

    control_elevation_ft: float
    design_water_table_ft: float
    trench_top_elevation_ft: float
    trench_bottom_elevation_ft: float
    pipe_invert_elevation_ft: float
    lowest_overflow_elevation_ft: float
    design_water_surface_ft: float

    trench_width_ft: float
    actual_trench_length_ft: float
    hydraulic_conductivity_k: float  # cfs/ft^2-ft head

    # Optional: the pipe (perforated/slotted underdrain) run through the
    # trench, for the ROCK VOLUME quantity below. This is purely a
    # construction/materials quantity -- it plays no part in the
    # exfiltration hydraulics above (H2/Du/Ds/K/W/required length are all
    # unaffected by pipe size) and is never used in required_length_ft or
    # provided_capacity_cuft.
    pipe_diameter_in: Optional[float] = None

    factor_of_safety: float = 2.0
    h2_method: H2Method = H2Method.SFWMD_STANDARD
    user_defined_h2_ft: Optional[float] = None
    # Effective head across the trench cross-section, distinct from H2
    # (the total available head at the receiving point). Real South
    # Florida practice (confirmed against a submitted EA3 report)
    # separates these -- Heff appears in the 2*Heff*Du and 2*Heff*Ds
    # terms while H2 only appears in the H2*W term. Defaults to H2 when
    # not given, which reduces to this module's original Section-19
    # formula, but a project may supply a distinct value.
    effective_head_ft: Optional[float] = None
    # Percentage of the water quality volume required to be treated
    # through this trench specifically (dry detention/retention credits
    # can reduce this below 100%, Section 25).
    percent_wq_required: float = 1.0

    def __post_init__(self):
        if self.trench_top_elevation_ft <= self.trench_bottom_elevation_ft:
            raise ExfiltrationError(
                f"Trench '{self.name}': top/bottom elevations reversed "
                f"(top={self.trench_top_elevation_ft}, bottom={self.trench_bottom_elevation_ft})."
            )
        if self.trench_width_ft <= 0:
            raise ExfiltrationError(f"Trench '{self.name}': width must be > 0.")
        if self.hydraulic_conductivity_k <= 0:
            raise ExfiltrationError(f"Trench '{self.name}': hydraulic conductivity K must be > 0.")
        if self.factor_of_safety < 2.0:
            raise ExfiltrationError(
                f"Trench '{self.name}': factor of safety {self.factor_of_safety} is below "
                "the regulatory minimum default of 2.0."
            )
        if self.h2_method == H2Method.USER_DEFINED and self.user_defined_h2_ft is None:
            raise ExfiltrationError(
                f"Trench '{self.name}': USER_DEFINED H2 method selected but no value given."
            )

    # ---- Derived geometry (Section 20) -----------------------------------

    @property
    def trench_height_ft(self) -> float:
        return self.trench_top_elevation_ft - self.trench_bottom_elevation_ft

    @property
    def Du(self) -> float:
        """Unsaturated trench depth: portion of the trench above the
        design water table."""
        top, bottom, dwt = (
            self.trench_top_elevation_ft,
            self.trench_bottom_elevation_ft,
            self.design_water_table_ft,
        )
        if dwt <= bottom:
            return top - bottom
        if dwt >= top:
            return 0.0
        return top - dwt

    @property
    def Ds(self) -> float:
        """Saturated trench depth: portion of the trench below the
        design water table."""
        return self.trench_height_ft - self.Du

    @property
    def H2(self) -> float:
        """Available hydraulic head driving exfiltration. Method depends
        on h2_method (Section 21) -- always fully computed and displayed,
        never hidden."""
        if self.h2_method == H2Method.USER_DEFINED:
            return self.user_defined_h2_ft

        if self.h2_method == H2Method.SFWMD_STANDARD:
            return self.design_water_surface_ft - self.design_water_table_ft

        if self.h2_method == H2Method.BROWARD_POSITIVE_DISCHARGE:
            # Broward's positive-discharge interpretation caps the driving
            # head at the lowest overflow/bleeder elevation rather than the
            # full design water surface, since excess head above the
            # overflow is relieved rather than driving exfiltration.
            governing_surface = min(self.design_water_surface_ft, self.lowest_overflow_elevation_ft)
            return governing_surface - self.design_water_table_ft

        raise ExfiltrationError(f"Unknown H2 method: {self.h2_method}")

    @property
    def Heff(self) -> float:
        """Effective head across the trench cross-section. Defaults to
        H2 if not explicitly given (see field docstring)."""
        return self.effective_head_ft if self.effective_head_ft is not None else self.H2

    # ---- Rock volume (construction quantity, not a hydraulic term) --------
    # Requested to help evaluate designs that achieve the required
    # exfiltration capacity while minimizing rock (#57 stone) fill volume,
    # since that's a material cost driver independent of what the
    # hydraulic sizing above requires.

    @property
    def pipe_area_sqft(self) -> float:
        """Cross-sectional area of the pipe run through the trench, or
        0.0 if no pipe diameter was given (e.g. a rock-only trench with
        no underdrain)."""
        if not self.pipe_diameter_in:
            return 0.0
        radius_ft = (self.pipe_diameter_in / 12.0) / 2.0
        return math.pi * radius_ft ** 2

    @property
    def rock_volume_cuft(self) -> float:
        """Gross trench volume (W x H x provided length) minus the pipe's
        own volume -- the volume of rock backfill actually needed to
        construct the trench around the pipe. This is a raw material
        quantity (no stone porosity applied): if you need the USABLE void
        storage the rock+pipe provide instead, see
        ExfiltrationStaticStorage.trench_void_storage_cuft, which applies
        porosity to this same gross-minus-pipe volume."""
        gross_volume = self.trench_width_ft * self.trench_height_ft * self.actual_trench_length_ft
        pipe_volume = self.pipe_area_sqft * self.actual_trench_length_ft
        return max(gross_volume - pipe_volume, 0.0)

    @property
    def rock_volume_cy(self) -> float:
        """rock_volume_cuft in cubic yards (1 CY = 27 CF) -- the unit
        rock/stone is typically ordered and paid for in."""
        return self.rock_volume_cuft / 27.0

    # ---- Validity checks (Section 22) -------------------------------------

    def requires_conservative_equation(self) -> bool:
        """Section 22's trigger for switching from the standard (L1) to
        the conservative (L2) trench equation."""
        du, ds = self.Du, self.Ds
        return ds > du or self.trench_width_ft > 2.0 * (du + ds)

    def validity_warnings(self) -> List[str]:
        warnings = []
        du, ds, h2 = self.Du, self.Ds, self.H2
        if du < 0:
            warnings.append("CRITICAL: Du < 0.")
        if ds < 0:
            warnings.append("CRITICAL: Ds < 0.")
        if h2 < 0:
            warnings.append("CRITICAL: H2 < 0 -- no available driving head.")
        if self.trench_width_ft <= 0:
            warnings.append("CRITICAL: trench width must be > 0.")
        if self.hydraulic_conductivity_k <= 0:
            warnings.append("CRITICAL: hydraulic conductivity K must be > 0.")
        if self.factor_of_safety < 2.0:
            warnings.append("CRITICAL: factor of safety below regulatory minimum (2.0).")
        if ds > du:
            warnings.append(
                "WARNING: Trench geometry may require the conservative "
                "exfiltration equation (Ds > Du)."
            )
        if self.trench_width_ft > 2.0 * (du + ds):
            warnings.append(
                "WARNING: Trench geometry may require the conservative "
                "exfiltration equation (W > 2*(Du+Ds))."
            )
        return warnings

    # ---- Core calculation (Section 19, plus the standard/conservative
    # split confirmed against a real submitted South Florida report) ---

    def _denominator_standard(self) -> float:
        """L1: the standard equation, including the H2*W term."""
        K, W, Du, Ds, H2, Heff = (
            self.hydraulic_conductivity_k, self.trench_width_ft,
            self.Du, self.Ds, self.H2, self.Heff,
        )
        return K * (H2 * W + 2 * Heff * Du - Du ** 2 + 2 * Heff * Ds) + (1.39e-4) * W * Du

    def _denominator_conservative(self) -> float:
        """L2: the conservative equation, dropping the H2*W term
        entirely -- used when trench geometry makes the standard
        equation non-conservative (Ds > Du, or W > 2*(Du+Ds))."""
        K, W, Du, Ds, Heff = (
            self.hydraulic_conductivity_k, self.trench_width_ft, self.Du, self.Ds, self.Heff,
        )
        return K * (2 * Heff * Du - Du ** 2 + 2 * Heff * Ds) + (1.39e-4) * W * Du

    def _denominator(self, force_conservative: Optional[bool] = None) -> float:
        use_conservative = (
            self.requires_conservative_equation() if force_conservative is None else force_conservative
        )
        return self._denominator_conservative() if use_conservative else self._denominator_standard()

    def required_length_ft(
        self, required_treatment_volume_cuft: float, design_period_hours: float = 1.0,
        force_conservative: Optional[bool] = None,
    ) -> float:
        """
        Section 19's own parameter description calls Vwq a "required
        treatment volume" but says it must be "expressed in applicable
        acre-inch/hour units" -- i.e. it is actually a FLOW RATE, not a
        volume, despite the name. This module takes the volume in cubic
        feet (the physically meaningful quantity engineers compute from
        water-quality criteria) and converts it to the rate the equation
        actually needs by spreading it over design_period_hours (SFWMD's
        conventional exfiltration design basis is 1 hour -- adjust if a
        project's accepted methodology specifies otherwise).

        Skipping this conversion and feeding a raw cubic-foot volume
        into the equation directly overstates required length by roughly
        3600x (one design run showed 620,000 LF instead of ~200 LF) --
        this is the single easiest place to silently get this calculator
        wrong, so the conversion is enforced here rather than left to
        the caller.

        Automatically switches to the conservative (L2) equation when
        trench geometry requires it (Section 22), unless force_conservative
        is explicitly set.
        """
        denom = self._denominator(force_conservative)
        if denom <= 0:
            raise ExfiltrationError(
                f"Trench '{self.name}': non-positive denominator in exfiltration "
                "equation -- check Du/Ds/H2/K/W inputs before sizing."
            )
        vwq_rate_cfs = (required_treatment_volume_cuft * self.percent_wq_required) / (design_period_hours * 3600.0)
        return (self.factor_of_safety * vwq_rate_cfs) / denom

    def exfiltration_capacity_cfs(self, force_conservative: Optional[bool] = None) -> float:
        """
        Steady-state hydraulic exfiltration RATE this trench (at its
        actual_trench_length_ft) can pass, in cfs -- the same Darcy-flow
        quantity provided_capacity_cuft() converts into a design-period
        volume, exposed directly as a rate instead.

        This is a function of trench geometry, K, and available head
        ONLY -- it has nothing to do with the property's rainfall or
        water-quality treatment volume (those only determine what
        LENGTH of trench gets built via required_length_ft()). Once a
        trench of a given length exists, its exfiltration rate capacity
        can be checked against ANY steady inflow -- a continuous
        dewatering discharge included -- not just the storm-driven WQ
        volume it may have originally been sized for.
        """
        denom = self._denominator(force_conservative)
        return (denom * self.actual_trench_length_ft) / self.factor_of_safety

    def exfiltration_capacity_gpm(self, force_conservative: Optional[bool] = None) -> float:
        return self.exfiltration_capacity_cfs(force_conservative) * 448.831169

    def provided_capacity_cuft(
        self, design_period_hours: float = 1.0, force_conservative: Optional[bool] = None,
    ) -> float:
        """Reverse calculation (Section 23): treatment volume capacity of
        the trench as actually provided (actual_trench_length_ft), using
        the same rate<->volume convention as required_length_ft."""
        rate_cfs = self.exfiltration_capacity_cfs(force_conservative)
        volume_cuft = rate_cfs * design_period_hours * 3600.0
        return volume_cuft / self.percent_wq_required if self.percent_wq_required else volume_cuft

    # ---- Width/length auto-suggestion (future-improvement #4) -------------
    # Requested alongside rock volume: given a fixed set of "everything
    # else" (elevations, water table, K, FS, ...), try a range of trench
    # widths and report the required length and rock volume at each --
    # so a width/length combination can be picked that meets the
    # required treatment volume with the least rock (#57 stone) fill,
    # which is a real cost driver independent of the hydraulic sizing.

    def suggest_trench_widths(
        self,
        required_treatment_volume_cuft: float,
        width_options_ft: Optional[List[float]] = None,
        available_length_ft: Optional[float] = None,
        design_period_hours: float = 1.0,
    ) -> List[dict]:
        """
        Does not mutate this trench. Returns one row per width tried:
        {width_ft, required_length_ft, rock_volume_cuft, rock_volume_cy,
        fits_available_length, recommended}. "recommended" marks the
        lowest-rock-volume option that still fits available_length_ft
        (or, if none fit, the lowest-rock-volume option overall, so the
        result is never silently empty just because nothing fit).
        """
        if width_options_ft is None:
            width_options_ft = [w / 2.0 for w in range(4, 25)]  # 2.0 .. 12.0 ft by 0.5 ft

        from dataclasses import replace

        rows: List[dict] = []
        for w in width_options_ft:
            if w is None or w <= 0:
                continue
            trial = replace(self, trench_width_ft=float(w))
            try:
                required_length = trial.required_length_ft(
                    required_treatment_volume_cuft, design_period_hours,
                )
            except ExfiltrationError:
                # This width makes the denominator non-positive (e.g. no
                # available head) -- skip it rather than fail the whole
                # suggestion run.
                continue
            rock_trial = replace(trial, actual_trench_length_ft=required_length)
            fits = available_length_ft is None or required_length <= available_length_ft
            rows.append({
                "width_ft": float(w),
                "required_length_ft": required_length,
                "rock_volume_cuft": rock_trial.rock_volume_cuft,
                "rock_volume_cy": rock_trial.rock_volume_cy,
                "fits_available_length": fits,
            })

        feasible = [r for r in rows if r["fits_available_length"]]
        pool = feasible if feasible else rows
        best = min(pool, key=lambda r: r["rock_volume_cy"]) if pool else None
        for r in rows:
            r["recommended"] = best is not None and r is best
        return rows

    def report(self, required_treatment_volume_cuft: float, design_period_hours: float = 1.0) -> dict:
        used_conservative = self.requires_conservative_equation()
        required_length = self.required_length_ft(required_treatment_volume_cuft, design_period_hours)
        provided_capacity = self.provided_capacity_cuft(design_period_hours)
        status = "PASS" if self.actual_trench_length_ft >= required_length else "FAIL"

        return {
            "name": self.name,
            "required_treatment_volume_ac_in": required_treatment_volume_cuft * CUFT_TO_ACRE_IN,
            "factor_of_safety": self.factor_of_safety,
            "percent_wq_required": self.percent_wq_required,
            "K": self.hydraulic_conductivity_k,
            "h2_method": self.h2_method.value,
            "H2_ft": self.H2,
            "Heff_ft": self.Heff,
            "Du_ft": self.Du,
            "Ds_ft": self.Ds,
            "width_ft": self.trench_width_ft,
            "height_ft": self.trench_height_ft,
            "equation_used": "L2 (conservative)" if used_conservative else "L1 (standard)",
            "required_length_ft": required_length,
            "provided_length_ft": self.actual_trench_length_ft,
            "provided_capacity_ac_in": provided_capacity * CUFT_TO_ACRE_IN,
            "provided_capacity_ac_ft": provided_capacity * CUFT_TO_ACRE_FT,
            "status": status,
            "warnings": self.validity_warnings(),
            "pipe_diameter_in": self.pipe_diameter_in,
            "rock_volume_cuft": self.rock_volume_cuft,
            "rock_volume_cy": self.rock_volume_cy,
        }


# ---------------------------------------------------------------------------
# Static void storage (Section 24) -- separate from hydraulic capacity above.
# ---------------------------------------------------------------------------

@dataclass
class ExfiltrationStaticStorage:
    """Physical void storage available in the trench/pipe system, computed
    independently of hydraulic exfiltration capacity (Section 24: 'Do not
    automatically assume that hydraulic exfiltration capacity and static
    storage are the same thing.')."""

    name: str
    trench_width_ft: float
    trench_height_ft: float
    trench_length_ft: float
    stone_porosity: float = 0.40  # fraction, e.g. 0.40 for #57 stone

    pipe_diameter_in: float = 0.0
    pipe_length_ft: float = 0.0

    def __post_init__(self):
        if not (0.0 < self.stone_porosity <= 1.0):
            raise ExfiltrationError(f"Trench '{self.name}': stone porosity must be in (0, 1].")

    @property
    def trench_void_storage_cuft(self) -> float:
        gross_volume = self.trench_width_ft * self.trench_height_ft * self.trench_length_ft
        pipe_volume = self.pipe_void_volume_cuft
        stone_volume = max(gross_volume - pipe_volume, 0.0)
        return stone_volume * self.stone_porosity + pipe_volume

    @property
    def pipe_void_volume_cuft(self) -> float:
        if self.pipe_diameter_in <= 0 or self.pipe_length_ft <= 0:
            return 0.0
        radius_ft = (self.pipe_diameter_in / 12.0) / 2.0
        import math
        return math.pi * radius_ft ** 2 * self.pipe_length_ft

    def summary(self) -> dict:
        return {
            "name": self.name,
            "pipe_void_storage_cuft": self.pipe_void_volume_cuft,
            "total_static_storage_cuft": self.trench_void_storage_cuft,
            "total_static_storage_acre_ft": self.trench_void_storage_cuft * CUFT_TO_ACRE_FT,
        }
