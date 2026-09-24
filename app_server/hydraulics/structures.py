"""
hydraulics/structures.py

Every structure has a DestinationClassification that is completely
separate from its numerical implementation (Section 3.2-3.3). This is
the single most important rule in the whole spec: an onsite drainage
well must stay active and enabled during a zero-offsite-discharge
simulation, and its flow must never be counted as offsite discharge,
regardless of how the solver represents its receiving node internally.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Tuple
import math


class DestinationClassification(Enum):
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    ONSITE_DISPOSAL = "ONSITE_DISPOSAL"
    OFFSITE_DISCHARGE = "OFFSITE_DISCHARGE"


class Structure:
    """Base interface every hydraulic structure must implement."""

    name: str
    destination: DestinationClassification
    enabled: bool = True

    def discharge(self, headwater_stage_ft: float, tailwater_stage_ft: float) -> float:
        """Return discharge in cfs (>= 0) for the given stages. Structures
        that are disabled (e.g. OFFSITE_DISCHARGE structures during a
        zero-offsite scenario) must return 0.0 without altering their
        underlying rating -- disabling is a scenario-level toggle, never
        a permanent mutation of the structure's physical parameters."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Drainage well (Sections 26-28)
# ---------------------------------------------------------------------------

@dataclass
class DrainageWell(Structure):
    name: str
    capacity_gpm: float
    turn_on_stage: float
    turn_off_stage: float
    destination: DestinationClassification = DestinationClassification.ONSITE_DISPOSAL
    enabled: bool = True
    capacity_curve: Optional[List[Tuple[float, float]]] = None  # (stage, gpm) optional

    def __post_init__(self):
        self._is_on = False
        if self.turn_off_stage > self.turn_on_stage:
            raise ValueError(
                f"Well '{self.name}': turn-off elevation ({self.turn_off_stage}) is "
                f"above turn-on elevation ({self.turn_on_stage}). This is only valid "
                "if intentionally configured for reverse logic -- verify before use."
            )

    def _rated_gpm(self, stage_ft: float) -> float:
        if not self.capacity_curve:
            return self.capacity_gpm
        pts = sorted(self.capacity_curve, key=lambda p: p[0])
        if stage_ft <= pts[0][0]:
            return pts[0][1]
        if stage_ft >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            s1, c1 = pts[i - 1]
            s2, c2 = pts[i]
            if s1 <= stage_ft <= s2:
                frac = (stage_ft - s1) / (s2 - s1) if s2 != s1 else 0.0
                return c1 + frac * (c2 - c1)
        return pts[-1][1]

    def discharge(self, headwater_stage_ft: float, tailwater_stage_ft: float = 0.0) -> float:
        """Pure read of the CURRENT on/off memory -- must NOT mutate state.
        The routing solver calls this many times per time step while
        searching for the converged stage (bisection trial evaluations),
        so toggling hysteresis here would make the residual function
        depend on call order instead of on stage alone, and break the
        solver's bracketing assumption. On/off transitions are decided
        exclusively by update_hysteresis(), once per accepted time step."""
        if not self.enabled or not self._is_on:
            return 0.0
        from core.units import gpm_to_cfs
        return gpm_to_cfs(self._rated_gpm(headwater_stage_ft))

    def update_hysteresis(self, headwater_stage_ft: float) -> None:
        """Commit an on/off transition using an ACCEPTED stage (the
        converged result of a time step, or the initial stage). Call this
        once per time step from the routing loop -- never from inside a
        trial/residual evaluation."""
        if not self._is_on and headwater_stage_ft >= self.turn_on_stage:
            self._is_on = True
        elif self._is_on and headwater_stage_ft <= self.turn_off_stage:
            self._is_on = False

    def would_transition(self, headwater_stage_ft: float) -> bool:
        """True if calling update_hysteresis(headwater_stage_ft) right now
        would actually flip on/off memory. Read-only -- does not mutate
        state. Lets a routing loop detect "this step's solved stage implies
        a transition happened somewhere inside it" so it can re-solve that
        step at finer resolution instead of crediting/withholding this
        well's whole-step discharge based on a transition that (per the
        physics) only really happened partway through."""
        return (not self._is_on and headwater_stage_ft >= self.turn_on_stage) or (
            self._is_on and headwater_stage_ft <= self.turn_off_stage
        )

    def reset_state(self):
        """Call before each new scenario run -- hysteresis state must not
        leak between independent simulations."""
        self._is_on = False


# ---------------------------------------------------------------------------
# Orifice (Section 30)
# ---------------------------------------------------------------------------

G = 32.2  # ft/s^2

@dataclass
class Orifice(Structure):
    name: str
    invert_elevation_ft: float
    area_sqft: float
    discharge_coefficient: float = 0.6
    destination: DestinationClassification = DestinationClassification.OFFSITE_DISCHARGE
    enabled: bool = True

    def discharge(self, headwater_stage_ft: float, tailwater_stage_ft: float = 0.0) -> float:
        if not self.enabled:
            return 0.0
        # Effective head accounts for submergence: use headwater minus the
        # greater of invert or tailwater as a simple submerged-orifice check.
        effective_downstream = max(self.invert_elevation_ft, tailwater_stage_ft)
        h = headwater_stage_ft - effective_downstream
        if h <= 0:
            return 0.0
        return self.discharge_coefficient * self.area_sqft * math.sqrt(2 * G * h)


# ---------------------------------------------------------------------------
# Weirs (Section 31)
# ---------------------------------------------------------------------------

@dataclass
class RectangularWeir(Structure):
    name: str
    crest_elevation_ft: float
    length_ft: float
    discharge_coefficient: float = 3.1  # broad-crested default, ft^0.5/s
    destination: DestinationClassification = DestinationClassification.OFFSITE_DISCHARGE
    enabled: bool = True

    def discharge(self, headwater_stage_ft: float, tailwater_stage_ft: float = 0.0) -> float:
        if not self.enabled:
            return 0.0
        h = headwater_stage_ft - self.crest_elevation_ft
        if h <= 0:
            return 0.0
        return self.discharge_coefficient * self.length_ft * h ** 1.5


@dataclass
class VNotchWeir(Structure):
    name: str
    crest_elevation_ft: float
    notch_angle_degrees: float = 90.0
    discharge_coefficient: float = 2.5
    destination: DestinationClassification = DestinationClassification.OFFSITE_DISCHARGE
    enabled: bool = True

    def discharge(self, headwater_stage_ft: float, tailwater_stage_ft: float = 0.0) -> float:
        if not self.enabled:
            return 0.0
        h = headwater_stage_ft - self.crest_elevation_ft
        if h <= 0:
            return 0.0
        theta_rad = math.radians(self.notch_angle_degrees)
        return self.discharge_coefficient * math.tan(theta_rad / 2.0) * h ** 2.5


# ---------------------------------------------------------------------------
# Pump (Section 29) -- constant capacity or stage-capacity curve, same
# on/off hysteresis pattern as a drainage well but discharges to an
# explicit downstream node rather than relying purely on a passive rating.
# ---------------------------------------------------------------------------

@dataclass
class Pump(Structure):
    name: str
    capacity_cfs: float
    turn_on_stage: float
    turn_off_stage: float
    destination: DestinationClassification = DestinationClassification.OFFSITE_DISCHARGE
    enabled: bool = True
    capacity_curve: Optional[List[Tuple[float, float]]] = None  # (stage, cfs)

    def __post_init__(self):
        self._is_on = False
        if self.turn_off_stage > self.turn_on_stage:
            raise ValueError(
                f"Pump '{self.name}': turn-off elevation is above turn-on "
                "elevation -- verify this is intentional before use."
            )

    def _rated_cfs(self, stage_ft: float) -> float:
        if not self.capacity_curve:
            return self.capacity_cfs
        pts = sorted(self.capacity_curve, key=lambda p: p[0])
        if stage_ft <= pts[0][0]:
            return pts[0][1]
        if stage_ft >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            s1, c1 = pts[i - 1]
            s2, c2 = pts[i]
            if s1 <= stage_ft <= s2:
                frac = (stage_ft - s1) / (s2 - s1) if s2 != s1 else 0.0
                return c1 + frac * (c2 - c1)
        return pts[-1][1]

    def discharge(self, headwater_stage_ft: float, tailwater_stage_ft: float = 0.0) -> float:
        """Pure read of the CURRENT on/off memory -- see DrainageWell.discharge
        for why this must not mutate state on a per-call basis."""
        if not self.enabled or not self._is_on:
            return 0.0
        return self._rated_cfs(headwater_stage_ft)

    def update_hysteresis(self, headwater_stage_ft: float) -> None:
        """Commit an on/off transition using an ACCEPTED stage. Call once
        per time step from the routing loop -- see DrainageWell."""
        if not self._is_on and headwater_stage_ft >= self.turn_on_stage:
            self._is_on = True
        elif self._is_on and headwater_stage_ft <= self.turn_off_stage:
            self._is_on = False

    def would_transition(self, headwater_stage_ft: float) -> bool:
        """Read-only check -- see DrainageWell.would_transition."""
        return (not self._is_on and headwater_stage_ft >= self.turn_on_stage) or (
            self._is_on and headwater_stage_ft <= self.turn_off_stage
        )

    def reset_state(self):
        self._is_on = False


# ---------------------------------------------------------------------------
# Time-varying tailwater (Section 32)
# ---------------------------------------------------------------------------

@dataclass
class TailwaterSeries:
    """A receiving-body stage hydrograph. Wrap an offsite structure's
    discharge() calls with this when the receiving canal/basin stage
    changes over the simulation instead of staying constant. Onsite
    disposal features (drainage wells, exfiltration) should NOT be
    forced to use this just because Cascade historically modeled every
    outlet against a receiving-body node (Section 32, last line)."""
    time_hours: List[float]
    stage_ft: List[float]

    def __post_init__(self):
        if len(self.time_hours) != len(self.stage_ft):
            raise ValueError("TailwaterSeries time/stage length mismatch.")

    def stage_at(self, t_hours: float) -> float:
        times, stages = self.time_hours, self.stage_ft
        if t_hours <= times[0]:
            return stages[0]
        if t_hours >= times[-1]:
            return stages[-1]
        for i in range(1, len(times)):
            t1, s1 = times[i - 1], stages[i - 1]
            t2, s2 = times[i], stages[i]
            if t1 <= t_hours <= t2:
                frac = (t_hours - t1) / (t2 - t1) if t2 != t1 else 0.0
                return s1 + frac * (s2 - s1)
        return stages[-1]
