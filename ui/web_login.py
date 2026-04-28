from __future__ import annotations

from datetime import datetime
from pathlib import Path

from appstore.session_state import BrowserSessionState
from ui.qt_compat import QtCore, QtWebEngineCore, QtWebEngineWidgets, QtWidgets, exec_dialog


STORE_INDEX_URL = "https://appstore-dev.uniontech.com/#/index"
STORE_HOST = "appstore-dev.uniontech.com"

_PAGE_STATE_SCRIPT = """
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
    readyState: String(document.readyState || ''),
    localStorage: dumpStorage(localStorage),
    sessionStorage: dumpStorage(sessionStorage)
  };
})()
"""


def _decode_cookie_bytes(value) -> str:
    raw = bytes(value)
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _cookie_to_payload(cookie) -> dict[str, object]:
    payload = {
        "name": _decode_cookie_bytes(cookie.name()),
        "value": _decode_cookie_bytes(cookie.value()),
        "domain": cookie.domain() or "",
        "path": cookie.path() or "/",
        "secure": bool(cookie.isSecure()),
        "httpOnly": bool(cookie.isHttpOnly()),
    }
    if hasattr(cookie, "expirationDate") and not cookie.isSessionCookie():
        expires = cookie.expirationDate()
        if expires is not None and expires.isValid():
            payload["expires"] = float(expires.toSecsSinceEpoch())
    return payload


def _normalize_storage_key(key: str) -> str:
    return str(key).replace("-", "").replace("_", "").lower()


def _has_auth_markers(storage: dict[str, str]) -> bool:
    if not isinstance(storage, dict):
        return False
    direct_keys = {"authorization", "authorizationtoken", "auth", "authtoken", "token", "accesstoken"}
    for key, value in storage.items():
        normalized_key = _normalize_storage_key(key)
        if "refresh" in normalized_key:
            continue
        if normalized_key in direct_keys and str(value).strip():
            return True
    return False


class _CookieMirror(QtCore.QObject):
    def __init__(self, cookie_store, parent=None) -> None:
        super().__init__(parent)
        self._cookies: dict[tuple[str, str, str], dict[str, object]] = {}
        self._cookie_store = cookie_store
        cookie_store.cookieAdded.connect(self._on_cookie_added)
        if hasattr(cookie_store, "cookieRemoved"):
            cookie_store.cookieRemoved.connect(self._on_cookie_removed)
        cookie_store.loadAllCookies()

    def snapshot(self) -> list[dict[str, object]]:
        return list(self._cookies.values())

    def _on_cookie_added(self, cookie) -> None:
        payload = _cookie_to_payload(cookie)
        key = (
            str(payload.get("name", "")).strip(),
            str(payload.get("domain", "")).strip(),
            str(payload.get("path", "/")).strip() or "/",
        )
        self._cookies[key] = payload

    def _on_cookie_removed(self, cookie) -> None:
        payload = _cookie_to_payload(cookie)
        key = (
            str(payload.get("name", "")).strip(),
            str(payload.get("domain", "")).strip(),
            str(payload.get("path", "/")).strip() or "/",
        )
        self._cookies.pop(key, None)


class StoreWebLoginDialog(QtWidgets.QDialog):
    def __init__(self, account_label: str, parent=None) -> None:
        if QtWebEngineCore is None or QtWebEngineWidgets is None:
            raise RuntimeError("当前 Qt 绑定未包含 QtWebEngine，无法使用真实网页扫码登录。")
        super().__init__(parent)
        self.account_label = account_label.strip() or "manual-login"
        self.session_state: BrowserSessionState | None = None
        self._last_page_state: dict[str, object] = {}
        self._disposed = False

        self.setWindowTitle("统信账号登录")
        self.resize(1280, 900)

        self._profile = QtWebEngineCore.QWebEngineProfile("appstore-ui-login", self)
        cache_root = Path(__file__).resolve().parent / "cache" / "webengine"
        cache_root.mkdir(parents=True, exist_ok=True)
        if hasattr(self._profile, "setCachePath"):
            self._profile.setCachePath(str(cache_root / "cache"))
        if hasattr(self._profile, "setPersistentStoragePath"):
            self._profile.setPersistentStoragePath(str(cache_root / "storage"))
        self._cookie_mirror = _CookieMirror(self._profile.cookieStore(), self)

        self._view = QtWebEngineWidgets.QWebEngineView(self)
        self._page = QtWebEngineCore.QWebEnginePage(self._profile, self._view)
        self._view.setPage(self._page)

        self.instructions_label = QtWidgets.QLabel(
            "这里打开的是统信账号真实网页登录页。可以按网页流程操作，点击“其他登录方式”进入“微信扫码登录”。"
        )
        self.instructions_label.setWordWrap(True)
        self.status_label = QtWidgets.QLabel("正在打开网页登录页。")
        self.url_label = QtWidgets.QLabel(STORE_INDEX_URL)
        if hasattr(self.url_label, "setTextInteractionFlags"):
            self.url_label.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                if hasattr(QtCore.Qt, "TextInteractionFlag")
                else QtCore.Qt.TextSelectableByMouse
            )
        self.reload_button = QtWidgets.QPushButton("重新加载")
        self.finish_button = QtWidgets.QPushButton("确认当前登录状态")
        self.finish_button.setEnabled(False)
        self.cancel_button = QtWidgets.QPushButton("取消")

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.reload_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.finish_button)
        button_layout.addWidget(self.cancel_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.instructions_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.url_label)
        layout.addWidget(self._view, 1)
        layout.addLayout(button_layout)

        self.reload_button.clicked.connect(self._view.reload)
        self.finish_button.clicked.connect(self._capture_and_accept)
        self.cancel_button.clicked.connect(self.reject)
        self._view.urlChanged.connect(self._handle_url_changed)
        self._page.loadFinished.connect(self._handle_load_finished)

        self._probe_timer = QtCore.QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.timeout.connect(self._probe_page_state)

        self._heartbeat_timer = QtCore.QTimer(self)
        self._heartbeat_timer.setInterval(1500)
        self._heartbeat_timer.timeout.connect(self._probe_page_state)
        self._heartbeat_timer.start()

        self._view.setUrl(QtCore.QUrl(STORE_INDEX_URL))

    def open_and_capture(self) -> BrowserSessionState | None:
        if exec_dialog(self) != self._accepted_code():
            return None
        return self.session_state

    def _accepted_code(self) -> int:
        if hasattr(QtWidgets.QDialog, "DialogCode"):
            return QtWidgets.QDialog.DialogCode.Accepted
        return QtWidgets.QDialog.Accepted

    def _handle_url_changed(self, url) -> None:
        self.url_label.setText(url.toString())
        self._schedule_probe()

    def _handle_load_finished(self, ok: bool) -> None:
        if not ok:
            self.status_label.setText("页面加载失败，可以点击重新加载后重试。")
            self.finish_button.setEnabled(False)
            return
        self._schedule_probe()

    def _schedule_probe(self) -> None:
        self._probe_timer.start(200)

    def _probe_page_state(self) -> None:
        try:
            state = self._run_javascript(_PAGE_STATE_SCRIPT, timeout_ms=10000)
        except Exception:
            return
        if not isinstance(state, dict):
            return
        self._last_page_state = state
        href = str(state.get("href", "")).strip()
        body_text = str(state.get("bodyText", "")).strip()
        local_storage = state.get("localStorage") if isinstance(state.get("localStorage"), dict) else {}
        session_storage = state.get("sessionStorage") if isinstance(state.get("sessionStorage"), dict) else {}
        if self._looks_logged_in(href=href, body_text=body_text, local_storage=local_storage, session_storage=session_storage):
            self.status_label.setText("已检测到商店登录态，可以点击“确认当前登录状态”保存会话。")
            self.finish_button.setEnabled(True)
            return
        if "微信扫码" in body_text or "其他登录方式" in body_text:
            self.status_label.setText("当前在统信账号登录页。可以按网页流程切换到“其他登录方式”并使用微信扫码登录。")
        elif "手机号/邮箱免密登录" in body_text or "密码登录" in body_text:
            self.status_label.setText("当前在统信账号登录页。可以继续账号登录，或切到“其他登录方式/微信扫码登录”。")
        else:
            self.status_label.setText("等待登录完成并返回应用商店。")
        self.finish_button.setEnabled(False)

    def _capture_and_accept(self) -> None:
        try:
            state = self._last_page_state or self._run_javascript(_PAGE_STATE_SCRIPT, timeout_ms=10000)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "无法读取页面状态", str(exc))
            return
        if not isinstance(state, dict):
            QtWidgets.QMessageBox.warning(self, "无法读取页面状态", "当前页面状态读取失败，请稍后重试。")
            return
        href = str(state.get("href", "")).strip()
        body_text = str(state.get("bodyText", "")).strip()
        local_storage = state.get("localStorage") if isinstance(state.get("localStorage"), dict) else {}
        session_storage = state.get("sessionStorage") if isinstance(state.get("sessionStorage"), dict) else {}
        if not self._looks_logged_in(
            href=href,
            body_text=body_text,
            local_storage=local_storage,
            session_storage=session_storage,
        ):
            QtWidgets.QMessageBox.warning(
                self,
                "登录尚未完成",
                "当前页面还没有检测到商店登录态。请继续在网页里完成登录，必要时切到“其他登录方式 -> 微信扫码登录”。",
            )
            return
        try:
            user_agent = self._run_javascript("navigator.userAgent", timeout_ms=5000)
        except Exception:
            user_agent = "Mozilla/5.0"
        self.session_state = BrowserSessionState(
            account=self.account_label,
            cookies=self._cookie_mirror.snapshot(),
            local_storage={str(key): str(value) for key, value in local_storage.items()},
            session_storage={str(key): str(value) for key, value in session_storage.items()},
            user_agent=str(user_agent or "Mozilla/5.0"),
            last_verified_at=datetime.now().isoformat(),
        )
        self.accept()

    def closeEvent(self, event) -> None:
        self._dispose_browser_objects()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._dispose_browser_objects()
        super().done(result)

    def _looks_logged_in(
        self,
        *,
        href: str,
        body_text: str,
        local_storage: dict[str, str],
        session_storage: dict[str, str],
    ) -> bool:
        normalized_href = href.lower()
        if STORE_HOST not in normalized_href:
            return False
        if "我的应用" in body_text and "应用列表" in body_text:
            return True
        if _has_auth_markers(local_storage) or _has_auth_markers(session_storage):
            return True
        return False

    def _run_javascript(self, script: str, *, timeout_ms: int) -> object:
        result_box: dict[str, object] = {"done": False, "result": None}
        loop = QtCore.QEventLoop(self)
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)

        def _finish(result=None) -> None:
            result_box["done"] = True
            result_box["result"] = result
            if timer.isActive():
                timer.stop()
            loop.quit()

        def _on_timeout() -> None:
            loop.quit()

        timer.timeout.connect(_on_timeout)
        timer.start(timeout_ms)
        self._page.runJavaScript(script, _finish)
        loop.exec()
        if not result_box["done"]:
            raise TimeoutError("timed out waiting for browser page javascript result")
        return result_box["result"]

    def _dispose_browser_objects(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._heartbeat_timer.stop()
        self._probe_timer.stop()
        try:
            self._view.urlChanged.disconnect(self._handle_url_changed)
        except Exception:
            pass
        try:
            self._page.loadFinished.disconnect(self._handle_load_finished)
        except Exception:
            pass
        try:
            replacement_page = QtWebEngineCore.QWebEnginePage(self._view)
            self._view.setPage(replacement_page)
        except Exception:
            replacement_page = None
        if self._page is not None:
            try:
                self._page.deleteLater()
            except Exception:
                pass
        self._page = replacement_page
