from __future__ import annotations

import asyncio
import re
import sys
import time
import traceback
from urllib.parse import urljoin

import requests
from pyppeteer import launch

from appstore.appstore_client import AppStoreClient, PYPPETEER_LAUNCH_OPTIONS, build_requests_session
from appstore.browser_runtime import capture_browser_session_state, session_is_valid
from appstore.session_state import BrowserSessionState
from ui.qt_compat import QtCore, QtGui, QtWidgets, Signal, exec_dialog


STORE_INDEX_URL = "https://appstore-dev.uniontech.com/#/index"
APPSTORE_HOST = "appstore-dev.uniontech.com"
WECHAT_QR_HOST = "open.weixin.qq.com/connect/qrconnect"
WECHAT_QRCODE_PATH_RE = re.compile(r'(?:"|\')(?P<path>/connect/qrcode/[^"\']+)(?:"|\')', re.IGNORECASE)

WECHAT_STATUS_SCRIPT = """
(() => {
  const visibleText = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return '';
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
      return '';
    }
    return String(el.innerText || '').trim();
  };
  return {
    scan: visibleText('#wx_after_scan, .js_wx_after_scan, .web_qrcode_msg_success, .status_succ'),
    cancel: visibleText('#wx_after_cancel, .js_wx_after_cancel, .web_qrcode_msg_error, .status_fail'),
    defaultTip: visibleText('.js_wx_default_tip, .js_web_qrcode_tips_normal, .status_browser, .web_qrcode_tips'),
    qrSrc: (() => {
      const img = document.querySelector('img.js_qrcode_img, img.web_qrcode_img, img.qrcode');
      return img ? String(img.src || '') : '';
    })(),
  };
})()
"""

PAGE_STATE_SCRIPT = """
(() => {
  const dumpStorage = (storage) => {
    const out = {};
    try {
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i);
        out[key] = storage.getItem(key);
      }
    } catch (error) {
      out.__error__ = String(error);
    }
    return out;
  };
  return {
    href: String(location.href || ''),
    title: String(document.title || ''),
    bodyText: String(document.body ? (document.body.innerText || '') : ''),
    localStorage: dumpStorage(localStorage),
    sessionStorage: dumpStorage(sessionStorage)
  };
})()
"""


def _download_qr_bytes(url: str) -> bytes:
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://open.weixin.qq.com/",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.content


class WechatQrLoginThread(QtCore.QThread):
    qr_ready = Signal(bytes)
    status_changed = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, account_label: str) -> None:
        super().__init__()
        self.account_label = account_label.strip() or "manual-login"
        self._cancel_requested = False
        self._network_qr_src = ""

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            result = asyncio.run(self._run())
        except RuntimeError as exc:
            self.failed.emit(str(exc))
            return
        except Exception:
            traceback.print_exc()
            exc = sys.exc_info()[1]
            self.failed.emit(str(exc) or exc.__class__.__name__ if exc is not None else "扫码登录失败。")
            return
        if result is not None and not self._cancel_requested:
            self.succeeded.emit(result)

    async def _run(self) -> BrowserSessionState | None:
        self._network_qr_src = ""
        browser = await launch(headless=True, autoClose=False, **PYPPETEER_LAUNCH_OPTIONS)
        try:
            index_page = await browser.newPage()
            await index_page.setViewport({"width": 1280, "height": 960})
            self.status_changed.emit("正在获取微信登录二维码。")
            await index_page.goto(STORE_INDEX_URL, {"waitUntil": "networkidle2", "timeout": 120000})
            await index_page.waitFor(2000)
            await self._click_other_login(index_page)
            qr_page = await self._wait_for_wechat_qr_page(browser)
            qr_src = await self._wait_for_qr_src(qr_page)
            self.qr_ready.emit(_download_qr_bytes(qr_src))
            self.status_changed.emit("请使用微信扫码，扫码后在手机上确认登录。")
            return await self._wait_for_login_success(browser, qr_page)
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    async def _click_other_login(self, page) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            self._ensure_not_cancelled()
            handles = await page.JJ("a,button,div,span")
            for handle in handles:
                try:
                    text = await page.evaluate('(el) => (el.innerText || "").trim()', handle)
                except Exception:
                    continue
                if text == "其他登录方式":
                    await handle.click()
                    return
            await asyncio.sleep(0.5)
        raise RuntimeError("未找到“其他登录方式”入口，无法切换到微信扫码登录。")

    async def _wait_for_wechat_qr_page(self, browser):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            self._ensure_not_cancelled()
            pages = await browser.pages()
            for page in pages:
                if WECHAT_QR_HOST in page.url:
                    self._attach_qr_response_listener(page)
                    try:
                        await page.bringToFront()
                    except Exception:
                        pass
                    return page
            await asyncio.sleep(0.5)
        raise RuntimeError("未能打开微信扫码登录页。")

    async def _wait_for_qr_src(self, qr_page) -> str:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            self._ensure_not_cancelled()
            if self._network_qr_src:
                return self._network_qr_src
            html_src = await self._extract_qr_src_from_html(qr_page)
            if html_src:
                return html_src
            try:
                state = await qr_page.evaluate(WECHAT_STATUS_SCRIPT)
            except Exception:
                await asyncio.sleep(0.5)
                continue
            qr_src = str((state or {}).get("qrSrc", "")).strip()
            if "/connect/qrcode/" in qr_src:
                return qr_src
            await asyncio.sleep(0.5)
        raise RuntimeError("未能从微信登录页提取二维码。")

    async def _wait_for_login_success(self, browser, qr_page) -> BrowserSessionState:
        deadline = time.monotonic() + 300
        last_status = ""
        last_probe_at = 0.0
        while time.monotonic() < deadline:
            self._ensure_not_cancelled()
            try:
                state = await qr_page.evaluate(WECHAT_STATUS_SCRIPT)
            except Exception:
                state = {}
            status_text = _wechat_status_text(state if isinstance(state, dict) else {})
            if status_text and status_text != last_status:
                self.status_changed.emit(status_text)
                last_status = status_text

            pages = await browser.pages()
            for page in pages:
                url = page.url.lower()
                if APPSTORE_HOST not in url:
                    continue
                try:
                    page_state = await self._read_page_state(page)
                except Exception:
                    continue
                if page_state is None:
                    continue
                if self._looks_logged_in_page(page_state):
                    self.status_changed.emit("扫码登录成功，正在保存会话。")
                    return await capture_browser_session_state(page, account=self.account_label, timeout_ms=120000)

            should_probe = False
            if status_text:
                lowered = status_text.lower()
                if any(token in lowered for token in ("已扫描", "扫描成功", "请在手机上确认", "确认登录", "success")):
                    should_probe = True
            if time.monotonic() - last_probe_at >= (1.5 if should_probe else 4.0):
                last_probe_at = time.monotonic()
                probed_state = await self._probe_appstore_session(browser)
                if probed_state is not None:
                    self.status_changed.emit("扫码登录成功，正在保存会话。")
                    return probed_state
            await asyncio.sleep(1)
        raise TimeoutError("微信扫码登录超时，未检测到应用商店登录成功。")

    async def _probe_appstore_session(self, browser) -> BrowserSessionState | None:
        page = await browser.newPage()
        try:
            await page.setViewport({"width": 1280, "height": 960})
            try:
                await page.goto(STORE_INDEX_URL, {"waitUntil": "networkidle2", "timeout": 120000})
            except Exception:
                return None
            await asyncio.sleep(1.5)
            captured = await capture_browser_session_state(page, account=self.account_label, timeout_ms=3000)
            page_state = await self._read_page_state(page)
            if page_state is not None and self._looks_logged_in_page(page_state):
                return captured
            try:
                client = AppStoreClient(
                    build_requests_session(
                        cookies=captured.cookies,
                        local_storage=captured.local_storage,
                        session_storage=captured.session_storage,
                    )
                )
                client.fetch_dev_info()
                return captured
            except Exception:
                return None
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _read_page_state(self, page) -> dict[str, object] | None:
        try:
            state = await page.evaluate(PAGE_STATE_SCRIPT)
        except Exception:
            return None
        if not isinstance(state, dict):
            return None
        return state

    def _looks_logged_in_page(self, state: dict[str, object]) -> bool:
        href = str(state.get("href", "")).strip().lower()
        if APPSTORE_HOST not in href:
            return False
        body_text = str(state.get("bodyText", "")).strip()
        if "我的应用" in body_text and "应用列表" in body_text:
            return True
        local_storage = state.get("localStorage")
        session_storage = state.get("sessionStorage")
        if self._has_auth_markers(local_storage) or self._has_auth_markers(session_storage):
            return True
        return False

    def _has_auth_markers(self, storage) -> bool:
        if not isinstance(storage, dict):
            return False
        direct_keys = {"authorization", "authorizationtoken", "auth", "authtoken", "token", "accesstoken"}
        for key, value in storage.items():
            normalized_key = str(key).replace("-", "").replace("_", "").lower()
            if "refresh" in normalized_key:
                continue
            if normalized_key in direct_keys and str(value).strip():
                return True
        return False

    def _ensure_not_cancelled(self) -> None:
        if self._cancel_requested:
            raise RuntimeError("扫码登录已取消。")

    def _attach_qr_response_listener(self, qr_page) -> None:
        if getattr(qr_page, "_appstore_qr_listener_attached", False):
            return

        def _on_response(response) -> None:
            try:
                response_url = str(response.url or "").strip()
            except Exception:
                return
            if "/connect/qrcode/" not in response_url:
                return
            self._network_qr_src = response_url

        qr_page.on("response", _on_response)
        setattr(qr_page, "_appstore_qr_listener_attached", True)

    async def _extract_qr_src_from_html(self, qr_page) -> str:
        try:
            html = await qr_page.content()
        except Exception:
            return ""
        match = WECHAT_QRCODE_PATH_RE.search(html)
        if not match:
            return ""
        return urljoin(qr_page.url, match.group("path"))


def _wechat_status_text(state: dict[str, object]) -> str:
    scan = str(state.get("scan", "")).strip()
    if scan:
        return scan
    cancel = str(state.get("cancel", "")).strip()
    if cancel:
        return cancel
    default_tip = str(state.get("defaultTip", "")).strip()
    if default_tip:
        return default_tip
    return ""


def _friendly_error_text(error_text: str) -> str:
    message = error_text.strip()
    if not message:
        return "扫码登录失败。"
    if "Traceback" not in message:
        return message
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith(("TimeoutError:", "RuntimeError:", "ValueError:", "AuthenticationError:", "Exception:")):
            return line.split(":", 1)[1].strip() or line
    return lines[-1]


class WechatQrLoginDialog(QtWidgets.QDialog):
    def __init__(self, account_label: str, parent=None) -> None:
        super().__init__(parent)
        self.account_label = account_label.strip() or "manual-login"
        self.session_state: BrowserSessionState | None = None
        self._thread: WechatQrLoginThread | None = None
        self._restart_after_stop = False

        self.setWindowTitle("微信扫码登录")
        self.resize(460, 620)

        self.title_label = QtWidgets.QLabel("请使用微信扫码登录统信账号")
        self.title_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.tip_label = QtWidgets.QLabel("二维码来自真实网页登录链路，后台自动等待扫码确认并回收登录态。")
        self.tip_label.setWordWrap(True)
        self.tip_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )
        self.qr_label = QtWidgets.QLabel("正在加载二维码…")
        self.qr_label.setMinimumSize(360, 360)
        self.qr_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )
        self.qr_label.setFrameShape(
            QtWidgets.QFrame.Shape.Box if hasattr(QtWidgets.QFrame, "Shape") else QtWidgets.QFrame.Box
        )
        self.status_label = QtWidgets.QLabel("准备中。")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )
        self.refresh_button = QtWidgets.QPushButton("刷新二维码")
        self.cancel_button = QtWidgets.QPushButton("取消")

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.cancel_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.tip_label)
        layout.addWidget(self.qr_label, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(button_layout)

        self.refresh_button.clicked.connect(self._handle_refresh)
        self.cancel_button.clicked.connect(self.reject)

        self._start_worker()

    def open_and_capture(self) -> BrowserSessionState | None:
        if exec_dialog(self) == self._accepted_code():
            return self.session_state
        return None

    def _accepted_code(self) -> int:
        if hasattr(QtWidgets.QDialog, "DialogCode"):
            return QtWidgets.QDialog.DialogCode.Accepted
        return QtWidgets.QDialog.Accepted

    def _start_worker(self) -> None:
        self._restart_after_stop = False
        thread = WechatQrLoginThread(self.account_label)
        thread.setParent(self)
        thread.qr_ready.connect(self._on_qr_ready)
        thread.status_changed.connect(self._on_status_changed)
        thread.succeeded.connect(self._on_login_succeeded)
        thread.failed.connect(self._on_login_failed)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self.refresh_button.setEnabled(False)
        self.status_label.setText("正在加载二维码。")
        self.qr_label.setPixmap(QtGui.QPixmap())
        self.qr_label.setText("正在加载二维码…")
        thread.start()

    def _stop_worker(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._thread = None
        thread.cancel()
        if thread.isRunning():
            thread.wait(5000)

    def _handle_refresh(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._restart_after_stop = True
            self._stop_worker()
            return
        self._start_worker()

    def _on_qr_ready(self, image_bytes: bytes) -> None:
        self.refresh_button.setEnabled(True)
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(image_bytes)
        if pixmap.isNull():
            self.qr_label.setText("二维码加载失败")
            return
        self.qr_label.setText("")
        self.qr_label.setPixmap(
            pixmap.scaled(
                320,
                320,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio
                if hasattr(QtCore.Qt, "AspectRatioMode")
                else QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation
                if hasattr(QtCore.Qt, "TransformationMode")
                else QtCore.Qt.SmoothTransformation,
            )
        )

    def _on_status_changed(self, message: str) -> None:
        self.status_label.setText(message.strip() or "等待扫码。")

    def _on_login_succeeded(self, state: BrowserSessionState) -> None:
        self.session_state = state
        self._thread = None
        self.accept()

    def _on_login_failed(self, error_text: str) -> None:
        self._thread = None
        self.refresh_button.setEnabled(True)
        message = _friendly_error_text(error_text)
        if "扫码登录已取消" in message:
            if self._restart_after_stop:
                self._start_worker()
            return
        self.status_label.setText("二维码加载或扫码登录失败。")
        QtWidgets.QMessageBox.critical(self, "微信扫码登录失败", message)

    def reject(self) -> None:
        self._stop_worker()
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop_worker()
        super().closeEvent(event)
