import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.adapter import run_project, build_project_from_app_json


def _basin_json(**overrides):
    d = dict(
        name="Site", areaAcres=12.0, groundStorageIn=3.0, tcHours=0.3, initialStageFt=5.0,
        stagePoints=[[5.0, 0.0], [6.0, 2.0], [7.0, 6.0], [8.0, 11.0], [9.0, 18.0]],
        structures=[
            {"kind": "weir", "name": "Outfall", "crestElevationFt": 6.9, "lengthFt": 6.0, "c": 3.1,
             "destination": "OFFSITE_DISCHARGE", "enabled": True},
        ],
    )
    d.update(overrides)
    return d


def _project_json(existing_overrides=None, proposed_overrides=None):
    return {
        "metadata": {"name": "Adapter Test Project"},
        "storms": {
            "y5": {"dur": 24, "depth": 3.0}, "y25": {"dur": 72, "depth": 9.0}, "y100": {"dur": 72, "depth": 14.0},
        },
        "basins": {
            "existing": {"basins": {"SITE": _basin_json(**(existing_overrides or {}))}, "links": []},
            "proposed": {"basins": {"SITE": _basin_json(**(proposed_overrides or {}))}, "links": []},
        },
    }


class TestAdapterBermFreeboard(unittest.TestCase):
    def test_basin_without_berm_elevation_has_none_freeboard(self):
        project = build_project_from_app_json(_project_json())
        basin = project.conditions["proposed"].network.basins["SITE"]
        self.assertIsNone(basin.berm_elevation_ft)

    def test_basin_with_berm_elevation_is_parsed(self):
        project = build_project_from_app_json(_project_json(proposed_overrides={"bermElevationFt": 9.5}))
        basin = project.conditions["proposed"].network.basins["SITE"]
        self.assertEqual(basin.berm_elevation_ft, 9.5)

    def test_run_project_reports_freeboard_and_berm_in_json(self):
        payload = run_project(_project_json(proposed_overrides={"bermElevationFt": 9.5}))
        proposed_basins = [
            b for s in payload["scenarios"] if s["condition"] == "proposed" for b in s["basins"]
        ]
        self.assertTrue(len(proposed_basins) > 0)
        for b in proposed_basins:
            self.assertEqual(b["bermElevationFt"], 9.5)
            self.assertAlmostEqual(b["freeboardFt"], 9.5 - b["peakStageFt"], places=6)

    def test_run_project_reports_none_freeboard_when_no_berm_set(self):
        payload = run_project(_project_json())
        proposed_basins = [
            b for s in payload["scenarios"] if s["condition"] == "proposed" for b in s["basins"]
        ]
        for b in proposed_basins:
            self.assertIsNone(b["bermElevationFt"])
            self.assertIsNone(b["freeboardFt"])

    def test_run_project_flags_overtopped_berm_as_critical_finding(self):
        # A berm set below the initial stage is guaranteed overtopped.
        payload = run_project(_project_json(proposed_overrides={"bermElevationFt": 5.1}))
        critical = [f for f in payload["findings"] if f["level"] == "CRITICAL"]
        self.assertTrue(any("Berm overtopped" in f["message"] for f in critical))


if __name__ == "__main__":
    unittest.main()
