from __future__ import annotations

from datetime import datetime

from appstore.session_state import BrowserSessionState


async def restore_session_state(page, state: BrowserSessionState) -> None:
    await page.setUserAgent(state.user_agent)
    if state.cookies:
        await page.setCookie(*state.cookies)
    await page.evaluate(
        """(localState, sessionState) => {
            localStorage.clear();
            sessionStorage.clear();
            for (const [key, value] of Object.entries(localState || {})) {
                localStorage.setItem(key, value);
            }
            for (const [key, value] of Object.entries(sessionState || {})) {
                sessionStorage.setItem(key, value);
            }
        }""",
        state.local_storage,
        state.session_storage,
    )


async def capture_browser_session_state(page, *, account: str, timeout_ms: int = 120000) -> BrowserSessionState:
    await page.waitFor(timeout_ms // 60)
    cookies = await page.cookies()
    user_agent = await page.evaluate("() => navigator.userAgent")
    local_storage = await page.evaluate(
        """() => {
          const out = {};
          for (let i = 0; i < localStorage.length; i += 1) {
            const key = localStorage.key(i);
            out[key] = localStorage.getItem(key);
          }
          return out;
        }"""
    )
    session_storage = await page.evaluate(
        """() => {
          const out = {};
          for (let i = 0; i < sessionStorage.length; i += 1) {
            const key = sessionStorage.key(i);
            out[key] = sessionStorage.getItem(key);
          }
          return out;
        }"""
    )
    return BrowserSessionState(
        account=account,
        cookies=cookies,
        local_storage=local_storage,
        session_storage=session_storage,
        user_agent=user_agent,
        last_verified_at=datetime.now().isoformat(),
    )


async def session_is_valid(page) -> bool:
    body = await page.evaluate("() => document.body ? document.body.innerText : ''")
    return "我的应用" in body and "应用列表" in body


async def wait_for_logged_in_portal_state(page, *, timeout_ms: int = 120000) -> None:
    deadline = datetime.now().timestamp() + (timeout_ms / 1000)
    while datetime.now().timestamp() < deadline:
        if await session_is_valid(page):
            return
        await page.waitFor(1000)
    raise TimeoutError("timed out waiting for logged-in portal state")
