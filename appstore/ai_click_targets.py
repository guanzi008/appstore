from __future__ import annotations

import json
from dataclasses import dataclass

import requests


SYSTEM_PROMPT = """You choose the next GUI exploration actions from the full OCR scene of a Linux desktop app.
Return strict JSON only in the format {"actions":[{"type":"click","element_id":"node-03"}]}.
Rules:
- Base the decision on the full scene elements provided, not on predefined page labels or curated candidates.
- Choose only element_id values that exist in the provided scene.
- Prefer actions likely to open a meaningfully different page, workflow state, detail view, tab, category, or filtered result.
- Avoid destructive or dangerous actions such as install, uninstall, delete, remove, submit, pay, purchase, confirm, or download.
- Avoid elements already tried.
- Avoid repeating the same interface when accepted screenshots or rejected reasons imply the page did not really change.
- Return 1 to max_actions click actions, in priority order.
"""


@dataclass(frozen=True)
class AIClickTargetsConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 120.0


def choose_click_targets(
    *,
    package_name: str,
    app_name: str,
    visible_texts: tuple[str, ...] | list[str],
    scene_elements: tuple[str, ...] | list[str],
    accepted_texts: tuple[str, ...] | list[str],
    rejected_reasons: tuple[str, ...] | list[str],
    tried_targets: tuple[str, ...] | list[str],
    max_targets: int,
    config: AIClickTargetsConfig,
    session: requests.Session | None = None,
) -> tuple[str, ...]:
    elements = [str(item).strip() for item in scene_elements if str(item).strip()]
    if not elements:
        return ()
    if not config.base_url.strip():
        raise ValueError("ai click target base_url is required")
    if not config.model.strip():
        raise ValueError("ai click target model is required")

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Package name: {package_name}\n"
                    f"App name: {app_name or package_name}\n"
                    f"Visible OCR texts: {_joined(visible_texts)}\n"
                    f"Scene elements:\n{_joined_lines(elements)}\n"
                    f"Accepted screenshot OCR summaries: {_joined_lines(accepted_texts)}\n"
                    f"Rejected reasons: {_joined_lines(rejected_reasons)}\n"
                    f"Already tried element ids or texts: {_joined(tried_targets)}\n"
                    f"max_actions: {max(1, int(max_targets))}\n"
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
    return _parse_targets(body, allowed_ids=_allowed_ids(elements), max_targets=max_targets)


def _parse_targets(
    body: dict,
    *,
    allowed_ids: tuple[str, ...],
    max_targets: int,
) -> tuple[str, ...]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"unexpected ai click target response: {body}")
    message = choices[0].get("message") or {}
    content = _message_text(message.get("content"))
    if not content:
        raise ValueError(f"ai click target response missing content: {body}")
    payload = _parse_json_payload(content)
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise ValueError(f"ai click target response missing actions: {payload}")

    allowed = {value.strip() for value in allowed_ids if value.strip()}
    result: list[str] = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).strip().lower() != "click":
            continue
        element_id = str(item.get("element_id", "")).strip()
        if not element_id or element_id not in allowed or element_id in result:
            continue
        result.append(element_id)
        if len(result) >= max(1, int(max_targets)):
            break
    return tuple(result)


def _allowed_ids(scene_elements: list[str]) -> tuple[str, ...]:
    ids: list[str] = []
    for item in scene_elements:
        head, separator, _tail = item.partition(" ")
        element_id = head.strip()
        if separator and element_id.startswith("node-"):
            ids.append(element_id)
    return tuple(ids)


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
        raise ValueError(f"ai click target response must be a JSON object: {payload}")
    return payload


def _joined(values) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        return "none"
    return " | ".join(items[:60])


def _joined_lines(values) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        return "none"
    return "\n".join(items[:80])
