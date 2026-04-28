from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScreenshotAnalysis:
    path: Path
    width: int
    height: int
    file_size: int
    sha256: str
    gray_stddev: float
    unique_gray_levels: int


@dataclass(frozen=True)
class ScreenshotValidationItem:
    analysis: ScreenshotAnalysis
    accepted: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ScreenshotValidationReport:
    accepted_paths: tuple[Path, ...]
    rejected_paths: tuple[Path, ...]
    items: tuple[ScreenshotValidationItem, ...]


def validate_screenshot_paths(
    screenshot_paths: tuple[Path, ...] | list[Path],
    *,
    min_width: int = 640,
    min_height: int = 360,
    min_file_size: int = 4096,
    min_gray_stddev: float = 2.5,
    min_unique_gray_levels: int = 8,
    analyzer=None,
    semantic_validator=None,
) -> ScreenshotValidationReport:
    screenshot_paths = tuple(Path(path) for path in screenshot_paths)
    analysis_fn = analyze_screenshot if analyzer is None else analyzer

    analyses = [analysis_fn(path) for path in screenshot_paths]
    return validate_screenshot_analyses(
        analyses,
        min_width=min_width,
        min_height=min_height,
        min_file_size=min_file_size,
        min_gray_stddev=min_gray_stddev,
        min_unique_gray_levels=min_unique_gray_levels,
        semantic_validator=semantic_validator,
    )


def validate_screenshot_analyses(
    analyses: tuple[ScreenshotAnalysis, ...] | list[ScreenshotAnalysis],
    *,
    min_width: int = 640,
    min_height: int = 360,
    min_file_size: int = 4096,
    min_gray_stddev: float = 2.5,
    min_unique_gray_levels: int = 8,
    semantic_validator=None,
) -> ScreenshotValidationReport:
    analyses = tuple(analyses)
    seen_hashes: dict[str, Path] = {}
    items: list[ScreenshotValidationItem] = []
    accepted_paths: list[Path] = []
    rejected_paths: list[Path] = []

    for analysis in analyses:
        reasons: list[str] = []
        if analysis.width < min_width or analysis.height < min_height:
            reasons.append(f"resolution below minimum: {analysis.width}x{analysis.height}")
        if analysis.file_size < min_file_size:
            reasons.append(f"file size below minimum: {analysis.file_size}")
        if analysis.gray_stddev < min_gray_stddev:
            reasons.append(f"image variance below minimum: {analysis.gray_stddev:.2f}")
        if analysis.unique_gray_levels < min_unique_gray_levels:
            reasons.append(f"gray levels below minimum: {analysis.unique_gray_levels}")
        duplicate_of = seen_hashes.get(analysis.sha256)
        if duplicate_of is not None:
            reasons.append(f"duplicate of {duplicate_of.name}")
        if semantic_validator is not None:
            semantic_reasons = semantic_validator(analysis.path)
            for reason in semantic_reasons or ():
                text = str(reason).strip()
                if text:
                    reasons.append(text)

        accepted = not reasons
        if accepted:
            accepted_paths.append(analysis.path)
            seen_hashes[analysis.sha256] = analysis.path
        else:
            rejected_paths.append(analysis.path)
        items.append(
            ScreenshotValidationItem(
                analysis=analysis,
                accepted=accepted,
                reasons=tuple(reasons),
            )
        )

    return ScreenshotValidationReport(
        accepted_paths=tuple(accepted_paths),
        rejected_paths=tuple(rejected_paths),
        items=tuple(items),
    )


def analyze_screenshot(path: Path | str) -> ScreenshotAnalysis:
    target = Path(path)
    file_size = target.stat().st_size
    sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    width, height = _probe_dimensions(target)
    rgb_bytes = _decode_rgb24(target, width=width, height=height)
    gray_stddev, unique_gray_levels = _gray_statistics(rgb_bytes)
    return ScreenshotAnalysis(
        path=target,
        width=width,
        height=height,
        file_size=file_size,
        sha256=sha256,
        gray_stddev=gray_stddev,
        unique_gray_levels=unique_gray_levels,
    )


def validation_report_payload(report: ScreenshotValidationReport) -> dict:
    return {
        "accepted_paths": [str(path) for path in report.accepted_paths],
        "rejected_paths": [str(path) for path in report.rejected_paths],
        "items": [
            {
                "accepted": item.accepted,
                "reasons": list(item.reasons),
                "analysis": {
                    **asdict(item.analysis),
                    "path": str(item.analysis.path),
                },
            }
            for item in report.items
        ],
    }


def validation_report_json(report: ScreenshotValidationReport) -> str:
    return json.dumps(validation_report_payload(report), ensure_ascii=False, indent=2)


def _probe_dimensions(path: Path) -> tuple[int, int]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError(f"ffprobe returned no streams for screenshot: {path}")
    width = int(streams[0].get("width") or 0)
    height = int(streams[0].get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"invalid screenshot dimensions for {path}")
    return width, height


def _decode_rgb24(path: Path, *, width: int, height: int) -> bytes:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-frames:v",
            "1",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    rgb_bytes = completed.stdout
    expected_size = width * height * 3
    if len(rgb_bytes) != expected_size:
        raise RuntimeError(
            f"decoded screenshot size mismatch for {path}: expected {expected_size}, got {len(rgb_bytes)}"
        )
    return rgb_bytes


def _gray_statistics(rgb_bytes: bytes) -> tuple[float, int]:
    if not rgb_bytes:
        return 0.0, 0

    sample_limit = 4096
    pixel_count = len(rgb_bytes) // 3
    stride = max(1, pixel_count // sample_limit)

    gray_values: list[int] = []
    for pixel_index in range(0, pixel_count, stride):
        offset = pixel_index * 3
        red = rgb_bytes[offset]
        green = rgb_bytes[offset + 1]
        blue = rgb_bytes[offset + 2]
        gray = (299 * red + 587 * green + 114 * blue) // 1000
        gray_values.append(gray)

    if not gray_values:
        return 0.0, 0
    mean = sum(gray_values) / len(gray_values)
    variance = sum((value - mean) ** 2 for value in gray_values) / len(gray_values)
    return math.sqrt(variance), len(set(gray_values))
