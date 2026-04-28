from __future__ import annotations

import json
from dataclasses import dataclass

import requests


LANGUAGE_LABELS: dict[str, str] = {
    "zh_CN": "中文（简体）",
    "en_US": "英文",
}


SYSTEM_PROMPT = """You translate Linux app store listing copy into the target language.
Return strict JSON only in the format:
{"name":"...","brief_info":"...","desc_info":"...","update_desc":"..."}
Rules:
- Keep product names, package names, brand names, version numbers, URLs, and code identifiers unchanged when appropriate.
- Translate naturally for an app store audience.
- brief_info should stay concise.
- desc_info can be full-length natural prose.
- If update_desc is empty, return it as an empty string.
- Do not add markdown or explanations.
"""


@dataclass(frozen=True)
class TranslationConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 120.0


def desired_languages_for_regions(region_codes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized = {str(code).strip() for code in region_codes if str(code).strip()}
    if "2" in normalized:
        return ("zh_CN", "en_US")
    return ("zh_CN",)


def translate_listing_texts(
    *,
    app_name_zh: str,
    short_desc_zh: str,
    full_desc_zh: str,
    update_desc_zh: str,
    target_lan: str,
    config: TranslationConfig,
    session: requests.Session | None = None,
) -> dict[str, str]:
    normalized_target = str(target_lan).strip()
    if normalized_target == "zh_CN":
        return {
            "name": app_name_zh.strip(),
            "brief_info": short_desc_zh.strip(),
            "desc_info": full_desc_zh.strip(),
            "update_desc": update_desc_zh.strip(),
        }
    if normalized_target != "en_US":
        raise ValueError(f"unsupported translation target language: {target_lan}")
    if not config.base_url.strip():
        raise ValueError("translation base_url is required")
    if not config.model.strip():
        raise ValueError("translation model is required")

    request_session = session or requests.Session()
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Source language: zh_CN\n"
                    "Target language: en_US\n"
                    f"Target language label: {LANGUAGE_LABELS.get(normalized_target, normalized_target)}\n"
                    f"name: {app_name_zh.strip()}\n"
                    f"brief_info: {short_desc_zh.strip()}\n"
                    f"desc_info: {full_desc_zh.strip()}\n"
                    f"update_desc: {update_desc_zh.strip()}\n"
                    "Return JSON only."
                ),
            },
        ],
    }
    headers = {"Content-Type": "application/json"}
    if config.api_key.strip():
        headers["Authorization"] = f"Bearer {config.api_key.strip()}"

    try:
        response = request_session.post(
            f"{config.base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
            timeout=config.timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            "无法连接英文文案生成服务。"
            f"当前地址：{config.base_url.rstrip('/')}/chat/completions 。"
            "请先启动对应 OpenAI 兼容接口，或检查 APPSTORE_AI_BASE_URL / APPSTORE_AI_MODEL / APPSTORE_AI_API_KEY 配置。"
        ) from exc
    response.raise_for_status()
    body = response.json()
    return _parse_translation(body)


def _parse_translation(body: dict) -> dict[str, str]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"unexpected translation response: {body}")
    message = choices[0].get("message") or {}
    content = _message_text(message.get("content"))
    if not content:
        raise ValueError(f"translation response missing content: {body}")
    payload = _parse_json_payload(content)
    return {
        "name": str(payload.get("name", "")).strip(),
        "brief_info": str(payload.get("brief_info", "")).strip(),
        "desc_info": str(payload.get("desc_info", "")).strip(),
        "update_desc": str(payload.get("update_desc", "")).strip(),
    }


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
        raise ValueError(f"translation response must be a JSON object: {payload}")
    return payload
