"""
project/serialization.py

Human-readable JSON project file format (Section 64). This is a
faithful, explicit serialization -- no pickling, no hidden binary
state -- so a project file can be inspected, diffed, and version
controlled like any other text asset (Section 65: the engineer must
be able to tell exactly which model produced a given report).

Only the data needed to fully reconstruct a Project is stored:
metadata, regulatory profile selection, basins (geometry + stage-
storage + structures), interbasin links, and scenario definitions.
Computed results are NEVER stored here -- they're always regenerated
by re-running the model (Section 63: changing an input invalidates
prior results; a stale cached result baked into the file would defeat
that entirely).
"""

from __future__ import annotations
import json
from dataclasses import asdict
from typing import Any, Dict

from core.interpolation import StageStorageCurve
from hydraulics.structures import (
    DrainageWell, Orifice, RectangularWeir, VNotchWeir, Pump,
    DestinationClassification, Structure,
)
from hydraulics.basin import Basin
from hydraulics.network import BasinNetwork, InterbasinLink
from regulatory.profiles import (
    RegulatoryProfile, RequiredEvent, broward_county_swm_profile, sfwmd_profile,
)
from project.model import Project, ProjectMetadata, Condition, Scenario

SCHEMA_VERSION = "1.0"

_STRUCTURE_CLASSES = {
    "DrainageWell": DrainageWell,
    "Orifice": Orifice,
    "RectangularWeir": RectangularWeir,
    "VNotchWeir": VNotchWeir,
    "Pump": Pump,
}


class SerializationError(Exception):
    pass


# ---- Structure <-> dict ---------------------------------------------------

def _structure_to_dict(s: Structure) -> Dict[str, Any]:
    d = dict(vars(s))
    d.pop("_is_on", None)  # runtime hysteresis state, never persisted
    d["destination"] = s.destination.value
    d["_type"] = type(s).__name__
    return d


def _structure_from_dict(d: Dict[str, Any]) -> Structure:
    d = dict(d)
    type_name = d.pop("_type")
    cls = _STRUCTURE_CLASSES.get(type_name)
    if cls is None:
        raise SerializationError(f"Unknown structure type '{type_name}'.")
    d["destination"] = DestinationClassification(d["destination"])
    if d.get("capacity_curve") is not None:
        d["capacity_curve"] = [tuple(p) for p in d["capacity_curve"]]
    return cls(**d)


# ---- Basin / Network <-> dict ----------------------------------------------

def _basin_to_dict(b: Basin) -> Dict[str, Any]:
    return {
        "basin_id": b.basin_id,
        "name": b.name,
        "area_acres": b.area_acres,
        "ground_storage_inches": b.ground_storage_inches,
        "time_of_concentration_hours": b.time_of_concentration_hours,
        "initial_stage_ft": b.initial_stage_ft,
        "stage_storage_points": [list(p) for p in b.stage_storage.points],
        "structures": [_structure_to_dict(s) for s in b.structures],
    }


def _basin_from_dict(d: Dict[str, Any]) -> Basin:
    curve = StageStorageCurve(points=[tuple(p) for p in d["stage_storage_points"]])
    structures = [_structure_from_dict(sd) for sd in d.get("structures", [])]
    return Basin(
        basin_id=d["basin_id"], name=d["name"], area_acres=d["area_acres"],
        ground_storage_inches=d["ground_storage_inches"],
        time_of_concentration_hours=d["time_of_concentration_hours"],
        initial_stage_ft=d["initial_stage_ft"], stage_storage=curve, structures=structures,
    )


def _network_to_dict(net: BasinNetwork) -> Dict[str, Any]:
    return {
        "basins": {bid: _basin_to_dict(b) for bid, b in net.basins.items()},
        "links": [
            {
                "link_id": link.link_id,
                "from_basin_id": link.from_basin_id,
                "to_basin_id": link.to_basin_id,
                "structure": _structure_to_dict(link.structure),
            }
            for link in net.links
        ],
    }


def _network_from_dict(d: Dict[str, Any]) -> BasinNetwork:
    basins = {bid: _basin_from_dict(bd) for bid, bd in d["basins"].items()}
    links = [
        InterbasinLink(
            link_id=ld["link_id"], from_basin_id=ld["from_basin_id"],
            to_basin_id=ld["to_basin_id"], structure=_structure_from_dict(ld["structure"]),
        )
        for ld in d.get("links", [])
    ]
    return BasinNetwork(basins=basins, links=links)


# ---- Regulatory profile <-> dict -------------------------------------------

_PROFILE_BUILDERS = {
    "Broward County SWM": broward_county_swm_profile,
    "SFWMD": sfwmd_profile,
}


def _profile_to_dict(p: RegulatoryProfile) -> Dict[str, Any]:
    return {
        "name": p.name,
        "version": p.version,
        "required_events": [asdict(e) for e in p.required_events],
    }


def _profile_from_dict(d: Dict[str, Any]) -> RegulatoryProfile:
    events = [RequiredEvent(**ed) for ed in d["required_events"]]
    return RegulatoryProfile(name=d["name"], version=d["version"], required_events=events)


# ---- Project <-> dict -------------------------------------------------------

def project_to_dict(project: Project) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": asdict(project.metadata),
        "regulatory_profile": _profile_to_dict(project.regulatory_profile),
        "conditions": {
            name: _network_to_dict(cond.network) for name, cond in project.conditions.items()
        },
        "scenarios": [asdict(s) for s in project.scenarios],
    }


def project_from_dict(d: Dict[str, Any]) -> Project:
    if d.get("schema_version") != SCHEMA_VERSION:
        raise SerializationError(
            f"Unsupported schema version '{d.get('schema_version')}' "
            f"(expected '{SCHEMA_VERSION}')."
        )
    metadata = ProjectMetadata(**d["metadata"])
    profile = _profile_from_dict(d["regulatory_profile"])
    project = Project(metadata=metadata, regulatory_profile=profile)
    for name, net_dict in d["conditions"].items():
        project.add_condition(Condition(name=name, network=_network_from_dict(net_dict)))
    project.scenarios = [Scenario(**sd) for sd in d.get("scenarios", [])]
    return project


def save_project(project: Project, path: str) -> None:
    with open(path, "w") as fh:
        json.dump(project_to_dict(project), fh, indent=2)


def load_project(path: str) -> Project:
    with open(path, "r") as fh:
        return project_from_dict(json.load(fh))
