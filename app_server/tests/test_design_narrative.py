import unittest
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.adapter import build_narrative_context, build_project_from_app_json
from project.model import ProjectMetadata
from storage.exfiltration import ExfiltrationTrench
from reports.trench_section import draw_exfiltration_trench_section
from reports.generator import (
    ReportOptions, design_narrative_intro_sections, design_narrative_conclusion_section,
)


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


def _project_json(metadata_overrides=None):
    metadata = {"name": "Narrative Test Project"}
    metadata.update(metadata_overrides or {})
    return {
        "metadata": metadata,
        "storms": {
            "y5": {"dur": 24, "depth": 3.0}, "y25": {"dur": 72, "depth": 9.0}, "y100": {"dur": 72, "depth": 14.0},
        },
        "basins": {
            "existing": {"basins": {"SITE": _basin_json()}, "links": []},
            "proposed": {"basins": {"SITE": _basin_json()}, "links": []},
        },
    }


class TestProjectMetadataNarrativeFields(unittest.TestCase):
    def test_blank_narrative_fields_default_to_empty_string(self):
        project = build_project_from_app_json(_project_json())
        m = project.metadata
        self.assertEqual(m.fema_community_panel, "")
        self.assertEqual(m.fema_flood_zone, "")
        self.assertEqual(m.fema_bfe_navd, "")
        self.assertEqual(m.design_water_table_source, "")
        self.assertEqual(m.system_narrative, "")

    def test_narrative_fields_parsed_from_app_json(self):
        project = build_project_from_app_json(_project_json({
            "femaCommunityPanel": "0367J", "femaFloodZone": "AE", "femaBfeNavd": "6.00",
            "waterTableSource": "Broward County 2070 map",
            "systemNarrative": "Catch basins and exfiltration trenches.",
        }))
        m = project.metadata
        self.assertEqual(m.fema_community_panel, "0367J")
        self.assertEqual(m.fema_flood_zone, "AE")
        self.assertEqual(m.fema_bfe_navd, "6.00")
        self.assertEqual(m.design_water_table_source, "Broward County 2070 map")
        self.assertEqual(m.system_narrative, "Catch basins and exfiltration trenches.")


class TestBuildNarrativeContext(unittest.TestCase):
    def test_empty_storage_data_returns_empty_context(self):
        self.assertEqual(build_narrative_context({}), {})
        self.assertEqual(build_narrative_context(None), {})

    def test_context_includes_equivalent_rainfall_reduction_when_trench_present(self):
        data = {
            "siteAreas": {"existingSiteSqft": 39930, "existingPerviousSqft": 20000,
                          "proposedSiteSqft": 39930, "proposedPerviousSqft": 15000},
            "soilStorage": {"existingCompactedIn": 1.88, "proposedCompactedIn": 8.18, "rainfallIn": 18.0},
            "swales": [],
            "exfiltration": {
                "name": "ET-1", "controlElevationFt": 3.25, "designWaterTableFt": 3.00,
                "trenchTopElevationFt": 5.00, "trenchBottomElevationFt": -5.00,
                "pipeInvertElevationFt": 3.9, "lowestOverflowElevationFt": 6.9,
                "designWaterSurfaceFt": 3.25, "trenchWidthFt": 6.0, "actualTrenchLengthFt": 150.0,
                "hydraulicConductivityK": 0.000118, "pipeDiameterIn": 15, "factorOfSafety": 2.0,
                "h2Method": "SFWMD_STANDARD", "percentWqRequired": 0.5,
                "requiredVolumeBasis": "runoffVolume",
            },
        }
        ctx = build_narrative_context(data)
        self.assertIn("equivalentRainfallReductionIn", ctx)
        self.assertGreater(ctx["equivalentRainfallReductionIn"], 0)
        self.assertIn("exfiltrationTrenchStatus", ctx)
        self.assertEqual(ctx["wqProvidedSource"], "Exfiltration Trench(es)")

    def test_context_never_recomputes_a_different_number_than_run_storage_calcs(self):
        # Section 71 regression guard: build_narrative_context must reuse
        # build_storage_objects, not recompute independently -- so its
        # equivalentRainfallReductionIn must always match run_storage_calcs'.
        from api.adapter import run_storage_calcs
        data = {
            "siteAreas": {"existingSiteSqft": 39930, "existingPerviousSqft": 20000,
                          "proposedSiteSqft": 39930, "proposedPerviousSqft": 15000},
            "soilStorage": {"existingCompactedIn": 1.88, "proposedCompactedIn": 8.18, "rainfallIn": 18.0},
            "swales": [],
            "exfiltration": {
                "name": "ET-1", "controlElevationFt": 3.25, "designWaterTableFt": 3.00,
                "trenchTopElevationFt": 5.00, "trenchBottomElevationFt": -5.00,
                "pipeInvertElevationFt": 3.9, "lowestOverflowElevationFt": 6.9,
                "designWaterSurfaceFt": 3.25, "trenchWidthFt": 6.0, "actualTrenchLengthFt": 150.0,
                "hydraulicConductivityK": 0.000118, "pipeDiameterIn": 15, "factorOfSafety": 2.0,
                "h2Method": "SFWMD_STANDARD", "percentWqRequired": 0.5,
                "requiredVolumeBasis": "runoffVolume",
            },
        }
        ctx = build_narrative_context(data)
        run_result = run_storage_calcs(data)
        self.assertAlmostEqual(
            ctx["equivalentRainfallReductionIn"],
            run_result["exfiltration"]["equivalentRainfallReductionIn"],
            places=9,
        )


class TestDesignNarrativeSections(unittest.TestCase):
    def _project(self, **metadata_kwargs):
        from regulatory.profiles import RegulatoryProfile
        # Reuse the project-integration helper's profile shape via a minimal
        # stand-in: the narrative functions only read project.metadata and
        # (for the conclusion) project.metadata again, never the profile's
        # required events, so a bare profile with no events is sufficient.
        from tests.test_project_integration import _build_test_project
        project = _build_test_project()
        for k, v in metadata_kwargs.items():
            setattr(project.metadata, k, v)
        return project

    def test_blank_fields_render_bracketed_placeholders(self):
        project = self._project()
        sections = design_narrative_intro_sections(project)
        joined = "\n".join(p for s in sections for p in s["paragraphs"])
        self.assertIn("[", joined)
        self.assertIn("FEMA COMMUNITY-PANEL NUMBER", joined)

    def test_filled_fields_appear_verbatim_not_as_placeholders(self):
        project = self._project(
            fema_community_panel="0367J", fema_flood_zone="AE", fema_bfe_navd="6.00",
            design_water_table_source="Broward County 2070 map",
            system_narrative="Catch basins and exfiltration trenches.",
        )
        sections = design_narrative_intro_sections(project)
        joined = "\n".join(p for s in sections for p in s["paragraphs"])
        self.assertIn("0367J", joined)
        self.assertIn("AE", joined)
        self.assertIn("Broward County 2070 map", joined)
        self.assertIn("Catch basins and exfiltration trenches.", joined)
        self.assertNotIn("FEMA COMMUNITY-PANEL NUMBER", joined)

    def test_storage_ctx_numbers_appear_in_water_quality_section(self):
        project = self._project()
        ctx = {"wqRequiredAcFt": 0.5, "wqProvidedAcFt": 0.55, "wqProvidedSource": "Exfiltration Trench(es)",
               "equivalentRainfallReductionIn": 0.53}
        sections = design_narrative_intro_sections(project, ctx)
        wq_section = next(s for s in sections if s["heading"] == "Water Quality Criteria")
        joined = "\n".join(wq_section["paragraphs"])
        self.assertIn("0.500", joined)
        self.assertIn("0.550", joined)
        self.assertIn("0.53 inches", joined)

    def test_conclusion_section_names_project(self):
        project = self._project()
        conclusion = design_narrative_conclusion_section(project)
        self.assertIn(project.metadata.project_name, conclusion["paragraphs"][0])

    def test_report_options_default_excludes_design_narrative(self):
        self.assertFalse(ReportOptions().include_design_narrative)


class TestTrenchSectionDrawing(unittest.TestCase):
    def test_draws_a_nonempty_png(self):
        trench = ExfiltrationTrench(
            name="ET-1", control_elevation_ft=3.25, design_water_table_ft=3.00,
            trench_top_elevation_ft=5.00, trench_bottom_elevation_ft=-5.00,
            pipe_invert_elevation_ft=3.9, lowest_overflow_elevation_ft=6.9,
            design_water_surface_ft=3.25, trench_width_ft=6.0, actual_trench_length_ft=150.0,
            hydraulic_conductivity_k=0.000118, pipe_diameter_in=15, factor_of_safety=2.0,
            percent_wq_required=0.5,
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            draw_exfiltration_trench_section(trench, tmp.name, pavement_elevation_ft=7.50)
            self.assertTrue(os.path.exists(tmp.name))
            self.assertGreater(os.path.getsize(tmp.name), 1000)

    def test_assumed_pavement_elevation_when_not_supplied(self):
        trench = ExfiltrationTrench(
            name="ET-1", control_elevation_ft=3.25, design_water_table_ft=3.00,
            trench_top_elevation_ft=5.00, trench_bottom_elevation_ft=-5.00,
            pipe_invert_elevation_ft=3.9, lowest_overflow_elevation_ft=6.9,
            design_water_surface_ft=3.25, trench_width_ft=6.0, actual_trench_length_ft=150.0,
            hydraulic_conductivity_k=0.000118, factor_of_safety=2.0, percent_wq_required=0.5,
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            # No pavement_elevation_ft supplied -- must not raise, and must
            # fall back to trench_top_elevation_ft + the typical-cover default.
            draw_exfiltration_trench_section(trench, tmp.name)
            self.assertTrue(os.path.exists(tmp.name))
            self.assertGreater(os.path.getsize(tmp.name), 1000)


if __name__ == "__main__":
    unittest.main()
