from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime

import requests
from pyppeteer import launch

from appstore.models import AppRecord, DebPackageInfo, ReleaseRecord, UploadedFileRef
from appstore.platforms import resolve_store_arch, resolve_store_platform
from appstore.update_payload import (
    build_reused_basic_info,
    build_reused_fit_info,
    build_reused_lan_infos,
    merge_origin_pkgs,
)

ORIGIN = "https://appstore-dev.uniontech.com"
BASE = f"{ORIGIN}/devprod-api"
REQUEST_TIMEOUT = 120
UPLOAD_PUT_TIMEOUT = 900
UPLOAD_PUT_ATTEMPTS = 3


class AppLookupAmbiguousError(RuntimeError):
    pass


class PayloadBuildError(ValueError):
    pass


class AuthenticationError(RuntimeError):
    pass


class AppStoreProtocolError(RuntimeError):
    pass


def choose_target_app_id(matches: list[dict], override: str) -> str:
    if override:
        return override
    if not matches:
        return ""
    if len(matches) > 1:
        raise AppLookupAmbiguousError("multiple app matches found; set app_id_override")
    return str(matches[0]["app_id"])


def _upload_time_label() -> str:
    now = datetime.now()
    return f"{now.year}-{now.month}-{now.day}  {now.hour}:{now.minute}:{now.second}  "


def _resolve_region_code(region: str) -> int:
    normalized = region.strip() if region else "1"
    try:
        return int(normalized)
    except ValueError as exc:
        raise PayloadBuildError(f"invalid region value: {region}") from exc


def _resolve_baseline_values(baseline: str) -> list[str]:
    normalized = baseline.strip()
    if not normalized:
        return []
    return [normalized]


def _pick_auth_headers(*, local_storage: dict | None = None, session_storage: dict | None = None) -> tuple[str, str]:
    storage_chain = (session_storage or {}, local_storage or {})
    direct_authorization = ("authorization", "authorizationtoken", "auth", "authtoken")
    access_token_keys = ("access_token", "accesstoken", "token", "id_token", "idtoken")
    refresh_markers = ("refresh",)

    authorization_value = ""
    token_value = ""
    for storage in storage_chain:
        for key, value in storage.items():
            if not value:
                continue
            normalized_key = str(key).replace("-", "").replace("_", "").lower()
            if any(marker in normalized_key for marker in refresh_markers):
                continue
            if not authorization_value and normalized_key in direct_authorization:
                authorization_value = value
            if not token_value and normalized_key in access_token_keys:
                token_value = value
        if authorization_value and token_value:
            break

    if not authorization_value:
        authorization_value = token_value
    if not token_value:
        token_value = authorization_value
    return authorization_value, token_value


def _pick_auth_cookie(cookies: list[dict] | None = None) -> str:
    for cookie in cookies or []:
        normalized_name = str(cookie.get("name", "")).replace("-", "").replace("_", "").lower()
        if normalized_name in {"accesstoken", "authorization", "token"}:
            value = str(cookie.get("value", "")).strip()
            if value:
                return value
    return ""


def _normalize_app_list_payload(payload: dict | list, action: str) -> list[dict]:
    if isinstance(payload, list):
        return payload
    datas = payload.get("datas", payload)
    if isinstance(datas, list):
        return datas
    if isinstance(datas, dict):
        for key in ("list", "records", "rows"):
            value = datas.get(key)
            if isinstance(value, list):
                return value
    raise AppStoreProtocolError(f"{action} returned unexpected payload: {payload}")


def _response_error_detail(response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, (dict, list)):
        return str(payload)
    text = str(getattr(response, "text", "") or "").strip()
    return text[:500]


def build_submit_payload(
    *,
    app: AppRecord,
    release: ReleaseRecord,
    package_info: DebPackageInfo,
    uploads: dict[str, UploadedFileRef | tuple[UploadedFileRef, ...]],
    target_app_id: str,
    existing_app_detail: dict | None = None,
) -> dict:
    platform = resolve_store_platform(release.system_platform)
    release_arch = resolve_store_arch(release.arch)
    package_arch = resolve_store_arch(package_info.pkg_arch)
    if release_arch.code != package_arch.code:
        raise PayloadBuildError(
            f"arch mismatch between release '{release.arch}' and package '{package_info.pkg_arch}'"
        )
    arch = package_arch
    region_code = _resolve_region_code(release.region)
    baseline_values = _resolve_baseline_values(release.baseline)
    package_upload = uploads["package"]
    package_install_mode = 1
    origin_pkg = {
        "pkg_name": package_info.pkg_name,
        "pkg_version": package_info.pkg_version,
        "pkg_arch": arch.code,
        "pkgArch": arch.label,
        "pkg_mode": 0,
        "pkgChannel": None,
        "pkgType": platform.pkg_type,
        "progressPercent": 100,
        "sums": 0,
        "index": 0,
        "pkg_size": package_info.pkg_size,
        "sha256": package_info.sha256,
        "upload_time": _upload_time_label(),
        "file_save_key": package_upload.file_save_key,
        "system_platform": [platform.code],
        "systemStr": platform.system_label,
        "supSys": platform.sup_sys,
        "baseline": baseline_values,
        "unsupportBaseline": [],
        "supBlineVer": release.baseline.strip(),
        "unsupportBlineVers": "",
    }

    if existing_app_detail is not None:
        app_info = {
            "app_lan_infos": build_reused_lan_infos(
                existing_app_detail,
                release_note=release.note,
            ),
            "app_basic_info": build_reused_basic_info(
                existing_app_detail,
                package_install_mode=package_install_mode,
                region=str(region_code),
            ),
            "app_fit_info": build_reused_fit_info(
                existing_app_detail,
                fit_system_codes=[str(platform.code)],
                fit_baseline_ids=baseline_values,
                fit_unsupported_ids=[],
                fit_arch_codes=[str(arch.code)],
                region_code=region_code,
            ),
            "app_origin_pkgs": merge_origin_pkgs(existing_app_detail, [origin_pkg]),
        }
    else:
        screenshots = uploads["screenshots"]
        icon_upload = uploads["icon"]
        app_info = {
            "app_lan_infos": [
                {
                    "lan": "zh_CN",
                    "label": "中文（简体）",
                    "lanStr": "中文（简体）",
                    "name": app.app_name_zh,
                    "brief_info": app.short_desc_zh,
                    "desc_info": app.full_desc_zh,
                    "dev_name": "",
                    "icon_save_key": icon_upload.file_save_key,
                    "appScreenShotList": [
                        {"screen_shot_key": shot.file_save_key, "image_mode": 1, "size": shot.size, "sort": index}
                        for index, shot in enumerate(screenshots)
                    ],
                }
            ],
            "app_basic_info": {
                "default_lan": "zh_CN",
                "pkg_mode": 0,
                "pkgInstallMode": package_install_mode,
                "inAppPayment": 0,
                "category_id": app.category_id,
                "website": app.website,
                "region": str(region_code),
            },
            "app_fit_info": {
                "system_mode": [{"code": 1}],
                "baseline": baseline_values,
                "unsupportBaseline": [],
                "system_platform": [{"code": platform.code}],
                "region": [{"code": region_code}],
                "arch": [{"code": arch.code}],
                "cpu_clip": [],
                "motherboard": [],
                "supWayland": 0,
            },
            "app_origin_pkgs": [origin_pkg],
        }

    payload = {
        "operate_type": 52,
        "app_info": app_info,
    }
    if target_app_id:
        payload["app_id"] = target_app_id
    return payload


def build_requests_session(
    *,
    cookies: list[dict] | None = None,
    local_storage: dict | None = None,
    session_storage: dict | None = None,
) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Origin": ORIGIN,
            "Referer": f"{ORIGIN}/",
            "User-Agent": "Mozilla/5.0",
        }
    )
    for cookie in cookies or []:
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )

    authorization_value, token_value = _pick_auth_headers(
        local_storage=local_storage,
        session_storage=session_storage,
    )
    cookie_token_value = _pick_auth_cookie(cookies)
    if not authorization_value:
        authorization_value = cookie_token_value
    if not token_value:
        token_value = cookie_token_value

    if authorization_value:
        session.headers["Authorization"] = authorization_value
    elif token_value:
        session.headers["Authorization"] = token_value
    if token_value:
        session.headers["token"] = token_value
    elif authorization_value:
        session.headers["token"] = authorization_value
    return session


class AppStoreClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @staticmethod
    def _ensure_ok(response, action: str) -> dict | list:
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            detail = _response_error_detail(response)
            if detail:
                raise RuntimeError(f"{action} failed with HTTP {status_code}: {detail}")
            raise RuntimeError(f"{action} failed with HTTP {status_code}")
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise AppStoreProtocolError(f"{action} returned unexpected payload: {payload}")
        status = payload.get("status")
        if status not in (None, 200):
            raise RuntimeError(f"{action} failed: {payload}")
        return payload

    async def _login_and_export_state(self, username: str, password: str) -> tuple[list[dict], dict, dict]:
        browser = await launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.newPage()
            await page.goto(f"{ORIGIN}/#/index", {"waitUntil": "networkidle2", "timeout": 120000})
            await page.waitForSelector('input[type="text"]', {"timeout": 120000})
            inputs = await page.querySelectorAll("input")
            if len(inputs) < 2:
                raise AuthenticationError("login page did not expose username/password inputs")
            await inputs[0].type(username, {"delay": 30})
            await inputs[1].type(password, {"delay": 30})
            await page.click("button")
            await page.waitForNavigation({"waitUntil": "networkidle2", "timeout": 120000})
            cookies = await page.cookies()
            for _ in range(15):
                if _pick_auth_cookie(cookies):
                    break
                await asyncio.sleep(1)
                cookies = await page.cookies()
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
            return cookies, local_storage, session_storage
        finally:
            await browser.close()

    def login(self, username: str, password: str) -> None:
        cookies, local_storage, session_storage = asyncio.run(self._login_and_export_state(username, password))
        self.session = build_requests_session(
            cookies=cookies,
            local_storage=local_storage,
            session_storage=session_storage,
        )
        try:
            self._ensure_ok(self.session.get(f"{BASE}/store-dev-auth/dev_info", timeout=REQUEST_TIMEOUT), "dev_info")
        except Exception as exc:
            raise AuthenticationError("authentication verification failed") from exc

    def fetch_linglong_system_lines(self) -> list[dict]:
        payload = self._ensure_ok(
            self.session.get(
                f"{BASE}/system/dict/data/type/linglong_app_sup_sys",
                timeout=REQUEST_TIMEOUT,
            ),
            "fetch_linglong_system_lines",
        )
        if not isinstance(payload, dict):
            raise AppStoreProtocolError(f"fetch_linglong_system_lines returned unexpected payload: {payload}")
        rows = payload.get("datas", [])
        if not isinstance(rows, list):
            raise AppStoreProtocolError(f"fetch_linglong_system_lines returned unexpected payload: {payload}")
        return list(rows)

    def fetch_adapt_info(self) -> dict:
        payload = self._ensure_ok(
            self.session.get(
                f"{BASE}/store-dev-app/adapt-info/",
                timeout=REQUEST_TIMEOUT,
            ),
            "fetch_adapt_info",
        )
        if not isinstance(payload, dict):
            raise AppStoreProtocolError(f"fetch_adapt_info returned unexpected payload: {payload}")
        return payload

    def list_apps(self, page_num: int = 1, page_size: int = 200) -> list[dict]:
        payload = self._ensure_ok(
            self.session.get(
                f"{BASE}/store-dev-app/app",
                params={"pageNum": page_num, "pageSize": page_size},
                timeout=REQUEST_TIMEOUT,
            ),
            "list_apps",
        )
        return _normalize_app_list_payload(payload, "list_apps")

    def find_apps_by_pkg_name(self, pkg_name: str) -> list[dict]:
        normalized = pkg_name.strip()
        matches: list[dict] = []
        page_num = 1
        page_size = 200
        while True:
            page = self.list_apps(page_num=page_num, page_size=page_size)
            if not page:
                return matches
            matches.extend(row for row in page if str(row.get("pkg_name", "")).strip() == normalized)
            page_num += 1

    def get_app_detail(self, detail_id: str) -> dict:
        payload = self._ensure_ok(
            self.session.get(
                f"{BASE}/store-dev-app/app/{detail_id}/detail",
                timeout=REQUEST_TIMEOUT,
            ),
            "get_app_detail",
        )
        if not isinstance(payload, dict):
            raise AppStoreProtocolError(f"get_app_detail returned unexpected payload: {payload}")
        datas = payload.get("datas")
        if not isinstance(datas, dict):
            raise AppStoreProtocolError(f"get_app_detail returned unexpected payload: {payload}")
        return datas

    def submit_payload(self, payload: dict) -> dict:
        response = self.session.post(
            f"{BASE}/store-dev-app/app",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        validated = self._ensure_ok(response, "submit_app")
        if not isinstance(validated, dict):
            raise AppStoreProtocolError(f"submit_app returned unexpected payload: {validated}")
        return validated

    def upload_file_bytes(self, filename: str, data: bytes, upload_type: str) -> UploadedFileRef:
        file_hash = hashlib.md5(data).hexdigest()
        begin_payload = {
            "fileName": filename,
            "uploadType": upload_type,
            "size": len(data),
            "md5": file_hash,
            "chunks": [{"hash": file_hash, "size": len(data)}],
        }
        begin = self._ensure_ok(
            self.session.post(f"{BASE}/store-file/upload/begin", json=begin_payload, timeout=REQUEST_TIMEOUT),
            "upload_begin",
        )
        info = begin.get("datas") or {}
        upload_url = (
            info.get("uploadUrl")
            or info.get("upload_url")
            or (((info.get("upload_infos") or [{}])[0]).get("upload_url"))
        )
        file_save_key = info.get("fileSaveKey") or info.get("file_save_key")
        file_upload_record_id = info.get("id") or info.get("fileUploadRecordId") or info.get("file_upload_record_id")
        upload_id = info.get("uploadId") if "uploadId" in info else info.get("upload_id")
        if not upload_url or not file_save_key:
            raise RuntimeError(f"upload_begin failed: unexpected response payload {begin}")
        put_response = None
        last_put_error: Exception | None = None
        for _attempt in range(UPLOAD_PUT_ATTEMPTS):
            try:
                put_response = requests.put(
                    upload_url,
                    data=data,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=UPLOAD_PUT_TIMEOUT,
                )
                last_put_error = None
                break
            except requests.RequestException as exc:
                last_put_error = exc
        if last_put_error is not None:
            raise last_put_error
        if hasattr(put_response, "raise_for_status"):
            put_response.raise_for_status()
        elif getattr(put_response, "status_code", 200) >= 400:
            raise RuntimeError(f"upload_put failed with HTTP {put_response.status_code}")

        end_payload = {
            "chunks": [file_hash],
            "hash": file_hash,
            "file_upload_record_id": file_upload_record_id,
            "size": len(data),
            "upload_id": upload_id,
            "file_save_key": file_save_key,
            "status": 2,
        }
        self._ensure_ok(
            self.session.post(f"{BASE}/store-file/upload/end", json=end_payload, timeout=REQUEST_TIMEOUT),
            "upload_end",
        )
        return UploadedFileRef(kind=upload_type, file_save_key=file_save_key, size=len(data), file_hash=file_hash)
