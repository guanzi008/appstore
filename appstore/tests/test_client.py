import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from appstore.appstore_client import (
    AppStoreClient,
    AppLookupAmbiguousError,
    AuthenticationError,
    PayloadBuildError,
    REQUEST_TIMEOUT,
    UPLOAD_PUT_TIMEOUT,
    build_requests_session,
    build_submit_payload,
    choose_target_app_id,
)
from appstore.models import AppRecord, DebPackageInfo, UploadedFileRef
from appstore.platforms import ARCHITECTURES, PlatformMappingError, StoreArch


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, text: str = "ok") -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json_data


class _FakeErrorResponse(_FakeResponse):
    def raise_for_status(self):
        raise requests.HTTPError(f"{self.status_code} Server Error", response=self)


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.headers = {}
        self.cookies = {}
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs or None))
        return self._responses.pop(0)

    def post(self, url: str, json=None, **kwargs):
        payload = dict(kwargs)
        payload["json"] = json
        self.calls.append(("POST", url, payload))
        return self._responses.pop(0)


class ClientDecisionTests(unittest.TestCase):
    def _release(self, **overrides) -> SimpleNamespace:
        data = {
            "row_id": 2,
            "app_key": "labelnova",
            "release_key": "stable",
            "release_name": "稳定版",
            "region": "1",
            "note": "",
            "system_platform": "Deepin_23",
            "arch": "amd64",
            "baseline": "",
            "deb_path": Path("/tmp/labelnova_1.0.4-1_amd64.deb"),
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    def setUp(self) -> None:
        self.app = AppRecord(
            app_key="labelnova",
            app_name_zh="LabelNova",
            pkg_name="labelnova",
            category_id=1,
            website="https://mm.md/p/",
            short_desc_zh="简短介绍",
            full_desc_zh="详细介绍",
            icon_path=Path("/tmp/icon.png"),
            screenshot_paths=(Path("/tmp/1.png"), Path("/tmp/2.png"), Path("/tmp/3.png")),
            keywords_zh="标签,条码,打印",
            app_id_override="",
        )
        self.release = self._release()
        self.package = DebPackageInfo(
            pkg_name="labelnova",
            pkg_version="1.0.4-1",
            pkg_arch="amd64",
            pkg_size=2865688,
            sha256="50cfc999f2edef83d7ca2a765175342c83155aaad87de11f34cad5ddabe9a078",
            deb_path=self.release.deb_path,
        )
        self.uploads = {
            "icon": UploadedFileRef(kind="icon", file_save_key="icon-key", size=512, file_hash="abc"),
            "screenshots": (
                UploadedFileRef(kind="image", file_save_key="shot-1", size=111, file_hash="1"),
                UploadedFileRef(kind="image", file_save_key="shot-2", size=222, file_hash="2"),
                UploadedFileRef(kind="image", file_save_key="shot-3", size=333, file_hash="3"),
            ),
            "package": UploadedFileRef(kind="temppkg", file_save_key="pkg-key", size=2865688, file_hash="md5"),
        }

    def test_choose_target_app_id_prefers_override(self) -> None:
        app_id = choose_target_app_id([{"app_id": 42}], override="1096227")
        self.assertEqual(app_id, "1096227")

    def test_choose_target_app_id_rejects_ambiguous_matches(self) -> None:
        with self.assertRaises(AppLookupAmbiguousError):
            choose_target_app_id([{"app_id": 1}, {"app_id": 2}], override="")

    def test_choose_target_app_id_returns_empty_string_without_matches(self) -> None:
        self.assertEqual(choose_target_app_id([], override=""), "")

    def test_build_submit_payload_uses_store_codes_and_deb_metadata(self) -> None:
        payload = build_submit_payload(
            app=self.app,
            release=self.release,
            package_info=self.package,
            uploads=self.uploads,
            target_app_id="1096227",
        )

        self.assertEqual(payload["operate_type"], 52)
        self.assertEqual(payload["app_id"], "1096227")
        self.assertEqual(payload["app_info"]["app_basic_info"]["category_id"], 1)
        self.assertEqual(payload["app_info"]["app_fit_info"]["system_platform"], [{"code": 11}])
        self.assertEqual(payload["app_info"]["app_fit_info"]["arch"], [{"code": "4"}])
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["pkg_version"], "1.0.4-1")
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["pkg_arch"], "4")
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["file_save_key"], "pkg-key")

    def test_build_submit_payload_rejects_invalid_region(self) -> None:
        release = self._release(region="cn")

        with self.assertRaisesRegex(PayloadBuildError, "invalid region"):
            build_submit_payload(
                app=self.app,
                release=release,
                package_info=self.package,
                uploads=self.uploads,
                target_app_id="1096227",
            )

    def test_build_submit_payload_rejects_arch_mismatch(self) -> None:
        release = self._release(arch="arm64")

        with patch.dict(ARCHITECTURES, {"arm64": StoreArch("arm64", "5", "ARM64")}, clear=False):
            with self.assertRaisesRegex(PayloadBuildError, "arch mismatch"):
                build_submit_payload(
                    app=self.app,
                    release=release,
                    package_info=self.package,
                    uploads=self.uploads,
                    target_app_id="1096227",
                )

    def test_build_submit_payload_accepts_equivalent_arch_aliases(self) -> None:
        release = self._release(arch="x86_64")

        payload = build_submit_payload(
            app=self.app,
            release=release,
            package_info=self.package,
            uploads=self.uploads,
            target_app_id="1096227",
        )

        self.assertEqual(payload["app_info"]["app_fit_info"]["arch"], [{"code": "4"}])
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["pkg_arch"], "4")

    def test_build_submit_payload_preserves_baseline(self) -> None:
        release = self._release(baseline="23.0.0")

        payload = build_submit_payload(
            app=self.app,
            release=release,
            package_info=self.package,
            uploads=self.uploads,
            target_app_id="1096227",
        )

        self.assertEqual(payload["app_info"]["app_fit_info"]["baseline"], ["23.0.0"])
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["baseline"], ["23.0.0"])
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["supBlineVer"], "23.0.0")

    def test_build_submit_payload_rejects_unsupported_platform_mapping(self) -> None:
        release = self._release(system_platform="UnknownOS")

        with self.assertRaisesRegex(PlatformMappingError, "UnknownOS"):
            build_submit_payload(
                app=self.app,
                release=release,
                package_info=self.package,
                uploads=self.uploads,
                target_app_id="1096227",
            )

    def test_build_submit_payload_rejects_unsupported_arch_mapping(self) -> None:
        package = DebPackageInfo(
            pkg_name=self.package.pkg_name,
            pkg_version=self.package.pkg_version,
            pkg_arch="sparc64",
            pkg_size=self.package.pkg_size,
            sha256=self.package.sha256,
            deb_path=self.package.deb_path,
        )
        release = self._release(arch="sparc64")

        with self.assertRaisesRegex(PlatformMappingError, "sparc64"):
            build_submit_payload(
                app=self.app,
                release=release,
                package_info=package,
                uploads=self.uploads,
                target_app_id="1096227",
            )

    def test_build_submit_payload_reuses_existing_detail_for_update(self) -> None:
        detail = {
            "app_basic_info": {
                "category_id": 9,
                "website": "https://existing.example/app",
                "region": "1",
                "default_lan": "zh_CN",
                "pkg_mode": 0,
                "inAppPayment": 0,
            },
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "旧应用名",
                    "brief_info": "旧简介",
                    "desc_info": "旧详情",
                    "update_desc": "旧更新说明",
                    "icon_save_key": "existing-icon",
                    "appScreenShotList": [
                        {"screen_shot_key": "existing-shot-1", "image_mode": 1, "sort": 0},
                        {"screen_shot_key": "existing-shot-2", "image_mode": 1, "sort": 1},
                        {"screen_shot_key": "existing-shot-3", "image_mode": 1, "sort": 2},
                    ],
                }
            ],
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "system_platform": [{"code": 11}],
                "region": [{"code": 1}],
                "arch": [{"code": 4}],
                "baseline": None,
            },
            "app_origin_pkgs": [
                {
                    "pkg_name": "labelnova",
                    "pkg_version": "1.0.3-1",
                    "pkg_arch": "4",
                    "pkgArch": "X86",
                    "pkgType": 11,
                    "pkg_mode": 0,
                    "pkg_size": 2048,
                    "sha256": "old-sha",
                    "file_save_key": "old-pkg",
                    "progressPercent": 101,
                    "supSys": "11",
                    "supBlineVer": "",
                    "unsupportBlineVers": "",
                    "systemStr": "社区版V23",
                }
            ],
        }
        release = self._release(note="只改更新内容")

        payload = build_submit_payload(
            app=self.app,
            release=release,
            package_info=self.package,
            uploads={"package": self.uploads["package"]},
            target_app_id="1096227",
            existing_app_detail=detail,
        )

        lan_info = payload["app_info"]["app_lan_infos"][0]
        self.assertEqual(lan_info["icon_save_key"], "existing-icon")
        self.assertEqual(lan_info["update_desc"], "只改更新内容")
        self.assertEqual(payload["app_info"]["app_basic_info"]["category_id"], 9)
        self.assertEqual(len(payload["app_info"]["app_origin_pkgs"]), 2)
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][0]["file_save_key"], "old-pkg")
        self.assertEqual(payload["app_info"]["app_origin_pkgs"][1]["file_save_key"], "pkg-key")

    def test_build_submit_payload_replaces_existing_screenshots_when_provided(self) -> None:
        detail = {
            "app_basic_info": {"category_id": 9, "website": "https://existing.example/app", "region": "1"},
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "旧应用名",
                    "brief_info": "旧简介",
                    "desc_info": "旧详情",
                    "update_desc": "旧更新说明",
                    "icon_save_key": "existing-icon",
                    "appScreenShotList": [
                        {"screen_shot_key": "existing-shot-1", "image_mode": 1, "sort": 0},
                    ],
                }
            ],
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "system_platform": [{"code": 11}],
                "region": [{"code": 1}],
                "arch": [{"code": 4}],
                "baseline": None,
            },
            "app_origin_pkgs": [],
        }
        release = self._release(note="替换截图")

        payload = build_submit_payload(
            app=self.app,
            release=release,
            package_info=self.package,
            uploads={
                "package": self.uploads["package"],
                "screenshots": (
                    UploadedFileRef(kind="image", file_save_key="new-shot-1", size=10, file_hash="hash-1"),
                    UploadedFileRef(kind="image", file_save_key="new-shot-2", size=11, file_hash="hash-2"),
                    UploadedFileRef(kind="image", file_save_key="new-shot-3", size=12, file_hash="hash-3"),
                ),
            },
            target_app_id="1096227",
            existing_app_detail=detail,
        )

        lan_info = payload["app_info"]["app_lan_infos"][0]
        self.assertEqual(
            [shot["screen_shot_key"] for shot in lan_info["appScreenShotList"]],
            ["new-shot-1", "new-shot-2", "new-shot-3"],
        )
        self.assertEqual(lan_info["icon_save_key"], "existing-icon")

    def test_build_submit_payload_applies_existing_app_overrides_when_provided(self) -> None:
        detail = {
            "app_basic_info": {"category_id": 9, "website": "https://existing.example/app", "region": "1", "default_lan": "zh_CN"},
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "name": "旧应用名",
                    "brief_info": "旧简介",
                    "desc_info": "旧详情",
                    "update_desc": "旧更新说明",
                    "icon_save_key": "existing-icon",
                    "appScreenShotList": [
                        {"screen_shot_key": "existing-shot-1", "image_mode": 1, "sort": 0},
                    ],
                }
            ],
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "system_platform": [{"code": 11}],
                "region": [{"code": 1}],
                "arch": [{"code": 4}],
                "baseline": None,
            },
            "app_origin_pkgs": [],
        }
        release = self._release(note="改基础信息")

        payload = build_submit_payload(
            app=self.app,
            release=release,
            package_info=self.package,
            uploads={"package": self.uploads["package"]},
            target_app_id="1096227",
            existing_app_detail=detail,
            existing_app_overrides={
                "app_name_zh": "新应用名",
                "short_desc_zh": "新简介",
                "full_desc_zh": "新详情",
                "category_id": 2,
                "website": "https://override.example/app",
            },
        )

        lan_info = payload["app_info"]["app_lan_infos"][0]
        self.assertEqual(lan_info["name"], "新应用名")
        self.assertEqual(lan_info["brief_info"], "新简介")
        self.assertEqual(lan_info["desc_info"], "新详情")
        self.assertEqual(payload["app_info"]["app_basic_info"]["category_id"], 2)
        self.assertEqual(payload["app_info"]["app_basic_info"]["website"], "https://override.example/app")

    def test_build_submit_payload_builds_multilingual_copy_for_other_regions(self) -> None:
        release = self._release(region="1")

        payload = build_submit_payload(
            app=self.app,
            release=release,
            package_info=self.package,
            uploads=self.uploads,
            target_app_id="1096227",
            localized_lan_texts={
                "zh_CN": {
                    "name": "LabelNova",
                    "brief_info": "简短介绍",
                    "desc_info": "详细介绍",
                    "update_desc": "",
                },
                "en_US": {
                    "name": "LabelNova",
                    "brief_info": "Brief intro",
                    "desc_info": "Full description",
                    "update_desc": "",
                },
            },
            desired_lans=("zh_CN", "en_US"),
        )

        self.assertEqual([item["lan"] for item in payload["app_info"]["app_lan_infos"]], ["zh_CN", "en_US"])
        self.assertEqual(payload["app_info"]["app_lan_infos"][1]["brief_info"], "Brief intro")


class ClientSessionTests(unittest.TestCase):
    def test_ensure_ok_includes_error_payload_for_http_failures(self) -> None:
        response = _FakeErrorResponse(
            status_code=500,
            json_data={"status": 500, "desc": "应用正在审核中，暂不可重复提审"},
            text='{"status":500,"desc":"应用正在审核中，暂不可重复提审"}',
        )

        with self.assertRaisesRegex(RuntimeError, "应用正在审核中，暂不可重复提审"):
            AppStoreClient._ensure_ok(response, "submit_app")

    def test_build_requests_session_copies_cookies_and_token_headers(self) -> None:
        session = build_requests_session(
            cookies=[
                {"name": "sid", "value": "cookie-value", "domain": "appstore-dev.uniontech.com", "path": "/"},
            ],
            local_storage={
                "token": "local-token",
                "Authorization": "Bearer local-auth",
            },
            session_storage={"access_token": "session-token"},
        )

        self.assertEqual(session.cookies.get("sid"), "cookie-value")
        self.assertEqual(session.headers["Authorization"], "Bearer local-auth")
        self.assertEqual(session.headers["token"], "session-token")
        self.assertEqual(session.headers["Origin"], "https://appstore-dev.uniontech.com")
        self.assertEqual(session.headers["Referer"], "https://appstore-dev.uniontech.com/")

    def test_build_requests_session_falls_back_to_access_token_cookie(self) -> None:
        session = build_requests_session(
            cookies=[
                {"name": "access-token", "value": "cookie-access-token", "domain": "appstore-dev.uniontech.com", "path": "/"},
            ],
            local_storage={},
            session_storage={},
        )

        self.assertEqual(session.cookies.get("access-token"), "cookie-access-token")
        self.assertEqual(session.headers["Authorization"], "cookie-access-token")
        self.assertEqual(session.headers["token"], "cookie-access-token")

    def test_fetch_linglong_system_lines_hits_dictionary_endpoint(self) -> None:
        session = _FakeSession([_FakeResponse(json_data={"datas": [{"dictLabel": "communityV23", "dictValue": "11"}], "status": 200})])
        client = AppStoreClient(session=session)

        rows = client.fetch_linglong_system_lines()

        self.assertEqual(rows, [{"dictLabel": "communityV23", "dictValue": "11"}])
        self.assertEqual(session.calls[0][0], "GET")
        self.assertIn("/system/dict/data/type/linglong_app_sup_sys", session.calls[0][1])
        self.assertEqual(session.calls[0][2]["timeout"], REQUEST_TIMEOUT)

    def test_fetch_adapt_info_hits_adapt_info_endpoint(self) -> None:
        session = _FakeSession([_FakeResponse(json_data={"datas": {"systemPlatformList": [], "shopVersionList": []}, "status": 200})])
        client = AppStoreClient(session=session)

        payload = client.fetch_adapt_info()

        self.assertEqual(payload["datas"]["systemPlatformList"], [])
        self.assertEqual(session.calls[0][0], "GET")
        self.assertIn("/store-dev-app/adapt-info/", session.calls[0][1])
        self.assertEqual(session.calls[0][2]["timeout"], REQUEST_TIMEOUT)

    def test_build_requests_session_ignores_refresh_tokens_for_api_headers(self) -> None:
        session = build_requests_session(
            local_storage={
                "refresh_token": "refresh-only",
                "access_token": "access-value",
            },
            session_storage={"refreshToken": "session-refresh"},
        )

        self.assertEqual(session.headers["Authorization"], "access-value")
        self.assertEqual(session.headers["token"], "access-value")
        self.assertNotEqual(session.headers["Authorization"], "refresh-only")

    @patch("appstore.appstore_client.build_requests_session")
    @patch.object(AppStoreClient, "_login_and_export_state")
    def test_login_wraps_auth_verification_failure_as_authentication_error(
        self,
        export_state_mock,
        build_session_mock,
    ) -> None:
        export_state_mock.return_value = ([{"name": "sid", "value": "cookie"}], {}, {})
        fake_session = _FakeSession([_FakeResponse(json_data={"status": 401, "message": "denied"})])
        build_session_mock.return_value = fake_session
        client = AppStoreClient()

        with self.assertRaises(AuthenticationError):
            client.login("demo", "secret")

        self.assertEqual(fake_session.calls[0][0], "GET")
        self.assertIn("/store-dev-auth/dev_info", fake_session.calls[0][1])
        self.assertEqual(fake_session.calls[0][2]["timeout"], REQUEST_TIMEOUT)


class ClientListAppsTests(unittest.TestCase):
    def test_list_apps_returns_top_level_json_list_unchanged(self) -> None:
        payload = [{"pkg_name": "alpha"}, {"pkg_name": "beta"}]
        client = AppStoreClient(session=_FakeSession([_FakeResponse(json_data=payload)]))

        result = client.list_apps()

        self.assertEqual(result, payload)

    def test_list_apps_raises_for_unknown_success_payload_shape(self) -> None:
        client = AppStoreClient(session=_FakeSession([_FakeResponse(json_data={"status": 200, "datas": {"count": 2}})]))

        with self.assertRaisesRegex(RuntimeError, "unexpected payload"):
            client.list_apps()

    def test_find_apps_by_pkg_name_searches_multiple_pages(self) -> None:
        client = AppStoreClient(
            session=_FakeSession(
                [
                    _FakeResponse(json_data={"status": 200, "datas": {"list": [{"pkg_name": "alpha"}, {"pkg_name": "beta"}]}}),
                    _FakeResponse(json_data={"status": 200, "datas": {"list": [{"pkg_name": "target"}, {"pkg_name": "gamma"}]}}),
                    _FakeResponse(json_data={"status": 200, "datas": {"list": []}}),
                ]
            )
        )

        matches = client.find_apps_by_pkg_name("target")

        self.assertEqual(matches, [{"pkg_name": "target"}])
        self.assertEqual(len(client.session.calls), 3)
        self.assertEqual(client.session.calls[0][2]["params"], {"pageNum": 1, "pageSize": 200})
        self.assertEqual(client.session.calls[1][2]["params"], {"pageNum": 2, "pageSize": 200})
        self.assertEqual(client.session.calls[2][2]["params"], {"pageNum": 3, "pageSize": 200})
        self.assertEqual(client.session.calls[0][2]["timeout"], REQUEST_TIMEOUT)


class ClientUploadTests(unittest.TestCase):
    @patch("appstore.appstore_client.requests.put")
    def test_upload_file_bytes_performs_begin_put_end_cycle(self, put_mock) -> None:
        session = _FakeSession(
            [
                _FakeResponse(
                    json_data={
                        "datas": {
                            "uploadUrl": "https://upload.example.com/object",
                            "fileSaveKey": "save-key",
                            "id": "record-1",
                            "upload_id": "upload-1",
                        }
                    }
                ),
                _FakeResponse(json_data={"datas": {"fileSaveKey": "save-key"}}),
            ]
        )
        put_mock.return_value = _FakeResponse(status_code=200)
        client = AppStoreClient(session=session)

        uploaded = client.upload_file_bytes("package.deb", b"deb-bytes", "temppkg")

        self.assertEqual(uploaded, UploadedFileRef(kind="temppkg", file_save_key="save-key", size=9, file_hash="b445841fe9fd447fb431bdc1db8e3191"))
        self.assertEqual(session.calls[0][0], "POST")
        self.assertIn("/store-file/upload/begin", session.calls[0][1])
        self.assertEqual(session.calls[0][2]["json"]["fileName"], "package.deb")
        self.assertEqual(session.calls[0][2]["json"]["size"], 9)
        self.assertEqual(session.calls[0][2]["json"]["md5"], "b445841fe9fd447fb431bdc1db8e3191")
        self.assertEqual(session.calls[0][2]["timeout"], REQUEST_TIMEOUT)
        put_mock.assert_called_once_with(
            "https://upload.example.com/object",
            data=b"deb-bytes",
            headers={"Content-Type": "application/octet-stream"},
            timeout=UPLOAD_PUT_TIMEOUT,
        )
        self.assertEqual(session.calls[1][0], "POST")
        self.assertIn("/store-file/upload/end", session.calls[1][1])
        self.assertEqual(
            session.calls[1][2]["json"],
            {
                "chunks": ["b445841fe9fd447fb431bdc1db8e3191"],
                "hash": "b445841fe9fd447fb431bdc1db8e3191",
                "file_upload_record_id": "record-1",
                "size": 9,
                "upload_id": "upload-1",
                "file_save_key": "save-key",
                "status": 2,
            },
        )
        self.assertEqual(session.calls[1][2]["timeout"], REQUEST_TIMEOUT)

    @patch("appstore.appstore_client.requests.put")
    def test_upload_file_bytes_retries_transient_put_failures(self, put_mock) -> None:
        session = _FakeSession(
            [
                _FakeResponse(
                    json_data={
                        "datas": {
                            "uploadUrl": "https://upload.example.com/object",
                            "fileSaveKey": "save-key",
                            "id": "record-1",
                            "upload_id": "upload-1",
                        }
                    }
                ),
                _FakeResponse(json_data={"datas": {"fileSaveKey": "save-key"}}),
            ]
        )
        put_mock.side_effect = [
            requests.exceptions.SSLError("EOF occurred in violation of protocol"),
            _FakeResponse(status_code=200),
        ]
        client = AppStoreClient(session=session)

        uploaded = client.upload_file_bytes("package.deb", b"deb-bytes", "temppkg")

        self.assertEqual(uploaded.file_save_key, "save-key")
        self.assertEqual(put_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
