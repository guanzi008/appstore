import unittest

from appstore.capture_steps import CaptureStep, default_capture_steps, parse_capture_step, parse_capture_steps


class CaptureStepsTests(unittest.TestCase):
    def test_parse_capture_steps_uses_default_when_empty(self) -> None:
        self.assertEqual(parse_capture_steps(()), default_capture_steps())

    def test_parse_click_step_extracts_coordinates(self) -> None:
        step = parse_capture_step("click:120,340")
        self.assertEqual(step, CaptureStep(action="click", x=120, y=340))

    def test_parse_key_step_preserves_value(self) -> None:
        step = parse_capture_step("key:ctrl+comma")
        self.assertEqual(step, CaptureStep(action="key", value="ctrl+comma"))

    def test_parse_click_text_step_preserves_value(self) -> None:
        step = parse_capture_step("click-text:排行榜")
        self.assertEqual(step, CaptureStep(action="click-text", value="排行榜"))

    def test_parse_invalid_step_rejects_unknown_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported capture step"):
            parse_capture_step("drag:1,2")


if __name__ == "__main__":
    unittest.main()
