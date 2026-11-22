"""Testes da escala do Valorant, da projeção e do diagnóstico local."""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from valaim.analyzer import analyze_sessions
from valaim.mathutil import (
    FlickTracker,
    cm_per_360,
    edpi,
    project_point,
    vertical_fov_deg,
    view_error_degrees,
)


class MathTests(unittest.TestCase):
    def test_edpi_and_cm360(self) -> None:
        self.assertAlmostEqual(edpi(800, 0.35), 280.0)
        # 360 / (800 * 0.35 * 0.07) * 2.54 = 46.653...
        self.assertAlmostEqual(cm_per_360(800, 0.35), 46.653061224, places=4)

    def test_vertical_fov_matches_16_9(self) -> None:
        vfov = vertical_fov_deg(103.0)
        self.assertAlmostEqual(vfov, 70.53, delta=0.15)

    def test_center_projects_to_screen_center(self) -> None:
        projected = project_point(0.0, 0.0, (0.0, 1.6, 0.0), (0.0, 1.6, 15.0), 1600, 900)
        self.assertIsNotNone(projected)
        assert projected is not None
        self.assertAlmostEqual(projected[0], 800.0, places=3)
        self.assertAlmostEqual(projected[1], 450.0, places=3)

    def test_target_on_the_right_is_positive_error(self) -> None:
        err = view_error_degrees(0.0, 0.0, (0.0, 1.6, 0.0), (2.0, 1.6, 15.0))
        self.assertGreater(err[0], 0.0)
        self.assertAlmostEqual(err[1], 0.0, places=4)

    def test_point_behind_camera_is_hidden(self) -> None:
        self.assertIsNone(project_point(0.0, 0.0, (0.0, 1.6, 0.0), (0.0, 1.6, -2.0), 1600, 900))

    def test_overshoot_past_target(self) -> None:
        tracker = FlickTracker((10.0, 0.0))
        tracker.update((0.0, 0.0), 0.05, 80.0)
        tracker.update((-3.0, 0.0), 0.05, 130.0)
        shot = tracker.snapshot(130.0, False, 15.0, 0.12)
        self.assertGreater(shot["overshoot_deg"], 2.5)
        self.assertLess(shot["path_efficiency"], 1.0)
        self.assertIsNotNone(shot["reaction_ms"])


def _shot(**kwargs) -> dict:
    base = {
        "initial_angle_deg": 4.0,
        "overshoot_deg": 0.2,
        "undershoot_deg": 0.2,
        "path_efficiency": 0.9,
        "near_target_reversals": 0,
        "hit": True,
    }
    base.update(kwargs)
    return base


def _battery(sens: float, bid: str, micro: dict, strafe: dict, wide: dict | None = None) -> list[dict]:
    rows = [
        {"exercise": "micro_flicks", "battery_id": bid, "sensitivity": sens, "shots": [micro] * 8},
        {
            "exercise": "strafe_tracking",
            "battery_id": bid,
            "sensitivity": sens,
            "shots": [],
            "direction_reversal_lag_ms_mean": strafe["lag"],
            "time_on_target_pct": strafe["tot"],
        },
    ]
    if wide:
        rows.append({"exercise": "wide_flicks", "battery_id": bid, "sensitivity": sens, "shots": [wide] * 6})
    return rows


class AnalyzerTests(unittest.TestCase):
    def test_high_sens_recommends_reduction_after_three_batteries(self) -> None:
        micro = _shot(overshoot_deg=1.8, path_efficiency=0.55, near_target_reversals=1, hit=False)
        sessions = []
        for i in range(3):
            sessions.extend(_battery(0.30, f"b{i}", micro, {"lag": 40, "tot": 55}))
        result = analyze_sessions(sessions, 400, 0.30)
        self.assertTrue(result["validated"])
        self.assertLess(result["adjust_percent"], 0)
        self.assertLess(result["recommended_sensitivity"], 0.30)
        self.assertIn("0.30", result["report"])
        self.assertIn("eDPI", result["report"])

    def test_low_sens_recommends_increase(self) -> None:
        micro = _shot(overshoot_deg=0.1, undershoot_deg=1.6, path_efficiency=0.93)
        wide = _shot(initial_angle_deg=50, overshoot_deg=0.2, undershoot_deg=2.2, path_efficiency=0.9)
        sessions = []
        for i in range(3):
            sessions.extend(_battery(0.30, f"c{i}", micro, {"lag": 180, "tot": 40}, wide))
        result = analyze_sessions(sessions, 400, 0.30)
        self.assertGreater(result["adjust_percent"], 0)
        self.assertGreater(result["recommended_sensitivity"], 0.30)

    def test_good_scores_do_not_ask_for_another_increase(self) -> None:
        micro = _shot(overshoot_deg=0.08, undershoot_deg=2.8, path_efficiency=0.89, hit=True)
        sessions = _battery(0.324, "done", micro, {"lag": 896, "tot": 74.7})
        result = analyze_sessions(sessions, 400, 0.324)
        self.assertEqual(result["adjust_percent"], 0.0)
        self.assertEqual(result["recommended_sensitivity"], 0.324)

    def test_single_battery_is_provisional(self) -> None:
        micro = _shot()
        sessions = _battery(0.35, "only", micro, {"lag": 50, "tot": 70})
        result = analyze_sessions(sessions, 800, 0.35)
        self.assertFalse(result["validated"])
        self.assertEqual(result["batteries"], 1)
        self.assertIn("provisória", result["report"])


if __name__ == "__main__":
    unittest.main()
