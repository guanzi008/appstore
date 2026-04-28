from __future__ import annotations

import json
from dataclasses import dataclass

import requests


SYSTEM_PROMPT = """You review OCR text extracted from Linux desktop app screenshots.
Return strict JSON only in the format {"useful":true,"page_kind":"home","reason":"..."}.
Rules:
- Reject loading, splash, update-progress, blank, or wrong-window screenshots.
- Reject screenshots that are effectively the same interface as an accepted screenshot, even if only banner items or carousel content changed.
- Accept screenshots that show a meaningfully different page, workflow state, or function area.
- Base the decision on the OCR text and accepted screenshot summaries, not on a preassigned label.
- Keep reason short and concrete.
"""


@dataclass(frozen=True)
class AICaptureReviewConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 120.0


@dataclass(frozen=True)
class CaptureReviewResult:
    useful: bool
    page_kind: str
    reason: str


def review_capture_text(
    *,
    package_name: str,
    app_name: str,
    current_text: str,
    accepted_texts: tuple[str, ...] | list[str],
    config: AICaptureReviewConfig,
    session: requests.Session | None = None,
) -> CaptureReviewResult:
    normalized_text = current_text.strip()
    if not normalized_text:
        raise ValueError("current_text is required for AI capture review")
    if not config.base_url.strip():
        raise ValueError("ai capture review base_url is required")
    if not config.model.strip():
        raise ValueError("ai capture review model is required")

    accepted_block = "\n\n".join(
        f"[accepted {index}]\n{text.strip()}"
        for index, text in enumerate(accepted_texts, start=1)
        if str(text).strip()
    ) or "none"
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Package name: {package_name}\n"
                    f"App name: {app_name or package_name}\n"
                    f"Accepted screenshot OCR summaries:\n{accepted_block}\n\n"
                    f"Current screenshot OCR summary:\n{normalized_text}\n\n"
                    "Return JSON only."
                ),
            },
        ],
    }
    headers = {"Content-Type": "application/json"}
    if config.api_key.strip():
        headers["Authorization"] = f"Bearer {config.api_key.strip()}"

    request_session = session or requests.Session()
    response = request_session.post(
        f"{config.base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers=headers,
        timeout=config.timeout,
    )
    response.raise_for_status()
    body = response.json()
    return _parse_review_result(body)


def _parse_review_result(body: dict) -> CaptureReviewResult:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"unexpected ai capture review response: {body}")
    message = choices[0].get("message") or {}
    content = _message_text(message.get("content"))
    if not content:
        raise ValueError(f"ai capture review response missing content: {body}")
    payload = _parse_json_payload(content)

    useful = bool(payload.get("useful"))
    page_kind = str(payload.get("page_kind", "")).strip() or "unknown"
    reason = str(payload.get("reason", "")).strip() or ("accepted" if useful else "rejected")
    return CaptureReviewResult(useful=useful, page_kind=page_kind, reason=reason)


def _message_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "output_text"}:
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _parse_json_payload(content: str) -> dict:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError(f"ai capture review response must be a JSON object: {payload}")
    return payload
