import json
import unittest

from build_demo import ZONE as DEMO_ZONE, demo_frames
from mine import ROOT, Visit, find_visits, gate_crossing, mine, speed_drop

ZONE = {"x": 0, "y": 0, "radius": 1}
RULES = {"max_gap_s": 5, "pet_s": 2, "max_distance_m": 5, "visit_margin_s": 1}


class RoundaboutTest(unittest.TestCase):
    def test_circle_crossings_are_interpolated(self):
        frames = {0.0: {1: (-2, 0)}, 0.1: {1: (0, 0)}, 0.2: {1: (2, 0)}}
        [visit] = find_visits(frames, {1: "Car"}, ZONE, 0.12)
        self.assertAlmostEqual(visit.enter, 0.05)
        self.assertAlmostEqual(visit.leave, 0.15)

    def test_partial_passage_or_missing_frames_are_excluded(self):
        frames = {0.0: {1: (0, 0), 2: (-2, 0)}, 0.1: {1: (2, 0), 2: (0, 0)},
                  0.5: {2: (2, 0)}}
        self.assertEqual(find_visits(frames, {1: "Car", 2: "Car"}, ZONE, 0.12), [])

    def test_overlap_and_sequential_passage_have_different_results(self):
        frames = {0.0: {1: (0, 0), 2: (0, 1), 3: (9, 0)}}
        events = mine(frames, [Visit(1, 0, 2), Visit(2, 1, 1.5), Visit(3, 3, 4)], RULES)
        overlap, sequential = events[:2]
        self.assertIsNone(overlap["pet_s"])
        self.assertEqual(overlap["overlap_s"], 0.5)
        self.assertEqual(overlap["status"], "distance")
        self.assertEqual(sequential["pet_s"], 1)
        self.assertEqual(sequential["status"], "pet")

    def test_invalid_distance_is_visible_when_observations_never_coincide(self):
        frames = {0.0: {1: (0, 0)}, 2.0: {2: (0, 0)}}
        [event] = mine(frames, [Visit(1, 0, 1), Visit(2, 2, 3)], RULES)
        self.assertEqual(event["shared_frames"], 0)
        self.assertIsNone(event["distance_m"])
        self.assertEqual(event["status"], "pet")

    def test_gate_requires_correct_direction_and_crossing_the_finite_segment(self):
        gate = ((-1, 0), (1, 0))
        self.assertAlmostEqual(gate_crossing((0, 0, 1), (1, 0, -1), gate), 0.5)
        self.assertIsNone(gate_crossing((0, 0, -1), (1, 0, 1), gate))
        self.assertIsNone(gate_crossing((0, 2, 1), (1, 2, -1), gate))

    def test_gate_evidence_does_not_cross_an_observation_gap(self):
        zone = {**ZONE, "approach": {"lookback_s": 15, "gates": {
            "north_entry": ((-1, 3), (1, 3)),
        }}}
        frames = {0.0: {1: (0, 4)}, 0.1: {1: (0, 2)}, 0.2: {1: (0, 0)},
                  0.3: {1: (0, -2)}}
        [visit] = find_visits(frames, {1: "Car"}, zone, 0.12)
        self.assertEqual(visit.approach, "north_entry")
        self.assertAlmostEqual(visit.gate_time, 0.05)
        point = frames.pop(0.1)
        frames = {0.0: frames[0.0], 0.1: point, 1.0: {1: (0, 2)},
                  1.1: {1: (0, 0)}, 1.2: {1: (0, -2)}}
        [visit] = find_visits(frames, {1: "Car"}, zone, 0.12)
        self.assertIsNone(visit.approach)

    def test_merging_time_rule_requires_both_known_and_different_approaches(self):
        frames = {0.0: {}}
        visits = [Visit(1, 0, 1, "north_entry"), Visit(2, 2, 3, "circulating"),
                  Visit(3, 4, 5)]
        events = mine(frames, visits, RULES)
        self.assertTrue(events[0]["merging_time_hit"])
        self.assertEqual(events[1]["path_label"], "unknown")
        self.assertFalse(events[2]["merging_time_hit"])

    def test_speed_drop_interpolates_start_of_exact_window(self):
        samples = [(0.0, 5.0), (0.4, 4.0), (0.9, 3.0), (1.2, 2.0)]
        best = speed_drop(samples, 1.0, 0.55)
        self.assertAlmostEqual(best["start_s"], 0.2)
        self.assertAlmostEqual(best["drop_mps"], 2.5)

    def test_speed_drop_requires_one_uninterrupted_interval(self):
        samples = [(0.0, 8.0), (0.1, 8.0), (1.1, 1.0), (1.2, 1.0)]
        self.assertIsNone(speed_drop(samples, 1.0, 0.12))
    def test_portfolio_demo_distinguishes_three_cases(self):
        rules = json.loads((ROOT / "zones.json").read_text(encoding="utf-8"))["rules"]
        frames, classes = demo_frames()
        visits = find_visits(frames, classes, DEMO_ZONE, rules["max_frame_gap_s"])
        first, second, third = mine(frames, visits, rules)
        self.assertEqual(first["path_label"], "same_approach")
        self.assertTrue(first["pet_hit"])
        self.assertFalse(first["merge_review_hit"])
        self.assertTrue(second["merging_time_hit"])
        self.assertFalse(second["speed_review_hit"])
        self.assertFalse(third["merging_time_hit"])
        self.assertTrue(third["speed_review_hit"])
        self.assertTrue(third["merge_review_hit"])


if __name__ == "__main__":
    unittest.main()
