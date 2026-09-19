import unittest
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.interpolation import StageStorageCurve
from hydraulics.structures import DrainageWell, RectangularWeir, DestinationClassification
from hydraulics.basin import Basin
from hydraulics.network import BasinNetwork
from regulatory.profiles import broward_county_swm_profile
from project.model import Project, ProjectMetadata, Condition, run_all_scenarios, compare_existing_vs_proposed
from project.serialization import project_to_dict, project_from_dict, save_project, load_project
from qa.validation import run_qa, has_unresolved_critical


def _build_test_project() -> Project:
    curve_ex = StageStorageCurve(points=[(5.0, 0.0), (6.0, 1.8), (7.0, 5.1), (8.0, 9.6), (9.0, 15.8)])
    outfall_ex = RectangularWeir(name="Existing Outfall", crest_elevation_ft=6.8, length_ft=6.0,
                                  destination=DestinationClassification.OFFSITE_DISCHARGE)
    basin_ex = Basin(basin_id="SITE", name="Existing", area_acres=12.0, ground_storage_inches=3.0,
                      time_of_concentration_hours=0.3, initial_stage_ft=5.0,
                      stage_storage=curve_ex, structures=[outfall_ex])

    curve_pr = StageStorageCurve(points=[(5.0, 0.0), (6.0, 2.4), (7.0, 6.3), (8.0, 11.2), (9.0, 17.9)])
    well_pr = DrainageWell(name="Well W-1", capacity_gpm=210, turn_on_stage=5.5, turn_off_stage=5.4)
    outfall_pr = RectangularWeir(name="Proposed Outfall", crest_elevation_ft=6.9, length_ft=6.0,
                                  destination=DestinationClassification.OFFSITE_DISCHARGE)
    basin_pr = Basin(basin_id="SITE", name="Proposed", area_acres=12.0, ground_storage_inches=2.5,
                      time_of_concentration_hours=0.25, initial_stage_ft=5.0,
                      stage_storage=curve_pr, structures=[well_pr, outfall_pr])

    project = Project(
        metadata=ProjectMetadata(project_name="Test Project"),
        regulatory_profile=broward_county_swm_profile(),
    )
    project.add_condition(Condition(name="existing", network=BasinNetwork(basins={"SITE": basin_ex})))
    project.add_condition(Condition(name="proposed", network=BasinNetwork(basins={"SITE": basin_pr})))
    project.default_scenarios_from_profile(time_step_hours=0.2)
    return project


class TestProjectIntegration(unittest.TestCase):
    def test_default_scenarios_cover_both_conditions(self):
        project = _build_test_project()
        conditions_used = {s.condition_name for s in project.scenarios}
        self.assertEqual(conditions_used, {"existing", "proposed"})

    def test_run_all_scenarios_produces_a_result_per_scenario(self):
        project = _build_test_project()
        results = run_all_scenarios(project)
        self.assertEqual(len(results), len(project.scenarios))

    def test_proposed_100y_scenario_is_zero_offsite(self):
        project = _build_test_project()
        results = run_all_scenarios(project)
        pr_100y = next(r for sid, r in results.items()
                        if r.scenario.condition_name == "proposed" and r.scenario.event_code == "100Y-3D")
        self.assertAlmostEqual(
            pr_100y.network_result.network_total_offsite_discharge_acre_ft, 0.0, places=5,
        )

    def test_qa_finds_no_unresolved_critical_on_clean_project(self):
        project = _build_test_project()
        results = run_all_scenarios(project)
        findings = run_qa(project, results)
        self.assertFalse(has_unresolved_critical(findings))

    def test_no_berm_finding_when_berm_elevation_not_set(self):
        # Default test project's basins have no berm_elevation_ft (None)
        # -- the check must skip them entirely rather than treating a
        # missing berm as "overtopped."
        project = _build_test_project()
        results = run_all_scenarios(project)
        findings = run_qa(project, results)
        self.assertFalse(any("Berm overtopped" in f.message for f in findings))

    def test_berm_overtopped_produces_critical_finding(self):
        project = _build_test_project()
        # Set the proposed basin's berm just below its known peak stage
        # range (curve tops out around 9 ft) so it's guaranteed overtopped.
        project.conditions["proposed"].network.basins["SITE"].berm_elevation_ft = 5.2
        results = run_all_scenarios(project)
        findings = run_qa(project, results)
        overtop_findings = [f for f in findings if "Berm overtopped" in f.message]
        self.assertTrue(len(overtop_findings) > 0)
        self.assertTrue(all(f.level.value == "CRITICAL" for f in overtop_findings))
        self.assertTrue(has_unresolved_critical(findings))

    def test_berm_above_peak_stage_produces_no_overtop_finding(self):
        project = _build_test_project()
        # A generously high berm should never be overtopped.
        project.conditions["proposed"].network.basins["SITE"].berm_elevation_ft = 100.0
        results = run_all_scenarios(project)
        findings = run_qa(project, results)
        self.assertFalse(any("Berm overtopped" in f.message for f in findings))

    def test_final_model_summary_table_reports_freeboard_as_plain_value(self):
        from reports.generator import final_model_summary_table
        project = _build_test_project()
        project.conditions["proposed"].network.basins["SITE"].berm_elevation_ft = 9.5
        results = run_all_scenarios(project)
        table = final_model_summary_table(project, results)
        self.assertIn("Freeboard (ft)", table)
        # Existing basin has no berm set -- its rows show the blank marker.
        self.assertIn("—", table)

    def test_comparison_engine_returns_a_row_per_shared_event(self):
        project = _build_test_project()
        results = run_all_scenarios(project)
        rows = compare_existing_vs_proposed(results)
        event_codes = {r.event_code for r in rows}
        expected_codes = {e.code for e in project.regulatory_profile.selected_by_default()}
        self.assertEqual(event_codes, expected_codes)


class TestProjectSerialization(unittest.TestCase):
    def test_round_trip_preserves_metadata(self):
        project = _build_test_project()
        d = project_to_dict(project)
        restored = project_from_dict(d)
        self.assertEqual(restored.metadata.project_name, project.metadata.project_name)

    def test_round_trip_preserves_basin_geometry(self):
        project = _build_test_project()
        restored = project_from_dict(project_to_dict(project))
        original_basin = project.conditions["proposed"].network.basins["SITE"]
        restored_basin = restored.conditions["proposed"].network.basins["SITE"]
        self.assertEqual(restored_basin.area_acres, original_basin.area_acres)
        self.assertEqual(len(restored_basin.structures), len(original_basin.structures))

    def test_round_trip_preserves_structure_types_and_destination(self):
        project = _build_test_project()
        restored = project_from_dict(project_to_dict(project))
        restored_basin = restored.conditions["proposed"].network.basins["SITE"]
        well = next(s for s in restored_basin.structures if s.name == "Well W-1")
        self.assertEqual(well.destination, DestinationClassification.ONSITE_DISPOSAL)
        self.assertEqual(type(well).__name__, "DrainageWell")

    def test_restored_project_produces_identical_zero_offsite_result(self):
        project = _build_test_project()
        restored = project_from_dict(project_to_dict(project))

        results_original = run_all_scenarios(project)
        results_restored = run_all_scenarios(restored)

        pr_100y_orig = next(r for r in results_original.values()
                             if r.scenario.condition_name == "proposed" and r.scenario.event_code == "100Y-3D")
        pr_100y_restored = next(r for r in results_restored.values()
                                 if r.scenario.condition_name == "proposed" and r.scenario.event_code == "100Y-3D")
        self.assertAlmostEqual(
            pr_100y_orig.network_result.peak_stage_ft["SITE"],
            pr_100y_restored.network_result.peak_stage_ft["SITE"],
            places=6,
        )

    def test_save_and_load_file_round_trip(self):
        project = _build_test_project()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_project.json")
            save_project(project, path)
            self.assertTrue(os.path.exists(path))
            restored = load_project(path)
            self.assertEqual(restored.metadata.project_name, project.metadata.project_name)


if __name__ == "__main__":
    unittest.main()
