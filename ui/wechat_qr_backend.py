from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

from appstore.session_state import BrowserSessionState
from appstore.store_login import (
    LOGIN_TIMEOUT,
    SSO_CALLBACK_HOST,
    SSO_ORIGIN,
    USER_AGENT,
    AuthenticationError,
    _read_json,
    begin_store_authorization,
    check_store_authorization,
    exchange_store_session,
)


WECHAT_ORIGIN = "https://open.weixin.qq.com"
WECHAT_POLL_URL = "https://long.open.weixin.qq.com/connect/l/qrconnect"
WECHAT_CONFIRMATION_TIMEOUT = 300
_QR_IMAGE = re.compile(r"""["'](/connect/qrcode/([A-Za-z0-9_-]+)(?:\?[^"']*)?)["']""")
_WX_STATUS = re.compile(r"\bwindow\.wx_errcode\s*=\s*(\d+)")
_WX_CODE = re.compile(r"""\bwindow\.wx_code\s*=\s*["']([A-Za-z0-9_-]{1,256})["']""")


class WechatQrCancelled(RuntimeError):
    pass


def _check_cancelled(is_cancelled: Callable[[], bool]) -> None:
    if is_cancelled():
        raise WechatQrCancelled("扫码登录已取消。")


def _wechat_login_page(identity: requests.Session, params: dict[str, str]) -> tuple[str, str]:
    response = identity.get(
        f"{SSO_ORIGIN}/api/oauth2/platform/login/url",
        params={**params, "platform": "wechat"},
        timeout=LOGIN_TIMEOUT,
        allow_redirects=False,
    )
    result = _read_json(response, "获取微信登录地址")
    data = result.get("data")
    if result.get("code") != 200 or not isinstance(data, dict) or not isinstance(data.get("url"), str):
        raise AuthenticationError("统信账号服务没有返回微信扫码地址。")
    url = urljoin(SSO_ORIGIN, data["url"])
    parts = urlsplit(url)
    if (parts.scheme, parts.netloc, parts.path) != ("https", "open.weixin.qq.com", "/connect/qrconnect"):
        raise AuthenticationError("统信账号服务返回了未知的微信扫码地址。")
    query = parse_qs(parts.query)
    state = query.get("state", [])
    if len(state) != 1 or query.get("redirect_uri") != [f"{SSO_ORIGIN}/oauth2/wechat"]:
        raise AuthenticationError("微信登录参数校验失败。")
    return url, state[0]


def _fetch_qr_image(wechat: requests.Session, page_url: str) -> tuple[str, bytes]:
    wechat.headers.update({"User-Agent": USER_AGENT, "Referer": page_url})
    page = wechat.get(page_url, timeout=LOGIN_TIMEOUT, allow_redirects=False)
    match = _QR_IMAGE.search(page.text) if page.status_code == 200 else None
    if match is None:
        raise AuthenticationError("微信登录页面未返回二维码，请刷新后重试。")
    image = wechat.get(urljoin(WECHAT_ORIGIN, match[1]), timeout=LOGIN_TIMEOUT, allow_redirects=False)
    if image.status_code != 200 or not image.headers.get("Content-Type", "").lower().startswith("image/") or not image.content:
        raise AuthenticationError("无法读取微信登录二维码。")
    return match[2], image.content


def _wait_for_wechat_code(
    wechat: requests.Session,
    qr_id: str,
    status_changed: Callable[[str], None],
    is_cancelled: Callable[[], bool],
) -> str:
    deadline = time.monotonic() + WECHAT_CONFIRMATION_TIMEOUT
    last_status = None
    scan_reported = False
    while (remaining := deadline - time.monotonic()) > 0:
        _check_cancelled(is_cancelled)
        params = {"uuid": qr_id, "_": int(time.time() * 1000)}
        if last_status is not None:
            params["last"] = last_status
        try:
            reply = wechat.get(
                WECHAT_POLL_URL, params=params, timeout=(10, min(32, remaining)), allow_redirects=False
            )
        except requests.ReadTimeout:
            continue
        _check_cancelled(is_cancelled)
        match = _WX_STATUS.search(reply.text) if reply.status_code == 200 else None
        if match is None:
            raise AuthenticationError("微信扫码状态读取失败，请刷新二维码。")
        status = int(match[1])
        if status == 405:
            code = _WX_CODE.search(reply.text)
            if code is None:
                raise AuthenticationError("微信已确认登录，但未返回授权码。")
            return code[1]
        if status == 404:
            if not scan_reported:
                status_changed("已扫码，请在手机微信中确认。")
                scan_reported = True
            last_status = status
            time.sleep(0.1)
            continue
        if status == 408:
            last_status = None
            time.sleep(2)
            continue
        if status == 402:
            raise AuthenticationError("微信二维码已过期，请刷新后重新扫码。")
        if status == 403:
            raise AuthenticationError("微信已取消本次登录，请刷新后重新扫码。")
        raise AuthenticationError(f"微信扫码返回未知状态（{status}），请刷新后重试。")
    raise TimeoutError("等待微信扫码确认超时，请刷新二维码。")


def _read_login_redirect(response: requests.Response) -> str:
    message = _read_json(response, "确认微信登录")
    data = message.get("data")
    if message.get("code") != 200 or not isinstance(data, dict):
        raise AuthenticationError("统信账号服务未接受本次微信授权，请重新扫码。")
    if (
        data.get("isbind") is False
        or data.get("need_bind_contact")
        or data.get("phone_unbind")
        or data.get("cancel_unregister_key")
    ):
        raise AuthenticationError("此微信账号需要在统信官方登录页面完成绑定或账号验证。")
    redirect = data.get("redirect")
    if not isinstance(redirect, str) or not redirect:
        raise AuthenticationError("统信账号服务需要进一步确认，请在官方登录页面完成。")
    return redirect


def _finish_wechat_authorization(identity: requests.Session, code: str, state: str) -> str:
    callback = identity.post(
        f"{SSO_ORIGIN}/api/oauth2/platform/callback/login/wechat",
        data={"code": code, "state": state},
        timeout=LOGIN_TIMEOUT,
        allow_redirects=False,
    )
    redirect = _read_login_redirect(callback)
    url = urljoin(SSO_ORIGIN, redirect)
    parts = urlsplit(url)
    if parts.path == "/api/oauth2/authorize/registerlogin":
        if parts.scheme != "https" or parts.netloc not in {urlsplit(SSO_ORIGIN).netloc, SSO_CALLBACK_HOST}:
            raise AuthenticationError("统信账号服务返回了未知的扫码确认地址。")
        # Complete the continuation used by the official sign-in page after phone confirmation.
        response = identity.post(url, json={}, timeout=LOGIN_TIMEOUT, allow_redirects=False)
        return _read_login_redirect(response)
    return redirect


def run_wechat_qr_login(
    account_label: str,
    event_callback: Callable[[dict], None] | None = None,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> BrowserSessionState:
    def emit(event: str, **payload: object) -> None:
        if event_callback is not None:
            event_callback({"event": event, **payload})

    def status_changed(message: str) -> None:
        emit("status", message=message)

    cancelled = is_cancelled or (lambda: False)
    try:
        with requests.Session() as store, requests.Session() as identity, requests.Session() as wechat:
            status_changed("正在获取微信登录二维码。")
            _check_cancelled(cancelled)
            params = begin_store_authorization(store, identity)
            check_store_authorization(identity, params)
            qr_page, wechat_state = _wechat_login_page(identity, params)
            qr_id, image = _fetch_qr_image(wechat, qr_page)
            _check_cancelled(cancelled)
            emit("qr", image_base64=base64.b64encode(image).decode("ascii"))
            status_changed("请使用微信扫码，扫码后在手机上确认登录。")
            code = _wait_for_wechat_code(wechat, qr_id, status_changed, cancelled)
            _check_cancelled(cancelled)
            status_changed("正在验证商店会话。")
            redirect = _finish_wechat_authorization(identity, code, wechat_state)
            _check_cancelled(cancelled)
            cookies = exchange_store_session(store, identity, redirect, params["state"])
            _check_cancelled(cancelled)
    except requests.RequestException:
        raise AuthenticationError("连接微信或统信账号服务失败，请检查网络后刷新二维码。") from None

    return BrowserSessionState(
        account=account_label.strip() or "wechat-login",
        cookies=cookies,
        local_storage={},
        session_storage={},
        user_agent=USER_AGENT,
        last_verified_at=datetime.now().isoformat(),
    )
