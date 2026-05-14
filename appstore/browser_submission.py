from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from appstore.browser_flow import (
    click_button,
    click_system_dialog_primary,
    fetch_status_text,
    maybe_click_any_dialog_primary,
    open_system_dialog_for_row,
    save_and_wait_list,
    select_system_code,
    set_new_version_intro,
    upload_package,
    wait_row,
)
from appstore.browser_runtime import (
    capture_browser_session_state,
    restore_session_state,
    session_is_valid,
    wait_for_logged_in_portal_state,
)
from appstore.platform_policy import resolve_target_system_line
from appstore.pyppeteer_runtime import PYPPETEER_LAUNCH_OPTIONS, launch
from appstore.session_state import SessionStateStore


ORIGIN = "https://appstore-dev.uniontech.com"
INDEX_URL = f"{ORIGIN}/#/index"


@dataclass(frozen=True)
class BrowserStep:
    action: str
    payload: dict


@dataclass(frozen=True)
class BrowserReleasePlan:
    steps: list[BrowserStep]


def arch_row_keywords(arch: str) -> list[str]:
    normalized = arch.strip().lower()
    if normalized in {"arm64", "aarch64"}:
        return ["ARM"]
    if normalized in {"loong64", "loongarch64"}:
        return ["LOONG64", "LOONGARCH64", "LOONG"]
    if normalized in {"amd64", "x86_64"}:
        return ["X86", "AMD64"]
    return [normalized.upper()]


def build_release_browser_plan(*, release, packages, targets_by_package) -> BrowserReleasePlan:
    steps: list[BrowserStep] = [
        BrowserStep(action="open_release", payload={"release_key": release.release_key}),
        BrowserStep(action="set_intro", payload={"release_name": release.release_name, "note": release.note}),
    ]
    for index, package in enumerate(packages):
        row_keywords = arch_row_keywords(package.declared_arch or "")
        steps.append(
            BrowserStep(
                action="upload_package",
                payload={
                    "package_key": package.package_key,
                    "file_path": str(package.file_path),
                    "row_keywords": row_keywords,
                },
            )
        )
        for target in targets_by_package.get(package.package_key, ()):
            resolved_target = resolve_target_system_line(package=package, target=target)
            steps.append(
                BrowserStep(
                    action="configure_target",
                    payload={
                        "package_key": package.package_key,
                        "row_keywords": row_keywords,
                        "sup_sys_code": resolved_target.sup_sys_code,
                        "baseline_id": resolved_target.baseline_id,
                    },
                )
            )
        steps.append(BrowserStep(action="save_release", payload={"stage": index + 1}))
        if index != len(packages) - 1:
            steps.append(BrowserStep(action="reopen_release", payload={"release_key": release.release_key}))
    steps.append(BrowserStep(action="submit_release", payload={"release_key": release.release_key}))
    return BrowserReleasePlan(steps=steps)


@dataclass(frozen=True)
class BrowserSubmissionResult:
    app_id: str
    status_text: str
    artifact_dir: Path
    detail_id: str


class BrowserSubmissionRunner:
    def __init__(
        self,
        *,
        username: str,
        password: str,
        session_cache_dir: Path | str = "appstore/cache/session-state",
        headless: bool = True,
    ) -> None:
        self.username = username
        self.password = password
        self.session_store = SessionStateStore(session_cache_dir)
        self.headless = headless

    async def _ensure_logged_in(self, page) -> None:
        state = self.session_store.load(self.username)
        if state is not None:
            await restore_session_state(page, state)
        await page.goto(INDEX_URL, {"waitUntil": "networkidle2", "timeout": 120000})
        await page.waitFor(2000)
        if await session_is_valid(page):
            return
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if "重新登录" in body:
            for button in await page.querySelectorAll("button"):
                text = await page.evaluate("(el) => (el.innerText || '').trim()", button)
                if text == "重新登录":
                    await button.click()
                    await page.waitFor(2000)
                    break
        await page.waitForSelector("input", {"timeout": 120000})
        inputs = await page.querySelectorAll("input")
        await inputs[0].click({"clickCount": 3})
        await inputs[0].type(self.username, {"delay": 20})
        await inputs[1].click({"clickCount": 3})
        await inputs[1].type(self.password, {"delay": 20})
        for button in await page.querySelectorAll("button"):
            text = await page.evaluate("(el) => (el.innerText || '').trim()", button)
            if text in {"登录", "重新登录", "确定"}:
                await button.click()
                break
        await wait_for_logged_in_portal_state(page, timeout_ms=120000)
        refreshed_state = await capture_browser_session_state(page, account=self.username, timeout_ms=120000)
        self.session_store.save(refreshed_state)

    def _resolve_app_entry(self, *, client, app, target_app_id: str) -> dict:
        matches = client.find_apps_by_pkg_name(app.pkg_name)
        if not matches:
            raise RuntimeError(f"app not found in store list: {app.pkg_name}")
        if target_app_id:
            for match in matches:
                if str(match.get("app_id", "")) == str(target_app_id):
                    return match
        if len(matches) == 1:
            return matches[0]
        raise RuntimeError(f"ambiguous app matches for browser submission: {app.pkg_name}")

    def _build_update_url(self, app_entry: dict, target_app_id: str) -> str:
        detail_id = str(app_entry.get("id", "")).strip()
        if not detail_id:
            raise RuntimeError("store app row missing detail id")
        app_id = str(target_app_id or app_entry.get("app_id", "")).strip()
        if not app_id:
            raise RuntimeError("store app row missing app_id")
        return f"{ORIGIN}/#/management-detial?id={detail_id}&type=3&app_id={app_id}"

    def _intro_text(self, release, packages) -> str:
        note = (release.note or "").strip()
        if note:
            return note
        package_labels = "、".join(package.package_key for package in packages)
        return f"批量上传更新：{release.release_name}，涉及包 {package_labels}。"

    async def _execute_plan(
        self,
        *,
        page,
        plan: BrowserReleasePlan,
        update_url: str,
        intro_text: str,
        events: list[dict],
    ) -> None:
        await page.goto(update_url, {"waitUntil": "networkidle2", "timeout": 120000})
        await page.waitFor(5000)
        for step in plan.steps:
            if step.action == "open_release":
                continue
            if step.action == "set_intro":
                await set_new_version_intro(page, intro_text)
                continue
            if step.action == "upload_package":
                await upload_package(page, step.payload["file_path"], step.payload["row_keywords"])
                continue
            if step.action == "configure_target":
                row, _ = await wait_row(page, step.payload["row_keywords"], lambda text: "100%" in text or "上传完成" in text, timeout=60)
                await open_system_dialog_for_row(page, row)
                await select_system_code(page, step.payload["sup_sys_code"])
                await click_system_dialog_primary(page)
                await wait_row(
                    page,
                    step.payload["row_keywords"],
                    lambda text, code=step.payload["sup_sys_code"]: (
                        "社区版V23" in text if code == "11" else "社区版V25" in text if code == "21" else True
                    ),
                    timeout=120,
                )
                continue
            if step.action == "save_release":
                start_idx = len(events)
                await save_and_wait_list(page, events, start_idx)
                continue
            if step.action == "reopen_release":
                await page.goto(update_url, {"waitUntil": "networkidle2", "timeout": 120000})
                await page.waitFor(5000)
                continue
            if step.action == "submit_release":
                await click_button(page, "提交审核")
                await page.waitFor(1500)
                await maybe_click_any_dialog_primary(page)
                await page.waitFor(10000)
                continue
            raise RuntimeError(f"unsupported browser step: {step.action}")

    def submit_release_group(
        self,
        *,
        client,
        app,
        release,
        packages,
        targets_by_package,
        target_app_id: str,
        artifact_root: Path | str,
    ) -> BrowserSubmissionResult:
        artifact_dir = Path(artifact_root)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        app_entry = self._resolve_app_entry(client=client, app=app, target_app_id=target_app_id)
        update_url = self._build_update_url(app_entry, target_app_id)
        plan = build_release_browser_plan(release=release, packages=packages, targets_by_package=targets_by_package)

        async def _run() -> BrowserSubmissionResult:
            browser = await launch(headless=self.headless, **PYPPETEER_LAUNCH_OPTIONS)
            page = await browser.newPage()
            events: list[dict] = []
            page.on(
                "response",
                lambda response: events.append({"status": response.status, "url": response.url})
                if ("devprod-api" in response.url or "oss-cn-shenzhen" in response.url)
                else None,
            )
            page.on("requestfailed", lambda request: events.append({"status": "failed", "url": request.url}))
            try:
                await self._ensure_logged_in(page)
                await self._execute_plan(
                    page=page,
                    plan=plan,
                    update_url=update_url,
                    intro_text=self._intro_text(release, packages),
                    events=events,
                )
                status_text = await fetch_status_text(page, INDEX_URL, app.app_name_zh, app.pkg_name)
                result = {
                    "status_text": status_text,
                    "detail_id": str(app_entry.get("id", "")),
                    "app_id": str(target_app_id or app_entry.get("app_id", "")),
                    "events_tail": events[-100:],
                }
                (artifact_dir / "browser-result.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return BrowserSubmissionResult(
                    app_id=result["app_id"],
                    status_text=status_text,
                    artifact_dir=artifact_dir,
                    detail_id=result["detail_id"],
                )
            finally:
                await browser.close()

        return asyncio.run(_run())
