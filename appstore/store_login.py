from __future__ import annotations

import base64
import html
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import requests


ORIGIN = "https://appstore-dev.uniontech.com"
BASE = f"{ORIGIN}/devprod-api"
SSO_ORIGIN = "https://login.uniontech.com"
SSO_CALLBACK_HOST = "login.chinauos.com"
LOGIN_TIMEOUT = (10, 30)
USER_AGENT = "Mozilla/5.0"
_STORE_HOST = urlsplit(ORIGIN).netloc
_CALLBACK_URL = re.compile(r"https://appstore-dev\.uniontech\.com[^\s<>\"']*")


class AuthenticationError(RuntimeError):
    pass


def _read_json(response: requests.Response, action: str) -> dict:
    if not 200 <= response.status_code < 300:
        raise AuthenticationError(f"{action}失败（HTTP {response.status_code}）。")
    try:
        data = response.json()
    except ValueError:
        raise AuthenticationError(f"{action}未返回有效的 JSON。") from None
    if not isinstance(data, dict):
        raise AuthenticationError(f"{action}返回了无效的数据。")
    return data


def _authorization_params(entry: str) -> dict[str, str]:
    parts = urlsplit(entry)
    expected = ("https", urlsplit(SSO_ORIGIN).netloc, "/oauth2/authorize")
    if (parts.scheme, parts.netloc, parts.path) != expected:
        raise AuthenticationError("商店返回了未知的账号登录地址。")
    query = parse_qs(parts.query)
    params = {key: values[0] for key, values in query.items() if len(values) == 1}
    required = {"client_id", "redirect_uri", "response_type", "scope", "state"}
    if (
        not required <= params.keys()
        or params["response_type"] != "code"
        or params["redirect_uri"].rstrip("/") != ORIGIN
    ):
        raise AuthenticationError("商店返回了无效的登录参数。")
    return params


def _encrypt_credentials(public_key: str, username: str, password: str) -> tuple[str, str]:
    try:
        key_bytes = base64.b64decode(public_key, validate=True)
    except ValueError:
        raise AuthenticationError("统信账号服务返回了无效的加密公钥。") from None
    # The login service spells the PKCS#1 PEM label with mixed case.
    key_bytes = key_bytes.replace(b"RSA Public Key", b"RSA PUBLIC KEY")
    with tempfile.TemporaryDirectory(prefix="utpublisher-login-key-") as directory:
        key_path = Path(directory) / "public.pem"
        key_path.write_bytes(key_bytes)
        command = [
            "openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(key_path),
            "-pkeyopt", "rsa_padding_mode:pkcs1",
        ]
        try:
            encrypted = [
                subprocess.run(command, input=value.encode(), capture_output=True, check=True, timeout=10).stdout
                for value in (username, password)
            ]
        except FileNotFoundError:
            raise AuthenticationError("账号密码登录需要安装 OpenSSL。") from None
        except (OSError, subprocess.SubprocessError):
            raise AuthenticationError("无法使用统信账号服务的公钥加密登录信息。") from None
    return (
        base64.b64encode(encrypted[0]).decode("ascii"),
        base64.b64encode(encrypted[1]).decode("ascii"),
    )


def _callback_payload(identity: requests.Session, redirect: str, state: str) -> dict[str, str]:
    url = urljoin(SSO_ORIGIN, redirect)
    parts = urlsplit(url)
    if (parts.scheme, parts.netloc, parts.path) == ("https", SSO_CALLBACK_HOST, "/api/oauth2/writehead"):
        response = identity.get(url, timeout=LOGIN_TIMEOUT, allow_redirects=False)
        if response.is_redirect:
            url = urljoin(url, response.headers["Location"])
        elif response.ok:
            # This first-party page finishes setting cookies and returns the portal URL.
            matches = _CALLBACK_URL.findall(html.unescape(response.text))
            url = matches[0] if len(matches) == 1 else ""
        else:
            raise AuthenticationError(f"读取统信账号回跳地址失败（HTTP {response.status_code}）。")
        parts = urlsplit(url)

    query = parse_qs(parts.query)
    if (parts.scheme, parts.netloc) != ("https", _STORE_HOST) or parts.path not in {"", "/"}:
        raise AuthenticationError("统信账号服务返回了未知的商店回跳地址。")
    if query.get("state") != [state] or len(query.get("code", ())) != 1:
        raise AuthenticationError("商店登录回跳校验失败，请重新登录。")
    return {"code": query["code"][0], "state": state}


def begin_store_authorization(store: requests.Session, identity: requests.Session) -> dict[str, str]:
    store.headers.update({"Origin": ORIGIN, "Referer": f"{ORIGIN}/", "User-Agent": USER_AGENT})
    identity.headers["User-Agent"] = USER_AGENT
    entry_reply = store.get(f"{BASE}/store-dev-auth/getLoginPath", timeout=LOGIN_TIMEOUT, allow_redirects=False)
    entry_data = _read_json(entry_reply, "读取商店登录地址")
    entry = entry_data.get("datas")
    if entry_data.get("status") != 200 or not isinstance(entry, str):
        raise AuthenticationError("商店没有返回登录地址。")
    params = _authorization_params(entry)
    response = identity.get(entry, timeout=LOGIN_TIMEOUT, allow_redirects=False)
    if not 200 <= response.status_code < 300:
        raise AuthenticationError(f"打开统信账号登录页失败（HTTP {response.status_code}）。")
    identity.headers.update({"Origin": SSO_ORIGIN, "Referer": entry, "Accept": "application/json, text/plain, */*"})
    return params


def check_store_authorization(identity: requests.Session, params: dict[str, str]) -> None:
    metadata = _read_json(
        identity.post(f"{SSO_ORIGIN}/api/oauth2/client", data=params, timeout=LOGIN_TIMEOUT, allow_redirects=False),
        "检查账号授权信息",
    )
    if metadata.get("code") != 200 or not isinstance(metadata.get("data"), dict):
        raise AuthenticationError("统信账号服务未接受商店授权请求。")
    if metadata["data"].get("IsGrunt"):
        raise AuthenticationError("统信账号服务需要确认授权，请在官方登录页面完成确认。")


def exchange_store_session(
    store: requests.Session, identity: requests.Session, redirect: str, state: str
) -> list[dict]:
    payload = _callback_payload(identity, redirect, state)
    reply = store.post(f"{BASE}/store-dev-auth/login", json=payload, timeout=LOGIN_TIMEOUT, allow_redirects=False)
    if _read_json(reply, "建立商店会话").get("status") != 200:
        raise AuthenticationError("商店未接受统信账号登录结果。")
    cookies = [
        {"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path}
        for cookie in store.cookies
        if cookie.domain.lstrip(".") == _STORE_HOST
    ]
    if not any(cookie["name"] == "access-token" and cookie["value"] for cookie in cookies):
        raise AuthenticationError("商店没有签发登录会话。")
    return cookies


def _authorize(identity: requests.Session, params: dict[str, str], username: str, password: str) -> str:
    config = _read_json(
        identity.get(f"{SSO_ORIGIN}/assets/config/config.json", timeout=LOGIN_TIMEOUT, allow_redirects=False),
        "读取登录配置",
    )
    if config.get("NECaptcha") is not False:
        raise AuthenticationError("统信账号服务要求完成交互验证，请使用网页或扫码登录。")
    check_store_authorization(identity, params)
    if config.get("JSEncrypt") is not False:
        key = _read_json(
            identity.get(f"{SSO_ORIGIN}/api/key", timeout=LOGIN_TIMEOUT, allow_redirects=False),
            "读取账号加密公钥",
        )
        if key.get("code") != 200 or not isinstance(key.get("data"), str):
            raise AuthenticationError("统信账号服务没有提供登录加密公钥。")
        username, password = _encrypt_credentials(key["data"], username, password)

    # Mirror the first-party password form when the service has disabled its CAPTCHA.
    data = dict(params, account=username, password=password, token="has", validate="random_hdh", user_oauth="o_base")
    result = _read_json(
        identity.post(f"{SSO_ORIGIN}/api/oauth2/authorize", data=data, timeout=LOGIN_TIMEOUT, allow_redirects=False),
        "统信账号登录",
    )
    details = result.get("data")
    if result.get("code") != 200 or not isinstance(details, dict):
        code = result.get("code")
        suffix = f"（代码 {code}）" if isinstance(code, int) else ""
        raise AuthenticationError(f"统信账号登录失败{suffix}，请检查账号和密码。")
    if details.get("need_bind_contact") or details.get("cancel_unregister_key"):
        raise AuthenticationError("统信账号需要完成交互验证，请使用网页或扫码登录。")
    redirect = details.get("redirect")
    if not isinstance(redirect, str) or not redirect:
        raise AuthenticationError("统信账号服务没有返回商店回跳地址。")
    return redirect


def login_store_cookies(username: str, password: str) -> list[dict]:
    if not username.strip() or not password:
        raise AuthenticationError("请输入账号和密码。")

    try:
        with requests.Session() as store, requests.Session() as identity:
            params = begin_store_authorization(store, identity)
            redirect = _authorize(identity, params, username.strip(), password)
            return exchange_store_session(store, identity, redirect, params["state"])
    except requests.RequestException:
        raise AuthenticationError("连接统信账号或商店服务失败，请检查网络后重试。") from None
