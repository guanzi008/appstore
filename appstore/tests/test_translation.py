import unittest

import requests

from appstore.translation import TranslationConfig, desired_languages_for_regions, translate_listing_texts


class _FakeSession:
    def __init__(self, response_body: dict) -> None:
        self.response_body = response_body
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _FakeResponse(self.response_body)


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class _FailingSession:
    def post(self, url, json=None, headers=None, timeout=None):
        raise requests.ConnectionError("connection refused")


class TranslationTests(unittest.TestCase):
    def test_desired_languages_for_regions_adds_english_for_other_regions(self) -> None:
        self.assertEqual(desired_languages_for_regions(("1",)), ("zh_CN",))
        self.assertEqual(desired_languages_for_regions(("1", "2")), ("zh_CN", "en_US"))

    def test_translate_listing_texts_uses_openai_compatible_shape(self) -> None:
        session = _FakeSession(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"name":"Demo App","brief_info":"Brief intro","desc_info":"Full description","update_desc":"Fix login issues"}'
                        }
                    }
                ]
            }
        )

        payload = translate_listing_texts(
            app_name_zh="演示应用",
            short_desc_zh="简短说明",
            full_desc_zh="详细说明",
            update_desc_zh="修复登录问题",
            target_lan="en_US",
            config=TranslationConfig(
                base_url="http://127.0.0.1:8787/v1",
                model="openai-codex/gpt-5.4",
                api_key="secret",
            ),
            session=session,
        )

        self.assertEqual(payload["name"], "Demo App")
        self.assertEqual(payload["update_desc"], "Fix login issues")
        self.assertEqual(session.calls[0]["url"], "http://127.0.0.1:8787/v1/chat/completions")
        self.assertEqual(session.calls[0]["json"]["model"], "openai-codex/gpt-5.4")
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer secret")

    def test_translate_listing_texts_raises_readable_error_when_service_unreachable(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "无法连接英文文案生成服务"):
            translate_listing_texts(
                app_name_zh="演示应用",
                short_desc_zh="简短说明",
                full_desc_zh="详细说明",
                update_desc_zh="修复登录问题",
                target_lan="en_US",
                config=TranslationConfig(
                    base_url="http://127.0.0.1:8787/v1",
                    model="openai-codex/gpt-5.4",
                    api_key="secret",
                ),
                session=_FailingSession(),
            )
