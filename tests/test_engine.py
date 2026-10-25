import unittest
from ripwave_human.engine import RippleEngine, normalize_command, validate_event

class EngineTests(unittest.TestCase):
    def event(self, intent="start_ripple", **kwargs):
        payload={"intent":intent,"source":"text","confidence":1.0,"acoustics":{"rms_energy":0.2,"spectral_centroid_hz":1000}}
        payload.update(kwargs)
        return validate_event(payload)

    def test_start_activates(self):
        e=RippleEngine(); e.apply(self.event())
        self.assertTrue(e.snapshot()["active"])

    def test_expand_is_bounded(self):
        e=RippleEngine()
        for _ in range(30): e.apply(self.event("expand"))
        self.assertLessEqual(e.snapshot()["radius"],1.0)

    def test_stop_deactivates(self):
        e=RippleEngine(); e.apply(self.event("start_ripple")); e.apply(self.event("stop"))
        self.assertFalse(e.snapshot()["active"])

    def test_unknown_intent_rejected(self):
        with self.assertRaises(ValueError): self.event("launch")

    def test_low_confidence_rejected(self):
        with self.assertRaises(ValueError): self.event("expand", confidence=0.1)

    def test_neutral_is_valid(self):
        self.assertEqual(self.event("neutral")["intent"], "neutral")

    def test_voice_command_phrases_are_normalized_exactly(self):
        examples = {
            "Raise your right arm.": "raise_right_arm",
            "LOWER YOUR RIGHT ARM!": "lower_right_arm",
            "Reach forward": "reach_forward",
            "Start walking.": "walking",
            "Stop walking.": "stop",
            "Return to neutral": "neutral",
        }
        for phrase, expected in examples.items():
            with self.subTest(phrase=phrase): self.assertEqual(normalize_command(phrase), expected)

    def test_command_parser_does_not_use_substring_matching(self):
        self.assertIsNone(normalize_command("do not stop walking"))
        self.assertIsNone(normalize_command("maybe raise your right arm later"))

    def test_transcript_is_authoritatively_normalized(self):
        event = validate_event({"transcript": "Raise your right arm.", "source": "voice"})
        self.assertEqual(event["intent"], "raise_right_arm")
        self.assertEqual(event["transcript"], "Raise your right arm.")

    def test_transcript_intent_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "do not match"):
            validate_event({"transcript": "stop", "intent": "walking"})

    def test_unsupported_phrase_has_actionable_validation_error(self):
        with self.assertRaisesRegex(ValueError, "Command not recognized"):
            validate_event({"transcript": "dance around"})

if __name__ == "__main__": unittest.main()
