import unittest

from appstore.ai_capture_planner import AICapturePlannerConfig, plan_capture_steps


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


class AICapturePlannerTests(unittest.TestCase):
    def test_plan_capture_steps_uses_openai_compatible_chat_completions_shape(self) -> None:
        session = _FakeSession(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"steps":["wait-window:30","screenshot:screen-01","click-text:排行榜","screenshot:screen-02"]}'
                        }
                    }
                ]
            }
        )

        steps = plan_capture_steps(
            prompt="打开设置页再截图",
            package_name="demo-app",
            app_name="Demo App",
            config=AICapturePlannerConfig(
                base_url="http://127.0.0.1:8787/v1",
                model="openai-codex/gpt-5.4",
                api_key="secret",
            ),
            session=session,
        )

        self.assertEqual(
            steps,
            ("wait-window:30", "screenshot:screen-01", "click-text:排行榜", "screenshot:screen-02"),
        )
        self.assertEqual(session.calls[0][0], "http://127.0.0.1:8787/v1/chat/completions")
        self.assertEqual(session.calls[0][1]["model"], "openai-codex/gpt-5.4")
        self.assertEqual(session.calls[0][2]["Authorization"], "Bearer secret")

    def test_plan_capture_steps_accepts_fenced_json(self) -> None:
        session = _FakeSession(
            {
                "choices": [
                    {
                        "message": {
                            "content": "```json\n{\"steps\":[\"wait-window:20\",\"screenshot:screen-01\"]}\n```"
                        }
                    }
                ]
            }
        )

        steps = plan_capture_steps(
            prompt="截图主页",
            package_name="demo-app",
            app_name="Demo App",
            config=AICapturePlannerConfig(
                base_url="http://127.0.0.1:8787/v1",
                model="openai-codex/gpt-5.4",
            ),
            session=session,
        )

        self.assertEqual(steps, ("wait-window:20", "screenshot:screen-01"))

    def test_plan_capture_steps_includes_runtime_ocr_context(self) -> None:
        session = _FakeSession(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"steps":["click-text:排行榜","sleep:2","screenshot:ranking"]}'
                        }
                    }
                ]
            }
        )

        plan_capture_steps(
            prompt="继续探索新页面",
            package_name="demo-app",
            app_name="Demo App",
            current_visible_texts=("推荐", "全部", "排行榜"),
            clickable_texts=("排行榜", "查看详情"),
            config=AICapturePlannerConfig(
                base_url="http://127.0.0.1:8787/v1",
                model="anthropic/glm-5",
            ),
            session=session,
        )

        user_content = session.calls[0][1]["messages"][1]["content"]
        self.assertIn("Current visible OCR texts: 推荐 | 全部 | 排行榜", user_content)
        self.assertIn("Clickable OCR targets: 排行榜 | 查看详情", user_content)


if __name__ == "__main__":
    unittest.main()
