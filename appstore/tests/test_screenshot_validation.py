import unittest
from pathlib import Path

from appstore.screenshot_validation import (
    ScreenshotAnalysis,
    validate_screenshot_analyses,
)


class ScreenshotValidationTests(unittest.TestCase):
    def test_validate_screenshot_analyses_rejects_low_information_and_duplicates(self) -> None:
        analyses = (
            ScreenshotAnalysis(
                path=Path("/tmp/01-home.png"),
                width=1280,
                height=720,
                file_size=20000,
                sha256="hash-a",
                gray_stddev=22.0,
                unique_gray_levels=64,
            ),
            ScreenshotAnalysis(
                path=Path("/tmp/02-blank.png"),
                width=1280,
                height=720,
                file_size=500,
                sha256="hash-b",
                gray_stddev=0.2,
                unique_gray_levels=2,
            ),
            ScreenshotAnalysis(
                path=Path("/tmp/03-dup.png"),
                width=1280,
                height=720,
                file_size=20000,
                sha256="hash-a",
                gray_stddev=22.0,
                unique_gray_levels=64,
            ),
        )

        report = validate_screenshot_analyses(analyses)

        self.assertEqual(report.accepted_paths, (Path("/tmp/01-home.png"),))
        self.assertEqual(
            report.rejected_paths,
            (Path("/tmp/02-blank.png"), Path("/tmp/03-dup.png")),
        )
        self.assertIn("file size below minimum", report.items[1].reasons[0])
        self.assertIn("duplicate of 01-home.png", report.items[2].reasons[-1])

    def test_validate_screenshot_analyses_rejects_small_resolution(self) -> None:
        report = validate_screenshot_analyses(
            (
                ScreenshotAnalysis(
                    path=Path("/tmp/tiny.png"),
                    width=320,
                    height=200,
                    file_size=10000,
                    sha256="hash-c",
                    gray_stddev=12.0,
                    unique_gray_levels=32,
                ),
            )
        )

        self.assertEqual(report.accepted_paths, ())
        self.assertEqual(report.rejected_paths, (Path("/tmp/tiny.png"),))
        self.assertIn("resolution below minimum", report.items[0].reasons[0])

    def test_validate_screenshot_analyses_appends_semantic_rejection_reasons(self) -> None:
        report = validate_screenshot_analyses(
            (
                ScreenshotAnalysis(
                    path=Path("/tmp/loading.png"),
                    width=1280,
                    height=720,
                    file_size=20000,
                    sha256="hash-d",
                    gray_stddev=22.0,
                    unique_gray_levels=64,
                ),
            ),
            semantic_validator=lambda _path: ("startup or loading screen detected by OCR: 检查更新",),
        )

        self.assertEqual(report.accepted_paths, ())
        self.assertEqual(report.rejected_paths, (Path("/tmp/loading.png"),))
        self.assertIn("startup or loading screen detected by OCR", report.items[0].reasons[-1])


if __name__ == "__main__":
    unittest.main()
