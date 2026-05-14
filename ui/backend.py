from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from appstore.appstore_client import AppStoreClient, build_requests_session
from appstore.browser_runtime import capture_browser_session_state, wait_for_logged_in_portal_state
from appstore.capabilities import CapabilityCache, build_system_templates, load_capability_cache, sync_capabilities_to_cache
from appstore.capture_workflow import CaptureOptions, CapturePackageResult, capture_packages
from appstore.models import AppRecord, PackageRecord, ReleaseRecord, RowResult, TargetRecord
from appstore.session_state import BrowserSessionState, SessionStateStore
from appstore.submission import submit_grouped_release, validate_release_group
from appstore.translation import TranslationConfig, desired_languages_for_regions, translate_listing_texts
from appstore.upload_batch import _write_reports
from appstore.pyppeteer_runtime import PYPPETEER_LAUNCH_OPTIONS, launch
from ui.assets import AssetBundle, preprocess_assets
from ui.package_meta import PackageGroup


REPO_ROOT = Path(__file__).resolve().parents[1]


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


DEFAULT_CAPABILITY_CACHE_DIR = _path_from_env("UTPUBLISHER_CAPABILITY_CACHE_DIR", REPO_ROOT / "appstore" / "cache" / "capabilities")
DEFAULT_SESSION_CACHE_DIR = _path_from_env("UTPUBLISHER_SESSION_CACHE_DIR", REPO_ROOT / "appstore" / "cache" / "session-state")
DEFAULT_OUTPUT_ROOT = _path_from_env("UTPUBLISHER_OUTPUT_ROOT", REPO_ROOT / "ui" / "output")
STORE_INDEX_URL = "https://appstore-dev.uniontech.com/#/index"
DEFAULT_AI_BASE_URL = os.environ.get("APPSTORE_AI_BASE_URL", "http://127.0.0.1:8787/v1")
DEFAULT_AI_MODEL = os.environ.get("APPSTORE_AI_MODEL", "openai-codex/gpt-5.4")
DEFAULT_AI_API_KEY = os.environ.get("APPSTORE_AI_API_KEY", "")
DEFAULT_SUDO_PASSWORD = os.environ.get("APPSTORE_SUDO_PASSWORD", "")
DEFAULT_OCR_PYTHON = os.environ.get("APPSTORE_OCR_PYTHON", "")
DEFAULT_SESSION_CACHE_MAX_AGE_SECONDS = int(os.environ.get("APPSTORE_SESSION_CACHE_MAX_AGE_SECONDS", str(7 * 24 * 3600)))


@dataclass(frozen=True)
class LoginContext:
    client: AppStoreClient
    account_label: str
    session_state_path: Path | None
    login_mode: str
    can_use_browser_mode: bool


@dataclass(frozen=True)
class StoreAppMatch:
    app_id: str
    detail_id: str
    pkg_name: str
    app_name: str


@dataclass(frozen=True)
class StoreCategoryOption:
    category_id: str
    name: str
    english_name: str


@dataclass(frozen=True)
class SystemTargetOption:
    package_path: str
    package_label: str
    package_arch: str
    code: str
    label: str
    package_family: str
    baseline_options: tuple[tuple[str, str], ...]
    selected: bool
    baseline_id: str
    selected_baseline_ids: tuple[str, ...] = ()
    unsupported_baseline_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubmissionResult:
    output_dir: Path
    report_path: Path
    rows: tuple[dict, ...]


@dataclass(frozen=True)
class BatchGroupSubmissionPlan:
    package_group: PackageGroup
    submission_mode: str
    selected_match: StoreAppMatch | None
    app_name_zh: str
    website: str
    short_desc_zh: str
    full_desc_zh: str
    category_id: str
    region_codes: tuple[str, ...]
    asset_dir: Path | None
    replace_assets: bool = False
    note_zh: str = ""
    app_name_en: str = ""
    short_desc_en: str = ""
    full_desc_en: str = ""
    note_en: str = ""
    developer_name: str = ""
    auto_translate_en: bool = True
    manual_screenshot_paths: tuple[Path, ...] = ()
    prepared_icon_path: Path | None = None
    prepared_screenshot_paths: tuple[Path, ...] = ()
    asset_warnings: tuple[str, ...] = ()
    metadata_edited: bool = False
    manual_en_edited: bool = False
    cpu_clip_codes: tuple[str, ...] | None = None
    motherboard_codes: tuple[str, ...] | None = None


REGION_OPTIONS: tuple[tuple[str, str], ...] = (
    ("1", "中国（包含港澳台）"),
    ("2", "其他地区"),
)

PACKAGE_ARCH_TO_STORE_ARCH_CODE = {
    "amd64": "4",
    "x86_64": "4",
    "arm64": "3",
    "aarch64": "3",
    "loong64": "6",
    "loongarch64": "6",
    "sw64": "8",
    "sw_64": "8",
    "mips64": "1",
    "mips64el": "1",
}

DEFAULT_ARM_CPU_CLIP_CODES = ("0", "3", "4")


def login_with_credentials(
    username: str,
    password: str,
    *,
    session_cache_dir: Path = DEFAULT_SESSION_CACHE_DIR,
    log: Callable[[str], None] | None = None,
) -> LoginContext:
    _log(log, "使用账号密码登录应用商店。")
    client = AppStoreClient()
    cookies, local_storage, session_storage = asyncio.run(client._login_and_export_state(username, password))
    client = AppStoreClient(
        build_requests_session(
            cookies=cookies,
            local_storage=local_storage,
            session_storage=session_storage,
        )
    )
    dev_info = client.fetch_dev_info()
    account_key = username.strip() or str(dev_info.get("dev_name", "") or "appstore-user").strip() or "manual-login"
    session_state = BrowserSessionState(
        account=account_key,
        cookies=cookies,
        local_storage=local_storage,
        session_storage=session_storage,
        user_agent="Mozilla/5.0",
        last_verified_at=datetime.now().isoformat(),
    )
    session_state_path = SessionStateStore(session_cache_dir).save(session_state)
    account_label = str(dev_info.get("dev_name", "") or username.strip() or "appstore-user").strip()
    _log(log, f"登录成功：{account_label}")
    return LoginContext(
        client=client,
        account_label=account_label,
        session_state_path=session_state_path,
        login_mode="credentials",
        can_use_browser_mode=True,
    )


def login_with_browser(
    account_label: str,
    *,
    session_cache_dir: Path = DEFAULT_SESSION_CACHE_DIR,
    log: Callable[[str], None] | None = None,
) -> LoginContext:
    normalized_label = account_label.strip() or "manual-login"
    _log(log, "打开可见浏览器，请扫码或手动完成网页登录。")

    async def _run() -> BrowserSessionState:
        browser = await launch(headless=False, **PYPPETEER_LAUNCH_OPTIONS)
        try:
            page = await browser.newPage()
            await page.goto(STORE_INDEX_URL, {"waitUntil": "networkidle2", "timeout": 120000})
            await wait_for_logged_in_portal_state(page, timeout_ms=300000)
            return await capture_browser_session_state(page, account=normalized_label, timeout_ms=300000)
        finally:
            await browser.close()

    state = asyncio.run(_run())
    store = SessionStateStore(session_cache_dir)
    session_state_path = store.save(state)
    client = AppStoreClient(
        build_requests_session(
            cookies=state.cookies,
            local_storage=state.local_storage,
            session_storage=state.session_storage,
        )
    )
    client.fetch_dev_info()
    _log(log, f"网页登录成功，session 已保存到 {session_state_path}")
    return LoginContext(
        client=client,
        account_label=normalized_label,
        session_state_path=session_state_path,
        login_mode="browser",
        can_use_browser_mode=False,
    )


def login_with_browser_state(
    state: BrowserSessionState,
    *,
    session_cache_dir: Path = DEFAULT_SESSION_CACHE_DIR,
    log: Callable[[str], None] | None = None,
) -> LoginContext:
    normalized_label = state.account.strip() or "manual-login"
    store = SessionStateStore(session_cache_dir)
    session_state_path = store.save(
        BrowserSessionState(
            account=normalized_label,
            cookies=state.cookies,
            local_storage=state.local_storage,
            session_storage=state.session_storage,
            user_agent=state.user_agent,
            last_verified_at=state.last_verified_at,
        )
    )
    client = AppStoreClient(
        build_requests_session(
            cookies=state.cookies,
            local_storage=state.local_storage,
            session_storage=state.session_storage,
        )
    )
    dev_info = client.fetch_dev_info()
    account_label = str(dev_info.get("dev_name", "") or normalized_label).strip() or normalized_label
    _log(log, f"网页登录成功，session 已保存到 {session_state_path}")
    return LoginContext(
        client=client,
        account_label=account_label,
        session_state_path=session_state_path,
        login_mode="browser",
        can_use_browser_mode=False,
    )


def try_restore_cached_login(
    preferred_account: str = "",
    *,
    session_cache_dir: Path = DEFAULT_SESSION_CACHE_DIR,
    log: Callable[[str], None] | None = None,
) -> LoginContext | None:
    store = SessionStateStore(session_cache_dir)
    candidates: list[str] = []
    normalized_preferred = preferred_account.strip()
    if normalized_preferred:
        candidates.append(normalized_preferred)
    for account in store.list_accounts():
        if account not in candidates:
            candidates.append(account)
    if not candidates:
        _log(log, "未找到可复用的登录缓存。")
        return None

    for account in candidates:
        state = store.load(account)
        if state is None:
            continue
        age_seconds = _session_age_seconds(state)
        if age_seconds is not None and age_seconds > DEFAULT_SESSION_CACHE_MAX_AGE_SECONDS:
            _log(log, f"检测到缓存登录态 {account} 已超过本地有效期，继续尝试校验是否仍可复用。")
        else:
            _log(log, f"正在校验缓存登录态：{account}")
        client = AppStoreClient(
            build_requests_session(
                cookies=state.cookies,
                local_storage=state.local_storage,
                session_storage=state.session_storage,
            )
        )
        try:
            dev_info = client.fetch_dev_info()
        except Exception as exc:
            _log(log, f"缓存登录态已失效：{account}，原因：{exc}")
            store.invalidate(account)
            continue
        refreshed_state = BrowserSessionState(
            account=state.account,
            cookies=state.cookies,
            local_storage=state.local_storage,
            session_storage=state.session_storage,
            user_agent=state.user_agent,
            last_verified_at=datetime.now().isoformat(),
        )
        session_state_path = store.save(refreshed_state)
        account_label = str(dev_info.get("dev_name", "") or state.account or account).strip() or account
        _log(log, f"已复用缓存登录态：{account_label}")
        return LoginContext(
            client=client,
            account_label=account_label,
            session_state_path=session_state_path,
            login_mode="cached",
            can_use_browser_mode=False,
        )
    _log(log, "未找到有效的缓存登录态，请重新登录。")
    return None


def sync_capabilities(
    client: AppStoreClient,
    *,
    cache_dir: Path = DEFAULT_CAPABILITY_CACHE_DIR,
    log: Callable[[str], None] | None = None,
) -> CapabilityCache:
    _log(log, "同步商店能力缓存。")
    latest_path = sync_capabilities_to_cache(client, cache_dir)
    cache = load_capability_cache(cache_dir)
    _log(log, f"能力缓存已更新：{latest_path}")
    return cache


def load_or_sync_capabilities(
    client: AppStoreClient | None = None,
    *,
    cache_dir: Path = DEFAULT_CAPABILITY_CACHE_DIR,
    log: Callable[[str], None] | None = None,
) -> CapabilityCache:
    latest_path = cache_dir / "latest.json"
    if latest_path.exists():
        return load_capability_cache(cache_dir)
    if client is None:
        raise RuntimeError("capability cache missing; please login and sync first")
    return sync_capabilities(client, cache_dir=cache_dir, log=log)


def build_target_options(
    cache: CapabilityCache,
    *,
    package_group: PackageGroup,
) -> tuple[SystemTargetOption, ...]:
    templates = [
        template
        for template in build_system_templates(cache)
        if template.package_family == package_group.package_family
    ]
    result: list[SystemTargetOption] = []
    for package in package_group.packages:
        selected_codes = {template.sup_sys_code for template in templates}
        if package.pkg_arch.lower() in {"loong64", "loongarch64"}:
            selected_codes = {code for code in selected_codes if code != "11"} | {
                code for code in selected_codes if code == "21"
            }
        for template in templates:
            baseline_options = tuple(
                (option.baseline_id, option.minor_version) for option in template.baseline_options
            )
            selected_baseline_ids = _default_selected_baseline_ids(baseline_options)
            baseline_id = selected_baseline_ids[0] if selected_baseline_ids else ""
            result.append(
                SystemTargetOption(
                    package_path=str(package.path),
                    package_label=package.path.name,
                    package_arch=package.pkg_arch,
                    code=template.sup_sys_code,
                    label=template.system_label,
                    package_family=template.package_family,
                    baseline_options=baseline_options,
                    selected=template.sup_sys_code in selected_codes,
                    baseline_id=baseline_id,
                    selected_baseline_ids=selected_baseline_ids,
                    unsupported_baseline_ids=(),
                )
            )
    return tuple(result)


def _default_selected_baseline_ids(baseline_options: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    if not baseline_options:
        return ()
    baseline_id = baseline_options[-1][0]
    return (baseline_id,) if baseline_id else ()


def package_group_store_arch_codes(package_group: PackageGroup) -> tuple[str, ...]:
    codes: list[str] = []
    for arch in package_group.pkg_arches:
        code = PACKAGE_ARCH_TO_STORE_ARCH_CODE.get(arch.strip().lower(), arch.strip())
        if code and code not in codes:
            codes.append(code)
    return tuple(sorted(codes, key=_store_code_sort_key))


def adapt_arch_label(cache: CapabilityCache | None, package_group: PackageGroup) -> str:
    labels: list[str] = []
    for code in package_group_store_arch_codes(package_group):
        option = cache.arch_options.get(code) if cache is not None else None
        label = option.label if option is not None else code
        if label and label not in labels:
            labels.append(label)
    return ",".join(labels)


def build_cpu_clip_options(cache: CapabilityCache | None, package_group: PackageGroup) -> tuple[dict[str, object], ...]:
    if cache is None:
        return ()
    selected_codes = _default_cpu_clip_codes(package_group)
    return _build_adapt_option_payloads(cache.cpu_clip_options, selected_codes=selected_codes)


def build_motherboard_options(cache: CapabilityCache | None) -> tuple[dict[str, object], ...]:
    if cache is None:
        return ()
    return _build_adapt_option_payloads(cache.motherboard_options, selected_codes=())


def _default_cpu_clip_codes(package_group: PackageGroup) -> tuple[str, ...]:
    arches = {arch.strip().lower() for arch in package_group.pkg_arches}
    if arches & {"arm64", "aarch64"}:
        return DEFAULT_ARM_CPU_CLIP_CODES
    return ()


def _build_adapt_option_payloads(
    options: dict[str, object],
    *,
    selected_codes: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    selected = {str(code).strip() for code in selected_codes if str(code).strip()}
    result: list[dict[str, object]] = []
    for code, option in options.items():
        label = str(getattr(option, "label", "") or code).strip()
        result.append(
            {
                "code": str(code),
                "label": label,
                "selected": str(code) in selected,
            }
        )
    return tuple(result)


def _store_code_sort_key(code: str) -> tuple[int, str]:
    normalized = str(code).strip()
    if normalized.isdigit():
        return (int(normalized), normalized)
    return (9999, normalized)


def build_target_options_for_groups(
    cache: CapabilityCache,
    *,
    package_groups: tuple[PackageGroup, ...],
) -> tuple[SystemTargetOption, ...]:
    result: list[SystemTargetOption] = []
    for package_group in package_groups:
        result.extend(build_target_options(cache, package_group=package_group))
    return tuple(result)


def find_existing_apps(
    client: AppStoreClient,
    *,
    pkg_name: str,
) -> tuple[StoreAppMatch, ...]:
    matches = client.find_apps_by_pkg_name(pkg_name)
    result: list[StoreAppMatch] = []
    for row in matches:
        result.append(
            StoreAppMatch(
                app_id=str(row.get("app_id", "")).strip(),
                detail_id=str(row.get("id", "")).strip(),
                pkg_name=str(row.get("pkg_name", "")).strip(),
                app_name=str(row.get("app_name", "") or row.get("name", "") or row.get("appName", "")).strip(),
            )
        )
    return tuple(result)


def fetch_category_options(
    client: AppStoreClient,
    *,
    log: Callable[[str], None] | None = None,
) -> tuple[StoreCategoryOption, ...]:
    _log(log, "同步应用分类列表。")
    rows = client.fetch_categories()
    result: list[StoreCategoryOption] = []
    for row in rows:
        category_id = str(row.get("id", "")).strip()
        name = str(row.get("name", "")).strip()
        english_name = str(row.get("enName", "") or row.get("en_name", "")).strip()
        if not category_id or not name:
            continue
        result.append(
            StoreCategoryOption(
                category_id=category_id,
                name=name,
                english_name=english_name,
            )
        )
    _log(log, f"已加载 {len(result)} 个应用分类。")
    return tuple(result)


def fetch_existing_app_detail(
    client: AppStoreClient,
    match: StoreAppMatch,
    *,
    log: Callable[[str], None] | None = None,
) -> dict:
    if not match.detail_id:
        raise RuntimeError("selected app match is missing detail id")
    _log(log, f"加载现有应用详情：app_id={match.app_id}")
    return client.get_app_detail(match.detail_id)


def build_existing_detail_editor_defaults(
    existing_app_detail: dict,
    *,
    fallback_name: str,
) -> dict[str, object]:
    defaults = _existing_update_defaults(existing_app_detail, fallback_name=fallback_name)
    existing_zh = _existing_lan_info(existing_app_detail, "zh_CN") or {}
    existing_en = _existing_lan_info(existing_app_detail, "en_US") or {}
    category_id = str(defaults.get("category_id") or "").strip() or "1"
    return {
        "app_name_zh": str(defaults.get("app_name_zh", "")).strip() or fallback_name.strip(),
        "website": str(defaults.get("website", "")).strip(),
        "short_desc_zh": str(defaults.get("short_desc_zh", "")).strip(),
        "full_desc_zh": str(defaults.get("full_desc_zh", "")).strip(),
        "category_id": category_id,
        "region_codes": tuple(str(code).strip() for code in (defaults.get("region_codes") or ()) if str(code).strip())
        or ("1",),
        "note_zh": str(existing_zh.get("update_desc", "")).strip(),
        "developer_name": str(existing_zh.get("dev_name", "") or existing_en.get("dev_name", "")).strip(),
        "app_name_en": str(existing_en.get("name", "")).strip(),
        "short_desc_en": str(existing_en.get("brief_info", "")).strip(),
        "full_desc_en": str(existing_en.get("desc_info", "")).strip(),
        "note_en": str(existing_en.get("update_desc", "")).strip(),
    }


def capture_screenshots_for_group(
    package_group: PackageGroup,
    *,
    output_dir: Path,
    ocr_python: str = "",
    sudo_password: str = "",
    launch_command: str = "",
    desktop_file: str = "",
    window_name: str = "",
    window_class: str = "",
    log: Callable[[str], None] | None = None,
) -> tuple[Path, ...]:
    package_path = package_group.packages[0].path
    _log(log, f"对 {package_path.name} 执行自动截图。")
    results = capture_packages(
        package_paths=[package_path],
        options=CaptureOptions(
            output_dir=output_dir,
            ocr_backend="auto",
            ocr_python=ocr_python or DEFAULT_OCR_PYTHON,
            sudo_password=sudo_password or DEFAULT_SUDO_PASSWORD,
            launch_command=launch_command,
            desktop_file=desktop_file,
            window_name=window_name,
            window_class=window_class,
            min_screenshots=3,
            max_screenshots=6,
            validate_screenshots=True,
        ),
    )
    result = results[0]
    if result.status != "captured":
        partial_screenshots = _load_partial_capture_screenshots(result.asset_dir)
        if partial_screenshots:
            _log(log, f"自动截图未达到最低数量，但已保留 {len(partial_screenshots)} 张有效截图。")
            validation_summary = _capture_validation_summary(result.asset_dir)
            if validation_summary:
                _log(log, validation_summary)
            missing_count = max(0, 3 - len(partial_screenshots))
            if missing_count:
                _log(log, f"请再补充至少 {missing_count} 张有效截图，或在已有应用更新时取消“替换图标/截图”。")
            return partial_screenshots
        raise RuntimeError(_format_capture_failure(result))
    _log(log, f"自动截图完成，得到 {len(result.screenshots)} 张。")
    return tuple(result.screenshots)


def preprocess_submission_assets(
    package_group: PackageGroup,
    *,
    asset_dir: Path | None,
    manual_icon_path: Path | None,
    manual_screenshot_paths: tuple[Path, ...],
    output_dir: Path,
    log: Callable[[str], None] | None = None,
) -> AssetBundle:
    _log(log, "检测并预处理图标与截图。")
    bundle = preprocess_assets(
        package_group,
        output_dir=output_dir,
        asset_dir=asset_dir,
        manual_icon_path=manual_icon_path,
        manual_screenshot_paths=manual_screenshot_paths,
        min_screenshots=3,
        max_screenshots=6,
    )
    if bundle.icon_path is not None:
        _log(log, f"图标已准备：{bundle.icon_path}")
    if bundle.screenshot_paths:
        _log(log, f"有效截图数量：{len(bundle.screenshot_paths)}")
    for warning in bundle.warnings:
        _log(log, f"警告：{warning}")
    return bundle


def generate_english_listing_texts(
    *,
    app_name_zh: str,
    short_desc_zh: str,
    full_desc_zh: str,
    note_zh: str,
    log: Callable[[str], None] | None = None,
) -> dict[str, str]:
    _log(log, "正在根据中文文案生成英文文案。")
    translated = translate_listing_texts(
        app_name_zh=app_name_zh,
        short_desc_zh=short_desc_zh,
        full_desc_zh=full_desc_zh,
        update_desc_zh=note_zh,
        target_lan="en_US",
        config=TranslationConfig(
            base_url=DEFAULT_AI_BASE_URL,
            model=DEFAULT_AI_MODEL,
            api_key=DEFAULT_AI_API_KEY,
        ),
    )
    _log(log, "英文文案已生成。")
    return translated


def submit_new_application(
    login: LoginContext,
    *,
    package_group: PackageGroup,
    cache: CapabilityCache,
    app_name_zh: str,
    website: str,
    short_desc_zh: str,
    full_desc_zh: str,
    keywords_zh: str,
    category_id: int,
    region_codes: tuple[str, ...],
    note: str,
    release_key: str,
    pkg_channel: str,
    assets: AssetBundle,
    selected_targets: tuple[SystemTargetOption, ...],
    output_dir: Path,
    log: Callable[[str], None] | None = None,
) -> SubmissionResult:
    if assets.icon_path is None:
        raise RuntimeError("new app submission requires an icon")
    if len(assets.screenshot_paths) < 3:
        raise RuntimeError("new app submission requires at least 3 valid screenshots")
    return _submit_grouped_release(
        login=login,
        package_group=package_group,
        cache=cache,
        app_name_zh=app_name_zh,
        website=website,
        short_desc_zh=short_desc_zh,
        full_desc_zh=full_desc_zh,
        keywords_zh=keywords_zh,
        category_id=category_id,
        region_codes=region_codes,
        note=note,
        release_key=release_key,
        pkg_channel=pkg_channel,
        assets=assets,
        selected_targets=selected_targets,
        output_dir=output_dir,
        target_app_id="",
        existing_app_detail=None,
        existing_app_overrides=None,
        desired_lans=desired_languages_for_regions(region_codes),
        localized_lan_texts=_build_localized_lan_texts(
            app_name_zh=app_name_zh,
            short_desc_zh=short_desc_zh,
            full_desc_zh=full_desc_zh,
            note=note,
            region_codes=region_codes,
            existing_app_detail=None,
            log=log,
        ),
        developer_name="",
        cpu_clip_codes=None,
        motherboard_codes=None,
        log=log,
    )


def submit_existing_application(
    login: LoginContext,
    *,
    package_group: PackageGroup,
    cache: CapabilityCache,
    match: StoreAppMatch,
    app_name_zh: str,
    website: str,
    short_desc_zh: str,
    full_desc_zh: str,
    category_id: int,
    region_codes: tuple[str, ...],
    note: str,
    release_key: str,
    pkg_channel: str,
    assets: AssetBundle,
    selected_targets: tuple[SystemTargetOption, ...],
    replace_assets: bool,
    output_dir: Path,
    log: Callable[[str], None] | None = None,
) -> SubmissionResult:
    if replace_assets:
        if assets.icon_path is None:
            raise RuntimeError("replacing store assets requires a valid icon")
        if len(assets.screenshot_paths) < 3:
            raise RuntimeError("replacing store screenshots requires at least 3 valid screenshots")
    existing_app_detail = fetch_existing_app_detail(login.client, match)
    desired_lans = desired_languages_for_regions(region_codes)
    effective_assets = assets if replace_assets else AssetBundle(
        icon_source=assets.icon_source,
        screenshot_sources=assets.screenshot_sources,
        icon_path=None,
        screenshot_paths=(),
        validation_report=assets.validation_report,
        warnings=assets.warnings,
    )
    return _submit_grouped_release(
        login=login,
        package_group=package_group,
        cache=cache,
        app_name_zh=app_name_zh.strip() or package_group.display_name,
        website=website.strip(),
        short_desc_zh=short_desc_zh.strip(),
        full_desc_zh=full_desc_zh.strip(),
        keywords_zh="",
        category_id=category_id,
        region_codes=region_codes,
        note=note,
        release_key=release_key,
        pkg_channel=pkg_channel,
        assets=effective_assets,
        selected_targets=selected_targets,
        output_dir=output_dir,
        target_app_id=match.app_id,
        existing_app_detail=existing_app_detail,
        existing_app_overrides={
            "app_name_zh": app_name_zh.strip() or package_group.display_name,
            "website": website.strip(),
            "short_desc_zh": short_desc_zh.strip(),
            "full_desc_zh": full_desc_zh.strip(),
            "category_id": category_id,
        },
        desired_lans=desired_lans,
        localized_lan_texts=_build_localized_lan_texts(
            app_name_zh=app_name_zh.strip() or package_group.display_name,
            short_desc_zh=short_desc_zh.strip(),
            full_desc_zh=full_desc_zh.strip(),
            note=note.strip(),
            region_codes=region_codes,
            existing_app_detail=existing_app_detail,
            log=log,
        ),
        developer_name="",
        cpu_clip_codes=None,
        motherboard_codes=None,
        log=log,
    )


def submit_applications_batch(
    login: LoginContext,
    *,
    plans: tuple[BatchGroupSubmissionPlan, ...],
    cache: CapabilityCache,
    note: str,
    release_key: str,
    pkg_channel: str,
    selected_targets: tuple[SystemTargetOption, ...],
    output_dir: Path,
    log: Callable[[str], None] | None = None,
) -> SubmissionResult:
    if not plans:
        raise RuntimeError("no package groups available for batch submission")
    _log(log, f"开始批量提交，共 {len(plans)} 个应用组。")
    all_rows: list[dict] = []
    next_row_id = 1
    for index, plan in enumerate(plans, start=1):
        package_group = plan.package_group
        label = f"{package_group.pkg_name} {package_group.pkg_version}"
        resolved_mode = _resolve_batch_submission_mode(plan)
        _log(log, f"[{index}/{len(plans)}] 处理 {label}，模式：{'批量更新' if resolved_mode == 'update' else '批量提新'}")
        package_paths = {str(package.path) for package in package_group.packages}
        group_targets = tuple(option for option in selected_targets if option.package_path in package_paths)
        group_output_dir = output_dir / f"{index:02d}-{_safe_output_name(package_group.pkg_name)}-{_safe_output_name(package_group.pkg_version)}"
        try:
            if resolved_mode == "update":
                result = _submit_batch_update_plan(
                    login=login,
                    plan=plan,
                    cache=cache,
                    note=note,
                    release_key=release_key,
                    pkg_channel=pkg_channel,
                    selected_targets=group_targets,
                    output_dir=group_output_dir,
                    log=log,
                )
            else:
                result = _submit_batch_new_plan(
                    login=login,
                    plan=plan,
                    cache=cache,
                    note=note,
                    release_key=release_key,
                    pkg_channel=pkg_channel,
                    selected_targets=group_targets,
                    output_dir=group_output_dir,
                    log=log,
                )
            all_rows.extend(_renumber_submission_rows(result.rows, start_row_id=next_row_id))
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            _log(log, f"[{index}/{len(plans)}] 失败：{message}")
            all_rows.extend(
                _batch_failure_rows(
                    package_group,
                    message=message,
                    start_row_id=next_row_id,
                    app_id=_failure_app_id_from_plan(plan),
                )
            )
        next_row_id = len(all_rows) + 1
    report_path = _write_submission_report(output_dir, all_rows)
    _log(log, f"批量提交完成，报告已写入 {report_path}")
    return SubmissionResult(
        output_dir=output_dir,
        report_path=report_path,
        rows=tuple(all_rows),
    )


def submit_existing_applications_batch(
    login: LoginContext,
    *,
    package_groups: tuple[PackageGroup, ...],
    cache: CapabilityCache,
    note: str,
    release_key: str,
    pkg_channel: str,
    selected_targets: tuple[SystemTargetOption, ...],
    output_dir: Path,
    log: Callable[[str], None] | None = None,
) -> SubmissionResult:
    plans = tuple(
        BatchGroupSubmissionPlan(
            package_group=package_group,
            submission_mode="update",
            selected_match=None,
            app_name_zh="",
            website="",
            short_desc_zh="",
            full_desc_zh="",
            category_id="1",
            region_codes=("1",),
            asset_dir=None,
            metadata_edited=False,
        )
        for package_group in package_groups
    )
    return submit_applications_batch(
        login,
        plans=plans,
        cache=cache,
        note=note,
        release_key=release_key,
        pkg_channel=pkg_channel,
        selected_targets=selected_targets,
        output_dir=output_dir,
        log=log,
    )


def _submit_batch_update_plan(
    *,
    login: LoginContext,
    plan: BatchGroupSubmissionPlan,
    cache: CapabilityCache,
    note: str,
    release_key: str,
    pkg_channel: str,
    selected_targets: tuple[SystemTargetOption, ...],
    output_dir: Path,
    log: Callable[[str], None] | None,
) -> SubmissionResult:
    package_group = plan.package_group
    match = plan.selected_match or _resolve_batch_existing_match(login.client, package_group=package_group)
    existing_app_detail = fetch_existing_app_detail(login.client, match, log=log)
    note_zh = plan.note_zh.strip() or note.strip()
    developer_name = plan.developer_name.strip()
    if plan.metadata_edited:
        category_id = _parse_batch_category_id(plan.category_id)
        region_codes = _normalize_batch_region_codes(plan.region_codes)
        app_name_zh = plan.app_name_zh.strip() or package_group.display_name
        website = plan.website.strip()
        short_desc_zh = plan.short_desc_zh.strip()
        full_desc_zh = plan.full_desc_zh.strip()
        existing_overrides = {
            "app_name_zh": app_name_zh,
            "website": website,
            "short_desc_zh": short_desc_zh,
            "full_desc_zh": full_desc_zh,
            "category_id": category_id,
        }
        if developer_name:
            existing_overrides["developer_name"] = developer_name
    else:
        defaults = _existing_update_defaults(existing_app_detail, fallback_name=package_group.display_name)
        category_id = int(defaults["category_id"])
        region_codes = tuple(str(code).strip() for code in defaults["region_codes"] if str(code).strip())
        app_name_zh = str(defaults["app_name_zh"])
        website = str(defaults["website"])
        short_desc_zh = str(defaults["short_desc_zh"])
        full_desc_zh = str(defaults["full_desc_zh"])
        developer_name = developer_name or str(defaults.get("developer_name", "")).strip()
        existing_overrides = None
    if plan.replace_assets:
        assets = _resolve_batch_assets(
            plan,
            output_dir=output_dir / "preprocessed",
            log=log,
        )
        if assets.icon_path is None:
            raise RuntimeError("batch update replacing assets requires a valid icon for each group")
        if len(assets.screenshot_paths) < 3:
            raise RuntimeError("batch update replacing screenshots requires at least 3 valid screenshots for each group")
    else:
        assets = AssetBundle(
            icon_source=None,
            screenshot_sources=(),
            icon_path=None,
            screenshot_paths=(),
            validation_report=None,
            warnings=(),
        )
    return _submit_grouped_release(
        login=login,
        package_group=package_group,
        cache=cache,
        app_name_zh=app_name_zh,
        website=website,
        short_desc_zh=short_desc_zh,
        full_desc_zh=full_desc_zh,
        keywords_zh="",
        category_id=category_id,
        region_codes=region_codes,
        note=note_zh,
        release_key=release_key,
        pkg_channel=pkg_channel,
        assets=assets,
        selected_targets=selected_targets,
        output_dir=output_dir,
        target_app_id=match.app_id,
        existing_app_detail=existing_app_detail,
        existing_app_overrides=existing_overrides,
        desired_lans=desired_languages_for_regions(region_codes),
        localized_lan_texts=_build_localized_lan_texts(
            app_name_zh=app_name_zh,
            short_desc_zh=short_desc_zh,
            full_desc_zh=full_desc_zh,
            note=note_zh,
            region_codes=region_codes,
            existing_app_detail=existing_app_detail,
            manual_en_texts=_manual_en_texts_from_plan(plan),
            allow_auto_translate=plan.auto_translate_en,
            log=log,
        ),
        developer_name=developer_name,
        cpu_clip_codes=plan.cpu_clip_codes,
        motherboard_codes=plan.motherboard_codes,
        log=log,
    )


def _submit_batch_new_plan(
    *,
    login: LoginContext,
    plan: BatchGroupSubmissionPlan,
    cache: CapabilityCache,
    note: str,
    release_key: str,
    pkg_channel: str,
    selected_targets: tuple[SystemTargetOption, ...],
    output_dir: Path,
    log: Callable[[str], None] | None,
) -> SubmissionResult:
    category_id = _parse_batch_category_id(plan.category_id)
    region_codes = _normalize_batch_region_codes(plan.region_codes)
    package_group = plan.package_group
    note_zh = plan.note_zh.strip() or note.strip()
    assets = _resolve_batch_assets(
        plan,
        output_dir=output_dir / "preprocessed",
        log=log,
    )
    if assets.icon_path is None:
        raise RuntimeError("batch new submission requires a valid icon for each group")
    if len(assets.screenshot_paths) < 3:
        raise RuntimeError("batch new submission requires at least 3 valid screenshots for each group")
    app_name_zh = plan.app_name_zh.strip() or package_group.display_name
    short_desc_zh = plan.short_desc_zh.strip() or package_group.short_description
    full_desc_zh = plan.full_desc_zh.strip() or package_group.full_description
    website = plan.website.strip() or package_group.homepage
    return _submit_grouped_release(
        login=login,
        package_group=package_group,
        cache=cache,
        app_name_zh=app_name_zh,
        website=website,
        short_desc_zh=short_desc_zh,
        full_desc_zh=full_desc_zh,
        keywords_zh="",
        category_id=category_id,
        region_codes=region_codes,
        note=note_zh,
        release_key=release_key,
        pkg_channel=pkg_channel,
        assets=assets,
        selected_targets=selected_targets,
        output_dir=output_dir,
        target_app_id="",
        existing_app_detail=None,
        existing_app_overrides=None,
        desired_lans=desired_languages_for_regions(region_codes),
        localized_lan_texts=_build_localized_lan_texts(
            app_name_zh=app_name_zh,
            short_desc_zh=short_desc_zh,
            full_desc_zh=full_desc_zh,
            note=note_zh,
            region_codes=region_codes,
            existing_app_detail=None,
            manual_en_texts=_manual_en_texts_from_plan(plan),
            allow_auto_translate=plan.auto_translate_en,
            log=log,
        ),
        developer_name=plan.developer_name.strip(),
        cpu_clip_codes=plan.cpu_clip_codes,
        motherboard_codes=plan.motherboard_codes,
        log=log,
    )


def _resolve_batch_submission_mode(plan: BatchGroupSubmissionPlan) -> str:
    mode = plan.submission_mode.strip().lower()
    if mode == "update":
        return "update"
    if mode == "new":
        return "new"
    return "update" if plan.selected_match is not None else "new"


def _parse_batch_category_id(raw: str) -> int:
    normalized = str(raw or "").strip()
    if not normalized.isdigit():
        raise RuntimeError(f"invalid category id for batch submission: {normalized or '-'}")
    value = int(normalized)
    if value <= 0:
        raise RuntimeError(f"invalid category id for batch submission: {normalized}")
    return value


def _normalize_batch_region_codes(region_codes: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(str(code).strip() for code in region_codes if str(code).strip())
    if not normalized:
        raise RuntimeError("at least one region must be selected for batch submission")
    return normalized


def _manual_en_texts_from_plan(plan: BatchGroupSubmissionPlan) -> dict[str, str]:
    if not plan.auto_translate_en or plan.manual_en_edited:
        return {
            "name": plan.app_name_en.strip(),
            "brief_info": plan.short_desc_en.strip(),
            "desc_info": plan.full_desc_en.strip(),
            "update_desc": plan.note_en.strip(),
        }
    return {}


def _resolve_batch_assets(
    plan: BatchGroupSubmissionPlan,
    *,
    output_dir: Path,
    log: Callable[[str], None] | None,
) -> AssetBundle:
    prepared = _prepared_assets_from_plan(plan)
    if prepared is not None:
        _log(log, "复用当前分组已预处理素材。")
        return prepared
    return preprocess_submission_assets(
        plan.package_group,
        asset_dir=plan.asset_dir,
        manual_icon_path=None,
        manual_screenshot_paths=tuple(plan.manual_screenshot_paths),
        output_dir=output_dir,
        log=log,
    )


def _prepared_assets_from_plan(plan: BatchGroupSubmissionPlan) -> AssetBundle | None:
    icon_path = plan.prepared_icon_path.expanduser().resolve() if plan.prepared_icon_path is not None else None
    if icon_path is not None and not icon_path.exists():
        icon_path = None
    screenshot_paths: list[Path] = []
    for raw_path in plan.prepared_screenshot_paths:
        normalized = raw_path.expanduser().resolve()
        if normalized.exists():
            screenshot_paths.append(normalized)
    if icon_path is None and not screenshot_paths:
        return None
    return AssetBundle(
        icon_source=icon_path,
        screenshot_sources=tuple(screenshot_paths),
        icon_path=icon_path,
        screenshot_paths=tuple(screenshot_paths),
        validation_report=None,
        warnings=tuple(plan.asset_warnings),
    )


def _submit_grouped_release(
    *,
    login: LoginContext,
    package_group: PackageGroup,
    cache: CapabilityCache,
    app_name_zh: str,
    website: str,
    short_desc_zh: str,
    full_desc_zh: str,
    keywords_zh: str,
    category_id: int,
    region_codes: tuple[str, ...],
    note: str,
    release_key: str,
    pkg_channel: str,
    assets: AssetBundle,
    selected_targets: tuple[SystemTargetOption, ...],
    output_dir: Path,
    target_app_id: str,
    existing_app_detail: dict | None,
    existing_app_overrides: dict[str, object] | None,
    desired_lans: tuple[str, ...],
    localized_lan_texts: dict[str, dict[str, str]],
    developer_name: str,
    cpu_clip_codes: tuple[str, ...] | None,
    motherboard_codes: tuple[str, ...] | None,
    log: Callable[[str], None] | None,
) -> SubmissionResult:
    selected = tuple(option for option in selected_targets if option.selected)
    if not selected:
        raise RuntimeError("at least one target system line must be selected")
    selected_by_path: dict[str, tuple[SystemTargetOption, ...]] = {}
    for package in package_group.packages:
        normalized_path = str(package.path)
        selected_by_path[normalized_path] = tuple(
            option for option in selected if option.package_path == normalized_path
        )
        if not selected_by_path[normalized_path]:
            raise RuntimeError(f"package has no target systems selected: {package.path.name}")

    _log(log, "开始构建提交载荷。")
    output_dir.mkdir(parents=True, exist_ok=True)
    app_key = package_group.pkg_name
    app = AppRecord(
        app_key=app_key,
        app_name_zh=app_name_zh.strip() or package_group.display_name,
        pkg_name=package_group.pkg_name,
        category_id=int(category_id),
        website=website.strip(),
        short_desc_zh=short_desc_zh.strip(),
        full_desc_zh=full_desc_zh.strip(),
        icon_path=assets.icon_path or Path("."),
        screenshot_paths=assets.screenshot_paths,
        keywords_zh=keywords_zh.strip(),
        app_id_override=target_app_id,
    )
    release = ReleaseRecord(
        row_id=1,
        app_key=app_key,
        release_key=release_key.strip() or "stable",
        release_name=release_key.strip() or "stable",
        execution_mode="api",
        region=",".join(region_codes) if region_codes else "1",
        note=note.strip(),
        cpu_clip_codes=cpu_clip_codes,
        motherboard_codes=motherboard_codes,
    )

    package_records: list[PackageRecord] = []
    inspected_by_package = {}
    targets_by_package: dict[str, tuple[TargetRecord, ...]] = {}
    for row_id, package in enumerate(package_group.packages, start=1):
        package_key = f"{app_key}-{package.pkg_arch}-{row_id}"
        package_record = PackageRecord(
            row_id=row_id,
            app_key=app_key,
            release_key=release.release_key,
            package_key=package_key,
            package_family=package.package_family,
            package_format=package.package_format,
            file_path=package.path,
            declared_arch=package.pkg_arch,
            pkg_channel=pkg_channel.strip(),
            note="",
        )
        package_records.append(package_record)
        inspected_by_package[package_key] = _package_info_from_metadata(package)
        package_targets = selected_by_path.get(str(package.path), ())
        targets_by_package[package_key] = tuple(
            TargetRecord(
                row_id=(row_id * 100) + index,
                app_key=app_key,
                release_key=release.release_key,
                package_key=package_key,
                sup_sys_code=option.code,
                baseline_id=(
                    option.selected_baseline_ids[0]
                    if option.selected_baseline_ids
                    else option.baseline_id
                ),
                unsupport_baseline_ids=option.unsupported_baseline_ids,
                target_note="",
                baseline_ids=option.selected_baseline_ids,
            )
            for index, option in enumerate(package_targets, start=1)
        )

    validated_release = validate_release_group(
        app=app,
        release=release,
        packages=tuple(package_records),
        targets_by_package=targets_by_package,
        inspected_by_package=inspected_by_package,
        capability_cache=cache,
    )

    uploads_by_package = {}
    uploadable_records = [package for package in package_records if package.file_path.exists()]
    if uploadable_records:
        _log(log, "上传包文件。")
    else:
        _log(log, "未选择新安装包，复用线上已有包文件。")
    for package in package_records:
        if package.file_path.exists():
            uploads_by_package[package.package_key] = login.client.upload_file_bytes(
                filename=package.file_path.name,
                data=package.file_path.read_bytes(),
                upload_type="temppkg",
            )
            continue
        if str(package.file_path).startswith("online/") and existing_app_detail is not None:
            continue
        raise RuntimeError(f"package file is not readable: {package.file_path}")

    app_uploads = None
    if assets.icon_path is not None and assets.screenshot_paths:
        _log(log, "上传图标与截图。")
        app_uploads = {
            "icon": login.client.upload_file_bytes(
                filename=assets.icon_path.name,
                data=assets.icon_path.read_bytes(),
                upload_type="icon",
            ),
            "screenshots": tuple(
                login.client.upload_file_bytes(
                    filename=path.name,
                    data=path.read_bytes(),
                    upload_type="image",
                )
                for path in assets.screenshot_paths
            ),
        }
    elif existing_app_detail is None:
        raise RuntimeError("new app submission requires icon and screenshots")

    _log(log, "提交应用信息到商店。")
    response = submit_grouped_release(
        client=login.client,
        validated_release=validated_release,
        app_uploads=app_uploads,
        uploads_by_package=uploads_by_package,
        target_app_id=target_app_id,
        existing_app_detail=existing_app_detail,
        existing_app_overrides=existing_app_overrides,
        localized_lan_texts=localized_lan_texts,
        desired_lans=desired_lans,
        developer_name=developer_name.strip(),
    )

    rows = _make_result_rows(
        package_records=tuple(package_records),
        package_group=package_group,
        response=response,
        target_app_id=target_app_id,
    )
    report_path = _write_submission_report(output_dir, rows)
    _log(log, f"提交完成，报告已写入 {report_path}")
    return SubmissionResult(
        output_dir=output_dir,
        report_path=report_path,
        rows=tuple(rows),
    )


def _make_result_rows(
    *,
    package_records: tuple[PackageRecord, ...],
    package_group: PackageGroup,
    response: dict,
    target_app_id: str,
) -> list[dict]:
    resolved_app_id = ""
    datas = response.get("datas")
    if isinstance(datas, dict):
        resolved_app_id = str(datas.get("app_id", "")).strip()
    if not resolved_app_id:
        resolved_app_id = target_app_id
    rows: list[RowResult] = []
    metadata_by_path = {package.path: package for package in package_group.packages}
    for record in package_records:
        metadata = metadata_by_path[record.file_path]
        rows.append(
            {
                "row_id": record.row_id,
                "app_key": record.app_key,
                "deb_path": str(record.file_path),
                "status": "submitted",
                "message": "submitted",
                "app_id": resolved_app_id,
                "pkg_name": metadata.pkg_name,
                "pkg_version": metadata.pkg_version,
                "selector": f"pkg:{record.row_id}",
            }
        )
    return rows


def _write_submission_report(output_dir: Path, rows: list[dict]) -> Path:
    row_results = [
        RowResult(
            row_id=int(row["row_id"]),
            app_key=str(row["app_key"]),
            deb_path=Path(row["deb_path"]),
            status=str(row["status"]),
            message=str(row["message"]),
            app_id=str(row.get("app_id", "")),
            pkg_name=str(row.get("pkg_name", "")),
            pkg_version=str(row.get("pkg_version", "")),
            selector=str(row.get("selector", "")),
        )
        for row in rows
    ]
    _write_reports(output_dir, row_results)
    return output_dir / "report.json"


def _resolve_batch_existing_match(
    client: AppStoreClient,
    *,
    package_group: PackageGroup,
) -> StoreAppMatch:
    matches = find_existing_apps(client, pkg_name=package_group.pkg_name)
    if not matches:
        raise RuntimeError(f"existing app not found for package name: {package_group.pkg_name}")
    if len(matches) > 1:
        raise RuntimeError(
            f"multiple app matches found for package name: {package_group.pkg_name}; "
            "batch update currently requires exactly one store match"
        )
    return matches[0]


def _existing_update_defaults(existing_app_detail: dict, *, fallback_name: str) -> dict[str, object]:
    detail_data = existing_app_detail.get("datas") if isinstance(existing_app_detail.get("datas"), dict) else existing_app_detail
    basic_info = detail_data.get("app_basic_info") or {}
    lan_info = _existing_lan_info(existing_app_detail, "zh_CN") or ((detail_data.get("app_lan_infos") or [{}])[0] if (detail_data.get("app_lan_infos") or []) else {})
    fit_info = detail_data.get("app_fit_info") or {}
    region_codes = tuple(
        str(item.get("code", "")).strip()
        for item in (fit_info.get("region") or [])
        if str(item.get("code", "")).strip()
    )
    if not region_codes:
        region_codes = tuple(token.strip() for token in str(basic_info.get("region") or "1").split(",") if token.strip()) or ("1",)
    return {
        "app_name_zh": str(lan_info.get("name", "")).strip() or fallback_name.strip(),
        "developer_name": str(lan_info.get("dev_name", "")).strip(),
        "website": str(basic_info.get("website", "")).strip(),
        "short_desc_zh": str(lan_info.get("brief_info", "")).strip(),
        "full_desc_zh": str(lan_info.get("desc_info", "")).strip(),
        "category_id": int(basic_info.get("category_id") or 0),
        "region_codes": region_codes,
    }


def _renumber_submission_rows(rows: tuple[dict, ...] | list[dict], *, start_row_id: int) -> list[dict]:
    result: list[dict] = []
    for offset, row in enumerate(rows):
        normalized = dict(row)
        normalized["row_id"] = start_row_id + offset
        selector = str(normalized.get("selector", "")).strip()
        if selector.startswith("pkg:"):
            normalized["selector"] = f"pkg:{start_row_id + offset}"
        result.append(normalized)
    return result


def _failure_app_id_from_plan(plan: BatchGroupSubmissionPlan) -> str:
    if plan.selected_match is None:
        return ""
    return str(plan.selected_match.app_id).strip()


def _batch_failure_rows(
    package_group: PackageGroup,
    *,
    message: str,
    start_row_id: int,
    app_id: str = "",
) -> list[dict]:
    rows: list[dict] = []
    for offset, package in enumerate(package_group.packages):
        rows.append(
            {
                "row_id": start_row_id + offset,
                "app_key": package.pkg_name,
                "deb_path": str(package.path),
                "status": "submit_failed",
                "message": message,
                "app_id": app_id,
                "pkg_name": package.pkg_name,
                "pkg_version": package.pkg_version,
                "selector": f"pkg:{start_row_id + offset}",
            }
        )
    return rows


def _safe_output_name(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    normalized = normalized.strip("-._")
    return normalized or "app"


def _package_info_from_metadata(package: PackageMetadata):
    from appstore.models import PackageInfo

    return PackageInfo(
        pkg_name=package.pkg_name,
        pkg_version=package.pkg_version,
        pkg_arch=package.pkg_arch,
        pkg_size=package.pkg_size,
        sha256=package.sha256,
        file_path=package.path,
        package_family=package.package_family,
        package_format=package.package_format,
    )


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)


def _build_localized_lan_texts(
    *,
    app_name_zh: str,
    short_desc_zh: str,
    full_desc_zh: str,
    note: str,
    region_codes: tuple[str, ...],
    existing_app_detail: dict | None,
    manual_en_texts: dict[str, str] | None = None,
    allow_auto_translate: bool = True,
    log: Callable[[str], None] | None,
) -> dict[str, dict[str, str]]:
    localized = {
        "zh_CN": {
            "name": app_name_zh.strip(),
            "brief_info": short_desc_zh.strip(),
            "desc_info": full_desc_zh.strip(),
            "update_desc": note.strip(),
        }
    }
    desired_lans = desired_languages_for_regions(region_codes)
    if "en_US" not in desired_lans:
        return localized

    existing_en = _existing_lan_info(existing_app_detail, "en_US")
    existing_zh = _existing_lan_info(existing_app_detail, "zh_CN")
    normalized_manual_en = {
        key: str(value).strip()
        for key, value in (manual_en_texts or {}).items()
        if str(value).strip()
    }

    if normalized_manual_en and not allow_auto_translate:
        required = ("name", "brief_info", "desc_info")
        if not all(normalized_manual_en.get(field, "").strip() for field in required):
            raise RuntimeError("其他地区已启用，但英文文案未填写完整；请填写英文名称、简介和详细描述，或启用自动生成。")
        normalized_manual_en.setdefault("update_desc", note.strip())
        localized["en_US"] = normalized_manual_en
        return localized

    if existing_en and existing_zh and _same_zh_content(
        existing_zh,
        app_name_zh=app_name_zh,
        short_desc_zh=short_desc_zh,
        full_desc_zh=full_desc_zh,
        note=note,
    ) and not normalized_manual_en:
        _log(log, "检测到其他地区已存在英文文案且中文内容未变化，沿用现有英文文案。")
        localized["en_US"] = {
            "name": str(existing_en.get("name", "")).strip(),
            "brief_info": str(existing_en.get("brief_info", "")).strip(),
            "desc_info": str(existing_en.get("desc_info", "")).strip(),
            "update_desc": str(existing_en.get("update_desc", "")).strip(),
        }
        return localized

    if not allow_auto_translate:
        if normalized_manual_en:
            normalized_manual_en.setdefault("update_desc", note.strip())
            localized["en_US"] = normalized_manual_en
            return localized
        raise RuntimeError("其他地区已启用，但英文文案为空；请填写英文文案，或启用自动生成。")

    _log(log, "已选择其他地区，正在自动生成英文文案。")
    translated = generate_english_listing_texts(
        app_name_zh=app_name_zh,
        short_desc_zh=short_desc_zh,
        full_desc_zh=full_desc_zh,
        note_zh=note,
        log=None,
    )
    translated.update(normalized_manual_en)
    localized["en_US"] = translated
    return localized


def _existing_lan_info(existing_app_detail: dict | None, lan: str) -> dict | None:
    if not existing_app_detail:
        return None
    detail_data = existing_app_detail.get("datas") if isinstance(existing_app_detail.get("datas"), dict) else existing_app_detail
    for info in detail_data.get("app_lan_infos") or ():
        if str(info.get("lan", "")).strip() == lan:
            return info
    return None


def _session_age_seconds(state: BrowserSessionState) -> float | None:
    raw = state.last_verified_at.strip()
    if not raw:
        return None
    try:
        verified_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    now = datetime.now(verified_at.tzinfo) if verified_at.tzinfo is not None else datetime.now()
    return max(0.0, (now - verified_at).total_seconds())


def _same_zh_content(
    existing_zh: dict,
    *,
    app_name_zh: str,
    short_desc_zh: str,
    full_desc_zh: str,
    note: str,
) -> bool:
    return (
        str(existing_zh.get("name", "")).strip() == app_name_zh.strip()
        and str(existing_zh.get("brief_info", "")).strip() == short_desc_zh.strip()
        and str(existing_zh.get("desc_info", "")).strip() == full_desc_zh.strip()
        and (not note.strip() or str(existing_zh.get("update_desc", "")).strip() == note.strip())
    )


def _format_capture_failure(result: CapturePackageResult) -> str:
    lines = [result.message.strip() or "自动截图失败。"]
    trace = result.execution_trace or {}
    stage = str(trace.get("capture_stage", "")).strip()
    if stage:
        lines.append(f"阶段: {stage}")
    validation_summary = _capture_validation_summary(result.asset_dir)
    if validation_summary:
        lines.append(validation_summary)

    install_excerpt = _read_log_excerpt(result.asset_dir / "logs" / "install.log")
    stderr_excerpt = _read_log_excerpt(result.asset_dir / "logs" / "app.stderr.log")
    hints = _capture_failure_hints(result=result, install_excerpt=install_excerpt, stderr_excerpt=stderr_excerpt)
    lines.extend(hints)
    if install_excerpt:
        lines.append(f"install.log 摘要:\n{install_excerpt}")
    if stderr_excerpt:
        lines.append(f"app.stderr.log 摘要:\n{stderr_excerpt}")
    return "\n\n".join(line for line in lines if line.strip())


def _capture_failure_hints(
    *,
    result: CapturePackageResult,
    install_excerpt: str,
    stderr_excerpt: str,
) -> list[str]:
    hints: list[str] = []
    message = result.message.strip()
    if "allow-downgrades" in install_excerpt.lower():
        hints.append("提示: 当前包版本低于已安装版本，安装命令需要允许降级。默认安装参数已补上 --allow-downgrades，重新尝试一次。")
    if "pkexec" in message and not DEFAULT_SUDO_PASSWORD:
        hints.append("提示: deb 自动截图需要提权安装。当前未配置 APPSTORE_SUDO_PASSWORD，建议在界面里填写提权密码后重试。")
    if "timed out waiting for window" in message:
        hints.append(
            "提示: 应用已启动但未匹配到窗口。可在“自动截图高级参数”里补充启动命令、desktop 文件、窗口标题或窗口类名。"
        )
    if "captured screenshots below minimum" in message:
        accepted = len(_load_partial_capture_screenshots(result.asset_dir))
        if accepted:
            hints.append(
                f"提示: 自动截图已保留 {accepted} 张有效截图，其余截图被判定为重复或质量不足。"
                "可先补足缺少的截图，或在已有应用更新时取消“替换图标/截图”。"
            )
    lowered_stderr = stderr_excerpt.lower()
    if "qt.qpa.plugin" in lowered_stderr or "could not connect to display" in lowered_stderr:
        hints.append("提示: 目标应用在 Xvfb 下未能正确启动图形界面，通常是 Qt/xcb 运行时依赖缺失或启动环境不兼容。")
    if "no session bus" in lowered_stderr:
        hints.append("提示: 应用依赖桌面会话总线，当前自动截图环境里的 DBus 会话没有准备好或应用自身强依赖宿主桌面。")
    return hints


def _read_log_excerpt(path: Path, *, max_lines: int = 16) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    trimmed = [line.rstrip() for line in lines if line.strip()]
    if not trimmed:
        return ""
    return "\n".join(trimmed[-max_lines:])


def _load_partial_capture_screenshots(asset_dir: Path) -> tuple[Path, ...]:
    payload = _load_capture_validation_payload(asset_dir)
    accepted_paths = payload.get("accepted_paths") if isinstance(payload, dict) else ()
    if not isinstance(accepted_paths, list):
        return ()
    paths: list[Path] = []
    for value in accepted_paths:
        normalized = Path(str(value)).expanduser()
        if normalized.exists():
            paths.append(normalized)
    return tuple(paths)


def _capture_validation_summary(asset_dir: Path) -> str:
    payload = _load_capture_validation_payload(asset_dir)
    if not isinstance(payload, dict):
        return ""
    accepted_paths = payload.get("accepted_paths")
    rejected_paths = payload.get("rejected_paths")
    items = payload.get("items")
    if not isinstance(accepted_paths, list) or not isinstance(rejected_paths, list):
        return ""

    reason_counter: Counter[str] = Counter()
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict) or item.get("accepted"):
                continue
            reasons = item.get("reasons")
            if not isinstance(reasons, list):
                reason_counter["rejected"] += 1
                continue
            for reason in reasons:
                normalized = str(reason).strip()
                if normalized:
                    reason_counter[normalized] += 1

    summary = f"截图校验：保留 {len(accepted_paths)} 张，拒绝 {len(rejected_paths)} 张。"
    if not reason_counter:
        return summary
    top_reasons = "；".join(
        f"{reason} x{count}"
        for reason, count in reason_counter.most_common(3)
    )
    return f"{summary} 主要原因：{top_reasons}"


def _load_capture_validation_payload(asset_dir: Path) -> dict:
    report_path = asset_dir / "screenshot-validation.json"
    if not report_path.exists():
        return {}
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
