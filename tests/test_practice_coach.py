import unittest

from practice_coach import PracticeCoach


class PracticeCoachTests(unittest.TestCase):
    def context(self, **updates):
        value = {
            "piece": "lesson:right_five",
            "segment": 0,
            "accuracy": 1.0,
            "mistakes": 0,
            "streak": 2,
            "tempo": 60,
            "hint_used": True,
            "duration": 20,
            "is_beginner": True,
        }
        value.update(updates)
        return value

    def test_mastery_rises_after_clean_attempt_and_drops_with_errors(self):
        coach = PracticeCoach()
        clean = coach.record_attempt(self.context())
        after_clean = clean["mastery"]
        self.assertGreater(after_clean, 0.25)
        coach.record_attempt(self.context(accuracy=0.25, mistakes=4, streak=0))
        self.assertLess(coach.mastery["lesson:right_five::0"], after_clean)

    def test_q_values_update_and_round_trip(self):
        coach = PracticeCoach()
        result = coach.record_attempt(self.context(accuracy=0.5, mistakes=2, streak=0))
        state = result["recorded"]
        self.assertTrue(coach.q_values)
        restored = PracticeCoach(coach.to_dict())
        self.assertEqual(restored.mastery, coach.mastery)
        self.assertEqual(restored.history, coach.history)
        self.assertEqual(restored.last_actions, coach.last_actions)
        self.assertEqual(restored.q_values, coach.q_values)
        self.assertEqual(state["item"], "lesson:right_five::0")

    def test_history_has_a_configurable_upper_bound(self):
        coach = PracticeCoach({"history_limit": 3})
        for index in range(10):
            coach.record_attempt(self.context(segment=index))
        self.assertEqual(len(coach.history), 3)
        self.assertEqual(coach.to_dict()["history_limit"], 3)

    def test_low_accuracy_recommends_a_safe_action(self):
        coach = PracticeCoach()
        recommendation = coach.recommend(self.context(accuracy=0.2, mistakes=3, tempo=60))
        self.assertIn(recommendation["action"], {"slow_down", "hints", "repeat"})
        self.assertNotEqual(recommendation["action"], "advance")

    def test_stable_beginner_attempt_can_recommend_next_segment(self):
        coach = PracticeCoach()
        context = self.context()
        for _ in range(5):
            coach.record_attempt(context)
        recommendation = coach.recommend(context)
        self.assertEqual(recommendation["action"], "advance")


if __name__ == "__main__":
    unittest.main()
