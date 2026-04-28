import unittest

from openGA.ocr import OCRLine

from appstore.screenshot_semantics import semantic_rejection_reasons_from_lines


class ScreenshotSemanticsTests(unittest.TestCase):
    def test_semantic_rejection_reasons_detects_loading_splash(self) -> None:
        lines = (
            OCRLine(text="玲珑应用商店", score=0.99, box=((0, 0), (10, 0), (10, 10), (0, 10))),
            OCRLine(text="检查更新中...", score=0.95, box=((0, 20), (10, 20), (10, 30), (0, 30))),
            OCRLine(text="v3.3.0", score=0.98, box=((0, 40), (10, 40), (10, 50), (0, 50))),
        )

        reasons = semantic_rejection_reasons_from_lines(lines)

        self.assertIn("startup or loading screen detected by OCR: 检查更新", reasons)
        self.assertIn("splash screen detected by OCR", reasons)

    def test_semantic_rejection_reasons_accepts_content_page(self) -> None:
        lines = (
            OCRLine(text="推荐", score=0.99, box=((0, 0), (10, 0), (10, 10), (0, 10))),
            OCRLine(text="排行榜", score=0.98, box=((0, 20), (10, 20), (10, 30), (0, 30))),
            OCRLine(text="查看详情", score=0.97, box=((0, 40), (10, 40), (10, 50), (0, 50))),
        )

        reasons = semantic_rejection_reasons_from_lines(lines)

        self.assertEqual(reasons, ())


if __name__ == "__main__":
    unittest.main()
