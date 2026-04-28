import unittest

from appstore.ai_click_targets import choose_click_targets


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _FakeSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict, dict, float]] = []

    def post(self, url: str, *, json: dict, headers: dict, timeout: float):
        self.calls.append((url, json, headers, timeout))
        return _FakeResponse(self.payload)


class AIClickTargetsTests(unittest.TestCase):
    def test_choose_click_targets_filters_to_allowed_targets(self) -> None:
        session = _FakeSession(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"actions":[{"type":"click","element_id":"node-02"},{"type":"click","element_id":"node-99"},{"type":"click","element_id":"node-03"}]}'
                        }
                    }
                ]
            }
        )

        result = choose_click_targets(
            package_name="linglong-store",
            app_name="玲珑应用商店",
            visible_texts=("推荐", "排行榜", "办公"),
            scene_elements=(
                'node-01 text="推荐" center=(42,62) box=(20,54,60,71) score=0.99',
                'node-02 text="排行榜" center=(42,132) box=(20,120,68,143) score=0.99',
                'node-03 text="办公" center=(42,204) box=(20,196,68,213) score=0.99',
            ),
            accepted_texts=("推荐 安装 更新",),
            rejected_reasons=("same home page",),
            tried_targets=("推荐",),
            max_targets=2,
            config=type(
                "Config",
                (),
                {
                    "base_url": "http://127.0.0.1:8787/v1",
                    "model": "anthropic/glm-5",
                    "api_key": "",
                    "timeout": 120.0,
                },
            )(),
            session=session,
        )

        self.assertEqual(result, ("node-02", "node-03"))
        self.assertEqual(session.calls[0][0], "http://127.0.0.1:8787/v1/chat/completions")
        self.assertIn("Scene elements", session.calls[0][1]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
