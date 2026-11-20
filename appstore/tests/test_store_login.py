import json
import unittest
from threading import Event
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

import requests

from appstore.store_login import BASE, ORIGIN, SSO_ORIGIN, AuthenticationError, login_store_cookies
from ui.wechat_qr_backend import WECHAT_ORIGIN, WECHAT_POLL_URL, WechatQrCancelled, run_wechat_qr_login


def _response(content: dict | str) -> requests.Response:
    result = requests.Response()
    result.status_code = 200
    result.encoding = "utf-8"
    result._content = (json.dumps(content) if isinstance(content, dict) else content).encode()
    return result


class StoreLoginTests(unittest.TestCase):
    def setUp(self) -> None:
        query = urlencode({
            "client_id": "example-store",
            "redirect_uri": ORIGIN,
            "response_type": "code",
            "scope": "base",
            "state": "example-state",
        })
        self.entry = f"{SSO_ORIGIN}/oauth2/authorize?{query}"
        self.store = MagicMock()
        self.store.__enter__.return_value = self.store
        self.store.headers = {}
        self.store.cookies = requests.cookies.RequestsCookieJar()
        self.store.cookies.set("access-token", "example-cookie", domain=".appstore-dev.uniontech.com", path="/")
        self.store.get.return_value = _response({"status": 200, "datas": self.entry})
        self.store.post.return_value = _response({"status": 200})
        self.identity = MagicMock()
        self.identity.__enter__.return_value = self.identity
        self.identity.headers = {}
        self.start_sessions = patch("appstore.store_login.requests.Session", side_effect=(self.store, self.identity))

    def test_account_login_exchanges_verified_callback_and_keeps_store_cookie(self) -> None:
        self.identity.get.side_effect = (
            _response("Login page"),
            _response({"JSEncrypt": True, "NECaptcha": False}),
            _response({"code": 200, "data": "example-key"}),
            _response(f'<script>location.href="{ORIGIN}?code=example-code&amp;state=example-state";</script>'),
        )
        self.identity.post.side_effect = (
            _response({"code": 200, "data": {"IsGrunt": False}}),
            _response({"code": 200, "data": {"redirect": "https://login.chinauos.com/api/oauth2/writehead?code=intermediate"}}),
        )

        with self.start_sessions, patch(
            "appstore.store_login._encrypt_credentials", return_value=("encrypted-account", "encrypted-password")
        ) as encrypt:
            cookies = login_store_cookies("example-account", "example-password")

        encrypt.assert_called_once_with("example-key", "example-account", "example-password")
        authorization = self.identity.post.call_args.kwargs["data"]
        self.assertEqual(authorization["password"], "encrypted-password")
        self.assertEqual(self.store.post.call_args.args[0], f"{BASE}/store-dev-auth/login")
        self.assertEqual(self.store.post.call_args.kwargs["json"], {"code": "example-code", "state": "example-state"})
        self.assertEqual(cookies, [{"name": "access-token", "value": "example-cookie", "domain": ".appstore-dev.uniontech.com", "path": "/"}])

    def test_account_login_stops_when_interactive_verification_is_required(self) -> None:
        self.identity.get.side_effect = (_response("Login page"), _response({"NECaptcha": True}))

        with self.start_sessions, self.assertRaisesRegex(AuthenticationError, "交互验证"):
            login_store_cookies("example-account", "example-password")

        self.identity.post.assert_not_called()
        self.store.post.assert_not_called()

    def test_account_login_rejects_mismatched_callback_state(self) -> None:
        self.identity.get.side_effect = (_response("Login page"), _response({"JSEncrypt": False, "NECaptcha": False}))
        self.identity.post.side_effect = (
            _response({"code": 200, "data": {"IsGrunt": False}}),
            _response({"code": 200, "data": {"redirect": f"{ORIGIN}?code=example-code&state=wrong-state"}}),
        )

        with self.start_sessions, self.assertRaisesRegex(AuthenticationError, "回跳校验失败"):
            login_store_cookies("example-account", "example-password")

        self.store.post.assert_not_called()


class WechatQrLoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MagicMock()
        self.identity = MagicMock()
        self.wechat = MagicMock()
        for session in (self.store, self.identity, self.wechat):
            session.__enter__.return_value = session
            session.headers = {}
        self.store.cookies = requests.cookies.RequestsCookieJar()
        self.store.cookies.set("access-token", "test-cookie", domain=".appstore-dev.uniontech.com", path="/")
        authorization = urlencode({
            "client_id": "test-store",
            "redirect_uri": ORIGIN,
            "response_type": "code",
            "scope": "base",
            "state": "store-state",
        })
        self.store.get.return_value = _response({"status": 200, "datas": f"{SSO_ORIGIN}/oauth2/authorize?{authorization}"})
        self.store.post.return_value = _response({"status": 200})
        wechat_params = urlencode({"redirect_uri": f"{SSO_ORIGIN}/oauth2/wechat", "state": "wechat-state"})
        self.page_url = f"{WECHAT_ORIGIN}/connect/qrconnect?{wechat_params}"
        self.identity.get.side_effect = (_response("Login page"), _response({"code": 200, "data": {"url": self.page_url}}))
        self.identity.post.return_value = _response({"code": 200, "data": {"IsGrunt": False}})
        self.qr_page = _response('<img src="/connect/qrcode/test-qr">')
        self.qr_image = _response("test image")
        self.qr_image.headers["Content-Type"] = "image/jpeg"
        self.sessions = patch("ui.wechat_qr_backend.requests.Session", side_effect=(self.store, self.identity, self.wechat))

    def test_qr_login_waits_for_confirmation_and_exchanges_both_states(self) -> None:
        continuation = f"{SSO_ORIGIN}/api/oauth2/authorize/registerlogin?autoLoginKey=test-continuation"
        self.identity.post.side_effect = (
            _response({"code": 200, "data": {"IsGrunt": False}}),
            _response({"code": 200, "data": {"redirect": continuation}}),
            _response({"code": 200, "data": {"redirect": f"{ORIGIN}/?code=store-code&state=store-state"}}),
        )
        self.wechat.get.side_effect = (
            self.qr_page, self.qr_image,
            _response("window.wx_errcode=408;"),
            _response("window.wx_errcode=404;"),
            _response("window.wx_errcode=405;window.wx_code='wechat-approved';"),
        )
        events = []

        with self.sessions, patch("ui.wechat_qr_backend.time.sleep"):
            state = run_wechat_qr_login("test-account", events.append)

        self.assertEqual(state.account, "test-account")
        self.assertEqual(state.cookies[0]["name"], "access-token")
        self.assertEqual(events[1]["event"], "qr")
        self.assertIn("已扫码", events[3]["message"])
        poll = self.wechat.get.call_args
        self.assertEqual(poll.args, (WECHAT_POLL_URL,))
        self.assertEqual(poll.kwargs["params"]["last"], 404)
        self.assertEqual(self.identity.post.call_args_list[1].kwargs["data"], {
            "code": "wechat-approved", "state": "wechat-state",
        })
        self.assertEqual(self.identity.post.call_args.args, (continuation,))
        self.assertEqual(self.store.post.call_args.kwargs["json"], {"code": "store-code", "state": "store-state"})

    def test_qr_login_rejects_mismatched_store_state(self) -> None:
        self.identity.post.side_effect = (
            _response({"code": 200, "data": {"IsGrunt": False}}),
            _response({"code": 200, "data": {"redirect": f"{ORIGIN}/?code=store-code&state=wrong-state"}}),
        )
        self.wechat.get.side_effect = (
            self.qr_page, self.qr_image, _response("window.wx_errcode=405;window.wx_code='wechat-approved';"),
        )

        with self.sessions, self.assertRaisesRegex(AuthenticationError, "回跳校验失败"):
            run_wechat_qr_login("test-account")

        self.store.post.assert_not_called()

    def test_qr_login_reports_expiry_without_exchanging_session(self) -> None:
        self.wechat.get.side_effect = (self.qr_page, self.qr_image, _response("window.wx_errcode=402;"))

        with self.sessions, self.assertRaisesRegex(AuthenticationError, "已过期"):
            run_wechat_qr_login("test-account")

        self.assertEqual(self.identity.post.call_count, 1)
        self.store.post.assert_not_called()

    def test_cancelled_qr_login_does_not_start_polling(self) -> None:
        self.wechat.get.side_effect = (self.qr_page, self.qr_image)
        cancelled = Event()

        def cancel_after_image(event: dict) -> None:
            if event["event"] == "qr":
                cancelled.set()

        with self.sessions, self.assertRaises(WechatQrCancelled):
            run_wechat_qr_login("test-account", cancel_after_image, is_cancelled=cancelled.is_set)

        self.assertEqual(self.wechat.get.call_count, 2)
        self.store.post.assert_not_called()
