from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from openGA.ocr import OCRLine, OCRMatchError, ocr_image


_LOADING_KEYWORDS = (
    "检查更新",
    "正在加载",
    "加载中",
    "启动中",
    "初始化",
    "请稍候",
    "loading",
    "checking for updates",
    "starting",
    "please wait",
)
_CONTENT_KEYWORDS = (
    "推荐",
    "排行",
    "排行榜",
    "应用分类",
    "分类",
    "搜索",
    "查看详情",
    "安装",
    "更新",
    "设置",
    "帮助",
    "关于",
)
_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+){1,4}$", re.IGNORECASE)


def semantic_rejection_reasons(
    image_path: str | Path,
    *,
    ocr_backend: str = "auto",
    ocr_python: str = "",
) -> tuple[str, ...]:
    normalized_backend = str(ocr_backend).strip().lower() or "auto"
    if normalized_backend == "off":
        return ()
    if normalized_backend not in {"auto", "rapidocr"}:
        return (f"unsupported OCR backend for semantic review: {ocr_backend}",)
    try:
        lines = ocr_image(image_path, python_executable=ocr_python)
    except OCRMatchError:
        return ()
    return semantic_rejection_reasons_from_lines(lines)


def semantic_text_signature(
    image_path: str | Path,
    *,
    ocr_backend: str = "auto",
    ocr_python: str = "",
) -> str:
    normalized_backend = str(ocr_backend).strip().lower() or "auto"
    if normalized_backend == "off":
        return ""
    if normalized_backend not in {"auto", "rapidocr"}:
        return ""
    try:
        lines = ocr_image(image_path, python_executable=ocr_python)
    except OCRMatchError:
        return ""
    return semantic_text_signature_from_lines(lines)


def semantic_rejection_reasons_from_lines(
    lines: tuple[OCRLine, ...] | list[OCRLine],
) -> tuple[str, ...]:
    texts = [str(line.text).strip() for line in lines if str(line.text).strip()]
    if not texts:
        return ()

    normalized_text = " ".join(texts).lower()
    reasons: list[str] = []

    for keyword in _LOADING_KEYWORDS:
        if keyword in normalized_text:
            reasons.append(f"startup or loading screen detected by OCR: {keyword}")
            break

    has_version_only = any(_VERSION_RE.fullmatch(text) for text in texts)
    non_loading_texts = [
        text
        for text in texts
        if not any(keyword in text.lower() for keyword in _LOADING_KEYWORDS)
    ]
    non_loading_joined = " ".join(non_loading_texts).lower()
    has_content_keyword = any(keyword in non_loading_joined for keyword in _CONTENT_KEYWORDS)
    if len(texts) <= 4 and has_version_only and not has_content_keyword:
        reasons.append("splash screen detected by OCR")

    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return tuple(deduped)


def semantic_text_signature_from_lines(
    lines: tuple[OCRLine, ...] | list[OCRLine],
) -> str:
    texts = [_normalize_text_for_signature(str(line.text)) for line in lines]
    normalized = [text for text in texts if text]
    return "\n".join(normalized)


def semantic_similarity(left: str, right: str) -> float:
    if not left.strip() or not right.strip():
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _normalize_text_for_signature(value: str) -> str:
    text = re.sub(r"\s+", "", str(value).strip().lower())
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "", text)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", "", text)
    return text
