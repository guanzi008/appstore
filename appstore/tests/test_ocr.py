import unittest

from openGA.ocr import OCRLine, OCRMatchError, select_best_text_match


class OCRTests(unittest.TestCase):
    def test_select_best_text_match_prefers_exact_or_containing_text(self) -> None:
        lines = (
            OCRLine(text="应用分类", score=0.91, box=((10, 10), (110, 10), (110, 30), (10, 30))),
            OCRLine(text="排行榜", score=0.83, box=((20, 50), (120, 50), (120, 80), (20, 80))),
        )
        match = select_best_text_match(lines, target_text="排行", min_score=0.3)
        self.assertEqual(match.text, "排行榜")
        self.assertEqual(match.center, (70, 65))

    def test_select_best_text_match_raises_when_no_line_matches(self) -> None:
        lines = (
            OCRLine(text="首页", score=0.91, box=((10, 10), (110, 10), (110, 30), (10, 30))),
        )
        with self.assertRaises(OCRMatchError):
            select_best_text_match(lines, target_text="设置", min_score=0.5)


if __name__ == "__main__":
    unittest.main()
