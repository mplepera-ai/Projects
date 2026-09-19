import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from storage.swale import Swale, SwaleError, merge_swales_into_basin_curve
from storage.exfiltration import ExfiltrationTrench, ExfiltrationError, H2Method
from storage.pond import suggest_pond_footprint, pond_volume_cuft, PondSizingError
from core.interpolation import StageStorageCurve
from core.units import ACRE_IN_TO_CUFT


class TestSwale(unittest.TestCase):
    def test_triangular_swale_area(self):
        swale = Swale(name="Tri", bottom_elevation_ft=6.0, top_elevation_ft=7.0,
                       length_ft=100.0, bottom_width_ft=0.0,
                       left_side_slope_h_per_v=4.0, right_side_slope_h_per_v=4.0)
        # A(1.0) = 0 + 0.5*(4+4)*1^2 = 4.0 sqft
        self.assertAlmostEqual(swale.cross_sectional_area_sqft(1.0), 4.0, places=6)

    def test_rectangular_swale_area(self):
        swale = Swale(name="Rect", bottom_elevation_ft=6.0, top_elevation_ft=7.0,
                       length_ft=50.0, bottom_width_ft=3.0,
                       left_side_slope_h_per_v=0.0, right_side_slope_h_per_v=0.0)
        self.assertAlmostEqual(swale.cross_sectional_area_sqft(0.5), 1.5, places=6)

    def test_storage_scales_with_length(self):
        swale = Swale(name="S", bottom_elevation_ft=6.0, top_elevation_ft=7.0,
                       length_ft=200.0, bottom_width_ft=2.0)
        double = Swale(name="S2", bottom_elevation_ft=6.0, top_elevation_ft=7.0,
                        length_ft=400.0, bottom_width_ft=2.0)
        self.assertAlmostEqual(double.storage_cuft(0.5), 2 * swale.storage_cuft(0.5), places=4)

    def test_invalid_geometry_rejected(self):
        with self.assertRaises(SwaleError):
            Swale(name="Bad", bottom_elevation_ft=7.0, top_elevation_ft=6.0, length_ft=100.0)

    def test_stage_storage_table_monotonic(self):
        swale = Swale(name="S", bottom_elevation_ft=6.0, top_elevation_ft=7.5, length_ft=300.0,
                       bottom_width_ft=2.0)
        table = swale.stage_storage_table(n_points=10)
        storages = [s for _, s in table]
        self.assertEqual(storages, sorted(storages))

    def test_merge_into_basin_curve_increases_storage(self):
        base = StageStorageCurve(points=[(6.0, 0.0), (7.0, 5.0), (8.0, 12.0)])
        swale = Swale(name="S", bottom_elevation_ft=6.0, top_elevation_ft=7.5, length_ft=300.0,
                       bottom_width_ft=2.0)
        merged = merge_swales_into_basin_curve(base, [swale])
        self.assertGreaterEqual(merged.storage_at_stage(7.0), base.storage_at_stage(7.0))

    def test_excluded_swale_not_merged(self):
        base = StageStorageCurve(points=[(6.0, 0.0), (7.0, 5.0), (8.0, 12.0)])
        swale = Swale(name="S", bottom_elevation_ft=6.0, top_elevation_ft=7.5, length_ft=300.0,
                       bottom_width_ft=2.0, include_in_basin_storage=False)
        merged = merge_swales_into_basin_curve(base, [swale])
        self.assertAlmostEqual(merged.storage_at_stage(7.0), base.storage_at_stage(7.0), places=6)


class TestExfiltrationTrench(unittest.TestCase):
    def _trench(self, **overrides):
        defaults = dict(
            name="ET-1", control_elevation_ft=6.00, design_water_table_ft=3.00,
            trench_top_elevation_ft=7.00, trench_bottom_elevation_ft=3.00,
            pipe_invert_elevation_ft=6.20, lowest_overflow_elevation_ft=7.20,
            design_water_surface_ft=7.17, trench_width_ft=4.00,
            actual_trench_length_ft=210.0, hydraulic_conductivity_k=1.55e-4,
            factor_of_safety=2.0,
        )
        defaults.update(overrides)
        return ExfiltrationTrench(**defaults)

    def test_du_ds_split_water_table_within_trench(self):
        trench = self._trench()
        # top=7, bottom=3, water table=3 -> fully unsaturated
        self.assertAlmostEqual(trench.Du, 4.0, places=6)
        self.assertAlmostEqual(trench.Ds, 0.0, places=6)

    def test_du_ds_water_table_above_trench(self):
        trench = self._trench(design_water_table_ft=8.0)
        self.assertAlmostEqual(trench.Du, 0.0, places=6)
        self.assertAlmostEqual(trench.Ds, 4.0, places=6)

    def test_required_length_positive(self):
        trench = self._trench()
        vwq_cuft = 0.640 * ACRE_IN_TO_CUFT
        length = trench.required_length_ft(vwq_cuft)
        self.assertGreater(length, 0.0)

    def test_reversed_elevations_rejected(self):
        with self.assertRaises(ExfiltrationError):
            self._trench(trench_top_elevation_ft=3.0, trench_bottom_elevation_ft=7.0)

    def test_fs_below_minimum_rejected(self):
        with self.assertRaises(ExfiltrationError):
            self._trench(factor_of_safety=1.5)

    def test_status_pass_when_provided_exceeds_required(self):
        trench = self._trench(actual_trench_length_ft=10000.0)
        report = trench.report(0.640 * ACRE_IN_TO_CUFT)
        self.assertEqual(report["status"], "PASS")

    def test_status_fail_when_provided_below_required(self):
        trench = self._trench(actual_trench_length_ft=1.0)
        report = trench.report(0.640 * ACRE_IN_TO_CUFT)
        self.assertEqual(report["status"], "FAIL")

    def test_geometry_warning_when_width_too_large(self):
        trench = self._trench(trench_width_ft=50.0)
        warnings = trench.validity_warnings()
        self.assertTrue(any("W > 2*(Du+Ds)" in w for w in warnings))

    def test_rock_volume_subtracts_pipe_from_gross_trench_volume(self):
        # W=4, H=4 (top 7 - bottom 3), L=210 -> gross = 3360 CF.
        # 12" pipe -> area = pi*0.5^2 = 0.7853981... SF, pipe volume over
        # the full length = 164.9336... CF.
        trench = self._trench(pipe_diameter_in=12.0)
        self.assertAlmostEqual(trench.pipe_area_sqft, 0.7853981633974483, places=6)
        expected_rock_cuft = 4.0 * 4.0 * 210.0 - 0.7853981633974483 * 210.0
        self.assertAlmostEqual(trench.rock_volume_cuft, expected_rock_cuft, places=3)
        self.assertAlmostEqual(trench.rock_volume_cy, expected_rock_cuft / 27.0, places=6)

    def test_rock_volume_zero_pipe_equals_gross_trench_volume(self):
        # No pipe diameter given -> rock volume is just the gross trench
        # volume (nothing to subtract).
        trench = self._trench()
        self.assertIsNone(trench.pipe_diameter_in)
        self.assertEqual(trench.pipe_area_sqft, 0.0)
        self.assertAlmostEqual(trench.rock_volume_cuft, 4.0 * 4.0 * 210.0, places=6)

    def test_pipe_diameter_does_not_affect_hydraulic_sizing(self):
        # Pipe size is a construction/materials quantity only -- it must
        # never change H2/Du/Ds/required length, whether present or not.
        without_pipe = self._trench()
        with_pipe = self._trench(pipe_diameter_in=15.0)
        vwq_cuft = 0.640 * ACRE_IN_TO_CUFT
        self.assertEqual(without_pipe.H2, with_pipe.H2)
        self.assertEqual(without_pipe.Du, with_pipe.Du)
        self.assertEqual(without_pipe.Ds, with_pipe.Ds)
        self.assertEqual(
            without_pipe.required_length_ft(vwq_cuft),
            with_pipe.required_length_ft(vwq_cuft),
        )

    def test_suggest_trench_widths_returns_one_row_per_width_and_does_not_mutate(self):
        trench = self._trench()
        original_width = trench.trench_width_ft
        vwq_cuft = 0.640 * ACRE_IN_TO_CUFT
        rows = trench.suggest_trench_widths(vwq_cuft, width_options_ft=[2.0, 4.0, 6.0, 8.0])
        self.assertEqual(len(rows), 4)
        self.assertEqual(trench.trench_width_ft, original_width)
        for r in rows:
            self.assertIn(r["width_ft"], [2.0, 4.0, 6.0, 8.0])
            self.assertGreater(r["required_length_ft"], 0.0)
            self.assertGreaterEqual(r["rock_volume_cy"], 0.0)

    def test_suggest_trench_widths_narrower_trench_needs_more_length(self):
        # Physically: a narrower trench has less exfiltration face per
        # foot of length, so it needs MORE length to pass the same
        # required volume -- required_length_ft should decrease as
        # width increases (all else equal).
        trench = self._trench()
        vwq_cuft = 0.640 * ACRE_IN_TO_CUFT
        rows = trench.suggest_trench_widths(vwq_cuft, width_options_ft=[2.0, 4.0, 8.0])
        by_width = {r["width_ft"]: r["required_length_ft"] for r in rows}
        self.assertGreater(by_width[2.0], by_width[4.0])
        self.assertGreater(by_width[4.0], by_width[8.0])

    def test_suggest_trench_widths_recommends_lowest_rock_volume_that_fits(self):
        trench = self._trench()
        vwq_cuft = 0.640 * ACRE_IN_TO_CUFT
        # Give a generous available length so every width option fits;
        # the recommendation should be whichever width has the lowest
        # rock_volume_cy among all rows actually returned.
        rows = trench.suggest_trench_widths(
            vwq_cuft, width_options_ft=[2.0, 4.0, 6.0, 8.0, 10.0], available_length_ft=10000.0,
        )
        recommended = [r for r in rows if r["recommended"]]
        self.assertEqual(len(recommended), 1)
        min_rock = min(r["rock_volume_cy"] for r in rows)
        self.assertAlmostEqual(recommended[0]["rock_volume_cy"], min_rock, places=6)
        self.assertTrue(all(r["fits_available_length"] for r in rows))

    def test_suggest_trench_widths_falls_back_when_nothing_fits(self):
        trench = self._trench()
        vwq_cuft = 0.640 * ACRE_IN_TO_CUFT
        # Absurdly short available length -- nothing fits, but a
        # recommendation should still come back (the lowest-rock-volume
        # option overall) rather than silently returning nothing.
        rows = trench.suggest_trench_widths(
            vwq_cuft, width_options_ft=[2.0, 4.0, 6.0], available_length_ft=0.01,
        )
        self.assertTrue(all(not r["fits_available_length"] for r in rows))
        recommended = [r for r in rows if r["recommended"]]
        self.assertEqual(len(recommended), 1)

    def test_h2_never_hidden_all_methods_computable(self):
        for method in H2Method:
            kwargs = {"h2_method": method}
            if method == H2Method.USER_DEFINED:
                kwargs["user_defined_h2_ft"] = 3.5
            trench = self._trench(**kwargs)
            self.assertIsInstance(trench.H2, float)

    def test_conservative_equation_triggered_when_ds_exceeds_du(self):
        # Ds > Du -> requires_conservative_equation() must be True.
        # Water table above the trench top means Du=0, Ds=full height.
        trench = self._trench(design_water_table_ft=10.0)
        self.assertTrue(trench.requires_conservative_equation())

    def test_conservative_length_matches_real_world_worked_example(self):
        # From a real submitted South Florida exfiltration trench sheet:
        # K=0.000118, FS=2, %WQ=0.5, H2=1.80, Heff=0.90, Du=1.80, Ds=8.20,
        # W=6.00, Vwq=0.4836 ac-in -> L1~107.15 LF, L2~149.25 LF (L2 governs, Ds>Du)
        trench = ExfiltrationTrench(
            name="ET-1", control_elevation_ft=7.5, design_water_table_ft=5.7,
            trench_top_elevation_ft=7.5, trench_bottom_elevation_ft=-2.5,
            pipe_invert_elevation_ft=5.0, lowest_overflow_elevation_ft=7.5,
            design_water_surface_ft=5.0, trench_width_ft=6.0, actual_trench_length_ft=150.0,
            hydraulic_conductivity_k=0.000118, factor_of_safety=2.0,
            effective_head_ft=0.90, percent_wq_required=0.5,
            user_defined_h2_ft=1.80, h2_method=H2Method.USER_DEFINED,
        )
        self.assertTrue(trench.requires_conservative_equation())
        vwq_cuft = 0.4836 * ACRE_IN_TO_CUFT
        L1 = trench.required_length_ft(vwq_cuft, force_conservative=False)
        L2 = trench.required_length_ft(vwq_cuft, force_conservative=True)
        self.assertAlmostEqual(L1, 107.15, delta=1.5)
        self.assertAlmostEqual(L2, 149.25, delta=1.5)
        # The auto-selected (default) equation should match the conservative one
        auto = trench.required_length_ft(vwq_cuft)
        self.assertAlmostEqual(auto, L2, places=6)


class TestPondSizing(unittest.TestCase):
    def test_pond_volume_cuft_zero_slope_is_simple_prism(self):
        # side_slope=0 -> vertical walls -> volume = L*W*D exactly.
        v = pond_volume_cuft(bottom_length_ft=20.0, bottom_width_ft=10.0, depth_ft=3.0, side_slope_h_per_v=0.0)
        self.assertAlmostEqual(v, 20.0 * 10.0 * 3.0, places=6)

    def test_pond_volume_cuft_matches_hand_calc_with_slope(self):
        # L0=10, W0=10, D=2, z=3 -> top=(10+12)x(10+12)=22x22, mid=(10+6)x(10+6)=16x16
        # V = 2/6 * (100 + 484 + 4*256) = 2/6 * 1608 = 536
        v = pond_volume_cuft(bottom_length_ft=10.0, bottom_width_ft=10.0, depth_ft=2.0, side_slope_h_per_v=3.0)
        self.assertAlmostEqual(v, 536.0, places=3)

    def test_suggest_pond_footprint_meets_or_exceeds_required_volume(self):
        result = suggest_pond_footprint(required_volume_cuft=5000.0, depth_ft=3.0, side_slope_h_per_v=4.0, length_to_width_ratio=1.5)
        self.assertGreaterEqual(result.volume_provided_cuft, 5000.0)
        # Should not wildly overshoot -- the bisection tolerance is tight.
        self.assertLess(result.volume_provided_cuft, 5000.0 + 50.0)

    def test_suggest_pond_footprint_respects_length_width_ratio(self):
        result = suggest_pond_footprint(required_volume_cuft=3000.0, depth_ft=2.5, side_slope_h_per_v=3.0, length_to_width_ratio=2.0)
        self.assertAlmostEqual(result.bottom_length_ft / result.bottom_width_ft, 2.0, places=4)

    def test_suggest_pond_footprint_actual_volume_recomputes_to_same_value(self):
        result = suggest_pond_footprint(required_volume_cuft=8000.0, depth_ft=4.0, side_slope_h_per_v=4.0)
        recomputed = pond_volume_cuft(result.bottom_length_ft, result.bottom_width_ft, result.depth_ft, result.side_slope_h_per_v)
        self.assertAlmostEqual(recomputed, result.volume_provided_cuft, places=3)

    def test_suggest_pond_footprint_rejects_bad_inputs(self):
        with self.assertRaises(PondSizingError):
            suggest_pond_footprint(required_volume_cuft=-1.0, depth_ft=3.0)
        with self.assertRaises(PondSizingError):
            suggest_pond_footprint(required_volume_cuft=1000.0, depth_ft=0.0)
        with self.assertRaises(PondSizingError):
            suggest_pond_footprint(required_volume_cuft=1000.0, depth_ft=3.0, length_to_width_ratio=0.0)


if __name__ == "__main__":
    unittest.main()
