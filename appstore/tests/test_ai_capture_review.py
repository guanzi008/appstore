import unittest

from appstore.ai_capture_review import review_capture_text


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _FakeResponse(self.payload)


class AICaptureReviewTests(unittest.TestCase):
    def test_review_capture_text_parses_useful_result(self) -> None:
        session = _FakeSession(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"useful": true, "page_kind": "ranking", "reason": "distinct ranking page"}'
                        }
                    }
                ]
            }
        )

        result = review_capture_text(
            package_name="linglong-store",
            app_name="玲珑应用商店",
            current_text="推荐 排行 查看详情 安装 更新",
            accepted_texts=("推荐 安装 更新",),
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

        self.assertTrue(result.useful)
        self.assertEqual(result.page_kind, "ranking")
        self.assertEqual(result.reason, "distinct ranking page")
        self.assertEqual(session.calls[0]["url"], "http://127.0.0.1:8787/v1/chat/completions")

    def test_review_capture_text_parses_rejected_result(self) -> None:
        session = _FakeSession(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"useful": false, "page_kind": "home", "reason": "same home page with only carousel changes"}'
                        }
                    }
                ]
            }
        )

        result = review_capture_text(
            package_name="linglong-store",
            app_name="玲珑应用商店",
            current_text="推荐 全部 排行 更新 查看详情 安装",
            accepted_texts=("推荐 全部 排行 更新 查看详情 安装",),
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

        self.assertFalse(result.useful)
        self.assertEqual(result.reason, "same home page with only carousel changes")


if __name__ == "__main__":
    unittest.main()
