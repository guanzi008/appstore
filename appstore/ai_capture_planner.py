from __future__ import annotations

import json
from dataclasses import dataclass

import requests


SYSTEM_PROMPT = """You plan deterministic GUI capture steps for a Linux desktop app.
Return strict JSON only in the format {"steps":["wait-window:30","screenshot:screen-01"]}.
Allowed step formats:
- wait-window[:seconds]
- activate
- sleep[:seconds]
- screenshot[:label]
- key:<xdotool key sequence>
- type:<text>
- click:<x>,<y>
- click-text:<visible text in the UI>
Rules:
- include 2 to 20 steps
- include at least one screenshot step
- prefer stable shortcuts such as Ctrl+, F1 or Alt+<key> over brittle mouse clicks
- prefer click-text for visible labels over raw click coordinates when a labeled button or tab is present
- when multiple screenshots are required, include multiple screenshot steps with distinct labels
- avoid repeating pages that were already captured or rejected as duplicates
- do not include explanations or markdown
"""


@dataclass(frozen=True)
class AICapturePlannerConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 120.0


def plan_capture_steps(
    *,
    prompt: str,
    package_name: str,
    app_name: str,
    current_visible_texts: tuple[str, ...] | list[str] = (),
    clickable_texts: tuple[str, ...] | list[str] = (),
    config: AICapturePlannerConfig,
    session: requests.Session | None = None,
) -> tuple[str, ...]:
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise ValueError("ai planner prompt is required")
    if not config.base_url.strip():
        raise ValueError("ai planner base_url is required")
    if not config.model.strip():
        raise ValueError("ai planner model is required")

    request_session = session or requests.Session()
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Package name: {package_name}\n"
                    f"App name: {app_name or package_name}\n"
                    f"Current visible OCR texts: {_joined_context(current_visible_texts)}\n"
                    f"Clickable OCR targets: {_joined_context(clickable_texts)}\n"
                    f"Goal: {normalized_prompt}\n"
                    "Return JSON only."
                ),
            },
        ],
    }
    headers = {"Content-Type": "application/json"}
    if config.api_key.strip():
        headers["Authorization"] = f"Bearer {config.api_key.strip()}"

    response = request_session.post(url, json=payload, headers=headers, timeout=config.timeout)
    response.raise_for_status()
    body = response.json()
    steps = _extract_steps_from_completion(body)
    if not steps:
        raise ValueError("ai planner returned no steps")
    return steps


def _extract_steps_from_completion(body: dict) -> tuple[str, ...]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"unexpected ai planner response: {body}")
    message = choices[0].get("message") or {}
    content = _message_text(message.get("content"))
    if not content:
        raise ValueError(f"ai planner response missing content: {body}")

    payload = _parse_json_payload(content)
    steps = payload.get("steps")
    if not isinstance(steps, list):
        raise ValueError(f"ai planner response missing steps: {payload}")

    normalized_steps = tuple(str(step).strip() for step in steps if str(step).strip())
    if not normalized_steps:
        raise ValueError(f"ai planner response missing steps: {payload}")
    return normalized_steps


def default_capture_prompt(
    *,
    package_name: str,
    app_name: str,
    min_screenshots: int,
    max_screenshots: int,
) -> str:
    min_count = max(1, int(min_screenshots))
    max_count = max(min_count, int(max_screenshots))
    return (
        f"Plan steps to capture {min_count} to {max_count} distinct useful screenshots for the Linux app "
        f"{app_name or package_name}. Start from the current main page, then navigate to clearly different views by "
        "using visible navigation text, sidebars, tabs, search results, detail entries, or other clickable UI areas. "
        "Prefer click-text for visible UI labels and use keyboard shortcuts only as support. Use neutral screenshot "
        "labels such as screen-02, screen-03, and so on. Include one screenshot "
        "step for each useful page and avoid duplicates."
    )


def retry_capture_prompt(
    *,
    package_name: str,
    app_name: str,
    min_screenshots: int,
    max_screenshots: int,
    accepted_labels: tuple[str, ...] | list[str],
    rejected_reasons: tuple[str, ...] | list[str],
) -> str:
    accepted_text = ", ".join(str(label) for label in accepted_labels if str(label).strip()) or "none"
    rejected_text = "; ".join(str(reason) for reason in rejected_reasons if str(reason).strip()) or "none"
    min_count = max(1, int(min_screenshots))
    max_count = max(min_count, int(max_screenshots))
    return (
        f"The Linux app {app_name or package_name} still needs more screenshots. Need at least {min_count} total and "
        f"at most {max_count}. Already accepted labels: {accepted_text}. Rejected or duplicate results: {rejected_text}. "
        "Plan only additional steps from the current app state. Prefer simulated mouse clicks on visible UI text or "
        "other navigational controls, and use keyboard shortcuts only when they clearly help. Avoid repeating the "
        "same page. Use neutral screenshot labels such as screen-02 and screen-03. Include screenshot steps with new labels only."
    )


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
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"failed to parse ai planner response: {content}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"ai planner response must be a JSON object: {payload}")
    return payload


def _joined_context(values: tuple[str, ...] | list[str]) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        return "none"
    return " | ".join(items[:50])
