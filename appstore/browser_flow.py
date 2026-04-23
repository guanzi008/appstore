from __future__ import annotations

import asyncio
import time
from pathlib import Path


async def text_of(page, element) -> str:
    return await page.evaluate("(el) => (el && el.innerText) ? el.innerText.trim() : ''", element)


async def info_of(page, element):
    return await page.evaluate(
        """(el) => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return {
              text: (el.innerText || '').trim(),
              width: r.width,
              height: r.height,
              top: r.top,
              left: r.left,
              display: s.display,
              visibility: s.visibility,
              opacity: s.opacity,
              disabled: !!el.disabled
            };
        }""",
        element,
    )


async def click_button(page, label: str) -> None:
    matches = []
    for button in await page.querySelectorAll("button"):
        info = await info_of(page, button)
        if info["text"] != label:
            continue
        if info["width"] <= 0 or info["height"] <= 0:
            continue
        if info["display"] == "none" or info["visibility"] == "hidden" or info["opacity"] == "0":
            continue
        if info["disabled"]:
            continue
        matches.append((button, info))
    if not matches:
        raise RuntimeError(f"button not found: {label}")
    button, _info = sorted(matches, key=lambda item: (item[1]["top"], item[1]["left"]))[-1]
    await page.evaluate("(el) => el.scrollIntoView({block: 'center'})", button)
    await page.waitFor(500)
    await button.click()


async def wait_system_dialog(page, timeout: int = 12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = []
        for dialog in await page.querySelectorAll(".el-dialog"):
            info = await info_of(page, dialog)
            if info["width"] <= 0 or info["height"] <= 0:
                continue
            if info["display"] == "none" or info["visibility"] == "hidden" or info["opacity"] == "0":
                continue
            text = await text_of(page, dialog)
            if "系统版本" in text:
                matches.append((dialog, info))
        if matches:
            return sorted(matches, key=lambda item: (item[1]["top"], item[1]["left"]))[-1][0]
        await page.waitFor(500)
    raise RuntimeError("system dialog not found")


async def click_system_dialog_primary(page) -> None:
    dialog = await wait_system_dialog(page)
    matches = []
    for button in await dialog.querySelectorAll(".el-dialog__footer .el-button--primary"):
        info = await info_of(page, button)
        if info["width"] <= 0 or info["height"] <= 0:
            continue
        if info["display"] == "none" or info["visibility"] == "hidden" or info["opacity"] == "0":
            continue
        if info["disabled"]:
            continue
        matches.append((button, info))
    if not matches:
        raise RuntimeError("system dialog primary not found")
    button, _info = sorted(matches, key=lambda item: (item[1]["top"], item[1]["left"]))[-1]
    await button.click()


async def maybe_click_any_dialog_primary(page) -> bool:
    matches = []
    for button in await page.querySelectorAll(".el-dialog__footer .el-button--primary"):
        info = await info_of(page, button)
        if info["width"] <= 0 or info["height"] <= 0:
            continue
        if info["display"] == "none" or info["visibility"] == "hidden" or info["opacity"] == "0":
            continue
        if info["disabled"]:
            continue
        matches.append((button, info))
    if not matches:
        return False
    button, _info = sorted(matches, key=lambda item: (item[1]["top"], item[1]["left"]))[-1]
    await button.click()
    return True


async def find_row(page, keywords: list[str]):
    normalized = [keyword.upper() for keyword in keywords]
    for row in await page.querySelectorAll("tr"):
        text = (await text_of(page, row)).upper()
        if text and any(keyword in text for keyword in normalized):
            return row, text
    return None, ""


async def wait_row(page, keywords: list[str], predicate, timeout: int = 180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row, text = await find_row(page, keywords)
        if row and predicate(text):
            return row, text
        await page.waitFor(3000)
    raise TimeoutError(f"timed out waiting for row: {keywords}")


async def set_new_version_intro(page, text: str) -> None:
    textareas = await page.querySelectorAll("textarea")
    if len(textareas) < 3:
        return
    field = textareas[2]
    await page.evaluate("(el) => el.scrollIntoView({block: 'center'})", field)
    await page.waitFor(500)
    await field.click({"clickCount": 3})
    await field.press("Backspace")
    await field.type(text, {"delay": 15})


async def upload_package(page, package_path: str | Path, row_keywords: list[str]):
    inputs = await page.querySelectorAll("input[type=file]")
    if not inputs:
        raise RuntimeError("package upload input not found")
    await inputs[0].uploadFile(str(Path(package_path)))
    return await wait_row(page, row_keywords, lambda text: "100%" in text or "上传完成" in text, timeout=240)


async def open_system_dialog_for_row(page, row) -> None:
    for button in await row.querySelectorAll("button"):
        if await text_of(page, button) == "系统版本管理":
            await button.click()
            await wait_system_dialog(page)
            return
    raise RuntimeError("system version management button not found")


async def select_system_code(page, code: str) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        dialog = await wait_system_dialog(page)
        for checkbox in await dialog.querySelectorAll("input[type=checkbox]"):
            value = await page.evaluate("(el) => el.value || ''", checkbox)
            if value == code:
                checked = await page.evaluate("(el) => !!el.checked", checkbox)
                if not checked:
                    await page.evaluate("(el) => el.click()", checkbox)
                    await page.waitFor(400)
                return
        await page.waitFor(500)
    raise RuntimeError(f"system code not found: {code}")


async def save_and_wait_list(page, events: list[dict], start_idx: int) -> None:
    await click_button(page, "保存")
    deadline = time.time() + 90
    while time.time() < deadline:
        if any("/devprod-api/store-dev-app/app" in event.get("url", "") for event in events[start_idx:]):
            break
        await page.waitFor(1000)
    deadline = time.time() + 60
    while time.time() < deadline:
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if "应用列表" in body:
            return
        await page.waitFor(2000)
    raise TimeoutError("did not return to app list")


async def fetch_status_text(page, index_url: str, app_name: str, pkg_name: str) -> str:
    await page.goto(index_url, {"waitUntil": "networkidle2", "timeout": 120000})
    await page.waitFor(5000)
    for row in await page.querySelectorAll("tr"):
        text = await text_of(page, row)
        if app_name in text and pkg_name in text:
            return text
    return ""


def ensure_artifact_dir(path: Path | str) -> Path:
    artifact_dir = Path(path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


async def write_json(path: Path | str, payload: dict) -> None:
    target = Path(path)
    target.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
