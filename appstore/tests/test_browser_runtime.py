import unittest
from unittest.mock import AsyncMock

from appstore.browser_runtime import restore_session_state, session_is_valid
from appstore.session_state import BrowserSessionState


class BrowserRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_session_state_replays_cookies_and_storage(self) -> None:
        page = AsyncMock()
        state = BrowserSessionState(
            account="odatacc",
            cookies=[{"name": "sid", "value": "cookie", "domain": "appstore-dev.uniontech.com", "path": "/"}],
            local_storage={"token": "abc"},
            session_storage={"refresh": "xyz"},
            user_agent="Mozilla/5.0",
            last_verified_at="2026-04-22T16:30:00+08:00",
        )

        await restore_session_state(page, state)

        page.setUserAgent.assert_awaited_once_with("Mozilla/5.0")
        page.setCookie.assert_awaited()
        page.evaluate.assert_awaited()

    async def test_session_is_valid_returns_true_for_logged_in_marker(self) -> None:
        page = AsyncMock()
        page.evaluate.return_value = "我的应用\n应用列表"

        valid = await session_is_valid(page)

        self.assertTrue(valid)
