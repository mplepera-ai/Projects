import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from storage.water_quality import LegacyVolumetricInputs, calculate_legacy_volumetric
from api.adapter import build_storage_objects, run_storage_calcs, build_narrative_context


class TestImperviousMultiplierDefault(unittest.TestCase):
    def test_default_multiplier_is_2_5_and_matches_old_hardcoded_behavior(self):
        inputs = LegacyVolumetricInputs(project_area_acres=1.0, impervious_fraction=0.6)
        result = calculate_legacy_volumetric(inputs, provided_volume_ac_in=0.0)
        self.assertEqual(result.impervious_multiplier_in, 2.5)
        self.assertAlmostEqual(result.impervious_based_volume_ac_in, 2.5 * 0.6 * 1.0)

    def test_adapter_defaults_missing_multiplier_to_2_5(self):
        data = {
            "waterQuality": {
                "method": "legacy_volumetric", "projectAreaAcres": 1.0, "imperviousFraction": 0.6,
            },
        }
        objs = build_storage_objects(data)
        self.assertEqual(objs["wq_result"].impervious_multiplier_in, 2.5)


class TestImperviousMultiplierOverride(unittest.TestCase):
    """Regression coverage for the reported discrepancy: a City of Miami
    Sec. 20.3 project requires the WQ volume to be the design-storm
    rainfall depth times the FULL site area, not the standard SFWMD
    2.5in x impervious-fraction rule. That's reproduced here (not by
    hardcoding a jurisdiction name) by entering the design rainfall depth
    as the multiplier with impervious_fraction=1.0."""

    def test_custom_multiplier_changes_the_governing_volume(self):
        site_acres = 15511 / 43560.0
        inputs = LegacyVolumetricInputs(
            project_area_acres=site_acres, impervious_fraction=1.0, impervious_multiplier_in=6.5,
        )
        result = calculate_legacy_volumetric(inputs, provided_volume_ac_in=0.0)
        # Matches the hand-calc's "6.5 IN. x 15,511 SF / 12in/ft = 8,402 CF
        # (CITY OF MIAMI 20.3 ORDINANCE)" governing value.
        self.assertAlmostEqual(result.governing_gross_required_volume_ac_in * 3630.0, 8402, delta=1)

    def test_adapter_honors_supplied_multiplier(self):
        data = {
            "waterQuality": {
                "method": "legacy_volumetric",
                "projectAreaAcres": 15511 / 43560.0,
                "imperviousFraction": 1.0,
                "imperviousMultiplierIn": 6.5,
            },
        }
        objs = build_storage_objects(data)
        self.assertAlmostEqual(
            objs["wq_result"].governing_gross_required_volume_ac_in * 3630.0, 8402, delta=1,
        )

    def test_narrative_context_reports_the_multiplier_actually_used(self):
        data = {
            "siteAreas": {"existingSiteSqft": 15511, "existingPerviousSqft": 2483,
                          "proposedSiteSqft": 15511, "proposedPerviousSqft": 2823},
            "soilStorage": {"existingCompactedIn": 8.18, "proposedCompactedIn": 8.18, "rainfallIn": 6.5},
            "waterQuality": {
                "method": "legacy_volumetric",
                "projectAreaAcres": 15511 / 43560.0,
                "imperviousFraction": 1.0,
                "imperviousMultiplierIn": 6.5,
                "providedVolumeAcIn": 0.0,
            },
        }
        ctx = build_narrative_context(data)
        self.assertEqual(ctx["wqImperviousMultiplierIn"], 6.5)


if __name__ == "__main__":
    unittest.main()
