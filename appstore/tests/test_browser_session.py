import tempfile
import unittest
from pathlib import Path

from appstore.browser_session import BrowserSessionStore


class BrowserSessionStoreTests(unittest.TestCase):
    def test_round_trips_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = BrowserSessionStore(Path(temp_dir))
            state = {
                "cookies": [{"name": "token", "value": "abc"}],
                "local_storage": {"authorization": "Bearer 1"},
                "session_storage": {},
                "user_agent": "UA",
            }

            store.save("odatacc", state)
            loaded = store.load("odatacc")

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["user_agent"], "UA")
            self.assertIn("last_verified_at", loaded)

    def test_invalidate_removes_cached_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = BrowserSessionStore(Path(temp_dir))
            store.save(
                "odatacc",
                {"cookies": [], "local_storage": {}, "session_storage": {}, "user_agent": "UA"},
            )

            store.invalidate("odatacc")

            self.assertIsNone(store.load("odatacc"))
