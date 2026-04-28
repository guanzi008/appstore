from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


DEFAULT_MIN_SCORE = 0.35


class OCRMatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRLine:
    text: str
    score: float
    box: tuple[tuple[float, float], ...]

    @property
    def center(self) -> tuple[int, int]:
        if not self.box:
            return (0, 0)
        xs = [point[0] for point in self.box]
        ys = [point[1] for point in self.box]
        return (int(round(sum(xs) / len(xs))), int(round(sum(ys) / len(ys))))


def default_ocr_python() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    venv_python = repo_root / ".venv-ocr" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def ocr_image(
    image_path: str | Path,
    *,
    python_executable: str = "",
) -> tuple[OCRLine, ...]:
    target = Path(image_path).expanduser().resolve()
    if not target.exists():
        raise OCRMatchError(f"ocr image not found: {target}")

    runtime = Path(__file__).resolve().with_name("ocr_runtime.py")
    python_bin = (python_executable or default_ocr_python()).strip()
    completed = subprocess.run(
        [python_bin, str(runtime), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr_text = (completed.stderr or "").strip()
        raise OCRMatchError(stderr_text or f"ocr runtime failed with exit code {completed.returncode}")

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise OCRMatchError(f"invalid ocr runtime output: {completed.stdout}") from exc
    return tuple(_parse_ocr_lines(payload))


def find_text_match(
    image_path: str | Path,
    *,
    target_text: str,
    python_executable: str = "",
    min_score: float = DEFAULT_MIN_SCORE,
) -> OCRLine:
    normalized_target = _normalize_text(target_text)
    if not normalized_target:
        raise OCRMatchError("target_text is required for OCR matching")

    lines = ocr_image(image_path, python_executable=python_executable)
    return select_best_text_match(lines, target_text=target_text, min_score=min_score)


def select_best_text_match(
    lines: tuple[OCRLine, ...] | list[OCRLine],
    *,
    target_text: str,
    min_score: float = DEFAULT_MIN_SCORE,
) -> OCRLine:
    normalized_target = _normalize_text(target_text)
    if not normalized_target:
        raise OCRMatchError("target_text is required for OCR matching")

    best_line: OCRLine | None = None
    best_rank = (-1.0, -1.0)
    for line in lines:
        normalized_line = _normalize_text(line.text)
        if not normalized_line:
            continue
        ratio = SequenceMatcher(None, normalized_target, normalized_line).ratio()
        contains = normalized_target in normalized_line or normalized_line in normalized_target
        rank = (
            1.0 if contains else ratio,
            float(line.score),
        )
        if rank <= best_rank:
            continue
        best_rank = rank
        best_line = line

    if best_line is None:
        raise OCRMatchError(f"ocr text not found: {target_text}")
    if best_rank[0] < min_score:
        raise OCRMatchError(
            f"ocr text match below minimum score for {target_text}: match={best_rank[0]:.2f}, line={best_line.score:.2f}"
        )
    return best_line


def _parse_ocr_lines(payload: dict) -> list[OCRLine]:
    items = payload.get("lines")
    if not isinstance(items, list):
        raise OCRMatchError(f"unexpected ocr payload: {payload}")

    lines: list[OCRLine] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        score = float(item.get("score", 0.0) or 0.0)
        points: list[tuple[float, float]] = []
        for point in item.get("box", []) or []:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            points.append((float(point[0]), float(point[1])))
        if not points:
            continue
        lines.append(OCRLine(text=text, score=score, box=tuple(points)))
    return lines


def _normalize_text(value: str) -> str:
    return "".join(str(value).strip().lower().split())
