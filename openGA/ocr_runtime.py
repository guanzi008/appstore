from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: ocr_runtime.py <image_path>\n")
        return 2

    image_path = Path(args[0]).expanduser().resolve()
    if not image_path.exists():
        sys.stderr.write(f"ocr image not found: {image_path}\n")
        return 2

    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        sys.stderr.write(
            "rapidocr_onnxruntime is not installed in the OCR venv; "
            "install it under /home/hao/Documents/ai/appstore/.venv-ocr first.\n"
        )
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 3

    engine = RapidOCR()
    result, _elapsed = engine(str(image_path))
    lines: list[dict[str, object]] = []
    for item in result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box = item[0]
        text = str(item[1]).strip()
        score = float(item[2] or 0.0)
        if not text:
            continue
        normalized_box: list[list[float]] = []
        for point in box:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            normalized_box.append([float(point[0]), float(point[1])])
        if not normalized_box:
            continue
        lines.append(
            {
                "text": text,
                "score": score,
                "box": normalized_box,
            }
        )

    sys.stdout.write(json.dumps({"lines": lines}, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
