from __future__ import annotations

import inspect
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ui.assets import AssetBundle
from ui.backend import (
    BatchGroupSubmissionPlan,
    DEFAULT_CAPABILITY_CACHE_DIR,
    DEFAULT_OUTPUT_ROOT,
    LoginContext,
    REGION_OPTIONS,
    StoreCategoryOption,
    StoreAppMatch,
    SubmissionResult,
    SystemTargetOption,
    build_target_options,
    build_target_options_for_groups,
    capture_screenshots_for_group,
    build_existing_detail_editor_defaults,
    fetch_category_options,
    fetch_existing_app_detail,
    find_existing_apps,
    generate_english_listing_texts,
    load_or_sync_capabilities,
    login_with_browser_state,
    login_with_credentials,
    preprocess_submission_assets,
    submit_applications_batch,
    submit_existing_application,
    submit_new_application,
    sync_capabilities,
    try_restore_cached_login,
)
from ui.dialogs import BatchGroupConfig, BatchGroupsDialog, CaptureOptionsDialog
from ui.native_file_dialog import get_existing_directory, get_open_file_name, get_open_file_names, install_qt_translations
from ui.package_meta import (
    analyze_package_groups,
    PackageGroup,
    find_package_files,
)
from ui.preferences import DEFAULT_PREFERENCES_PATH, PreferenceStore, UIPreferences, remember_value
from ui.qt_compat import (
    CHECKED,
    QT_BINDING,
    Signal,
    Slot,
    UNCHECKED,
    USER_ROLE,
    QtCore,
    QtGui,
    QtWidgets,
)
from ui.wechat_qr_login import WechatQrLoginDialog


@dataclass(frozen=True)
class AnalysisEntry:
    package_group: PackageGroup
    icon_path: Path | None
    screenshot_paths: tuple[Path, ...]
    existing_matches: tuple[StoreAppMatch, ...]


@dataclass(frozen=True)
class AnalysisState:
    groups: tuple[AnalysisEntry, ...]
    asset_dir: Path | None

    @property
    def is_batch(self) -> bool:
        return len(self.groups) > 1

    @property
    def package_groups(self) -> tuple[PackageGroup, ...]:
        return tuple(group.package_group for group in self.groups)

    @property
    def package_group(self) -> PackageGroup:
        return self.groups[0].package_group

    @property
    def icon_path(self) -> Path | None:
        return self.groups[0].icon_path if self.groups else None

    @property
    def screenshot_paths(self) -> tuple[Path, ...]:
        return self.groups[0].screenshot_paths if self.groups else ()

    @property
    def existing_matches(self) -> tuple[StoreAppMatch, ...]:
        return self.groups[0].existing_matches if self.groups else ()


class TaskThread(QtCore.QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            kwargs = dict(self._kwargs)
            if _accepts_log_callback(self._fn):
                kwargs["log"] = self.log.emit
            result = self._fn(*self._args, **kwargs)
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.succeeded.emit(result)


class BaselineSelectionDialog(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        title: str,
        baseline_options: tuple[tuple[str, str], ...],
        selected_ids: tuple[str, ...],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 420)
        self._baseline_options = baseline_options

        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("可为当前系统线选择多个基线版本；“全选”表示该系统线下的所有基线都支持。")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        action_row = QtWidgets.QHBoxLayout()
        self.select_all_button = QtWidgets.QPushButton("全选")
        self.clear_button = QtWidgets.QPushButton("清空")
        self.invert_button = QtWidgets.QPushButton("反选")
        self.select_all_button.clicked.connect(self._select_all)
        self.clear_button.clicked.connect(self._clear_all)
        self.invert_button.clicked.connect(self._invert_selection)
        action_row.addWidget(self.select_all_button)
        action_row.addWidget(self.clear_button)
        action_row.addWidget(self.invert_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.list_widget = QtWidgets.QListWidget()
        normalized_selected = {value for value in selected_ids if value}
        for baseline_id, minor_version in baseline_options:
            text = f"{minor_version} ({baseline_id})" if minor_version and minor_version != baseline_id else baseline_id
            item = QtWidgets.QListWidgetItem(text)
            item.setData(USER_ROLE, baseline_id)
            item.setFlags(
                item.flags()
                | (
                    QtCore.Qt.ItemFlag.ItemIsUserCheckable
                    if hasattr(QtCore.Qt, "ItemFlag")
                    else QtCore.Qt.ItemIsUserCheckable
                )
            )
            item.setCheckState(CHECKED if baseline_id in normalized_selected else UNCHECKED)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
            if hasattr(QtWidgets.QDialogButtonBox, "StandardButton")
            else QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select_all(self) -> None:
        for index in range(self.list_widget.count()):
            self.list_widget.item(index).setCheckState(CHECKED)

    def _clear_all(self) -> None:
        for index in range(self.list_widget.count()):
            self.list_widget.item(index).setCheckState(UNCHECKED)

    def _invert_selection(self) -> None:
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item.setCheckState(UNCHECKED if item.checkState() == CHECKED else CHECKED)

    def selected_baseline_ids(self) -> tuple[str, ...]:
        result: list[str] = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.checkState() != CHECKED:
                continue
            baseline_id = str(item.data(USER_ROLE) or "").strip()
            if baseline_id and baseline_id not in result:
                result.append(baseline_id)
        return tuple(result)

    def open_selector(self) -> tuple[str, ...] | None:
        if hasattr(self, "exec"):
            accepted = self.exec()
        else:
            accepted = self.exec_()
        if hasattr(QtWidgets.QDialog, "DialogCode"):
            is_ok = accepted == QtWidgets.QDialog.DialogCode.Accepted
        else:
            is_ok = accepted == QtWidgets.QDialog.Accepted
        if not is_ok:
            return None
        return self.selected_baseline_ids()


def _accepts_log_callback(fn: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return "log" in signature.parameters


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Appstore Upload Studio")
        self.resize(1400, 960)

        self.preference_store = PreferenceStore()
        self.preferences = self.preference_store.load()
        self.login_context: LoginContext | None = None
        self.capability_cache = None
        self.analysis_state: AnalysisState | None = None
        self.batch_group_configs: dict[str, BatchGroupConfig] = {}
        self.batch_existing_app_details: dict[str, dict] = {}
        self.asset_bundle: AssetBundle | None = None
        self.existing_app_detail: dict | None = None
        self.store_category_options: tuple[StoreCategoryOption, ...] = ()
        self.region_checkboxes: dict[str, QtWidgets.QCheckBox] = {}
        self._worker: TaskThread | None = None
        self._worker_success_handler: Callable[[Any], None] | None = None
        self._worker_thread: TaskThread | None = None

        self._build_ui()
        self.capture_options_dialog = CaptureOptionsDialog(self)
        self.batch_groups_dialog = BatchGroupsDialog(self)
        self.batch_groups_dialog.state_changed.connect(self._update_batch_management_summary)
        self.batch_groups_dialog.preprocess_requested.connect(self._handle_batch_group_preprocess_requested)
        self.batch_groups_dialog.capture_requested.connect(self._handle_batch_group_capture_requested)
        self.batch_groups_dialog.english_translation_requested.connect(self._handle_batch_group_english_requested)
        self.batch_groups_dialog.match_requested.connect(self._handle_batch_group_match_requested)
        self._apply_defaults()

    def closeEvent(self, event) -> None:
        thread = self._worker_thread
        if thread is not None and thread.isRunning():
            self._append_log("正在等待后台任务结束后退出。")
            thread.wait(10000)
            if thread.isRunning():
                QtWidgets.QMessageBox.warning(self, "任务仍在执行", "后台任务尚未结束，请稍后再关闭窗口。")
                event.ignore()
                return
        super().closeEvent(event)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        root_layout.addWidget(self._build_summary_group())

        splitter = QtWidgets.QSplitter()
        splitter.setOrientation(
            QtCore.Qt.Orientation.Vertical if hasattr(QtCore.Qt, "Orientation") else QtCore.Qt.Vertical
        )

        self.workflow_tabs = QtWidgets.QTabWidget()
        if hasattr(self.workflow_tabs, "setDocumentMode"):
            self.workflow_tabs.setDocumentMode(True)
        self.workflow_tabs.addTab(self._wrap_scroll_area(self._build_prepare_page()), "1. 登录与包")
        self.workflow_tabs.addTab(self._wrap_scroll_area(self._build_metadata_page()), "2. 商店信息")
        self.workflow_tabs.addTab(self._wrap_scroll_area(self._build_assets_page()), "3. 素材与版本")
        self.workflow_tabs.addTab(self._wrap_scroll_area(self._build_targets_page()), "4. 包与系统适配")
        splitter.addWidget(self.workflow_tabs)

        splitter.addWidget(self._build_log_group())
        splitter.setSizes([760, 180])

        root_layout.addWidget(splitter, 1)
        root_layout.addWidget(self._build_actions_group())
        self.setCentralWidget(central)

    def _build_summary_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("当前状态")
        layout = QtWidgets.QGridLayout(group)

        self.summary_login_label = QtWidgets.QLabel("未登录")
        self.summary_package_label = QtWidgets.QLabel("未选择包")
        self.summary_assets_label = QtWidgets.QLabel("未准备")
        self.summary_targets_label = QtWidgets.QLabel("未加载")
        self.summary_mode_label = QtWidgets.QLabel("自动判断")

        layout.addWidget(QtWidgets.QLabel("登录"), 0, 0)
        layout.addWidget(self.summary_login_label, 0, 1)
        layout.addWidget(QtWidgets.QLabel("包"), 0, 2)
        layout.addWidget(self.summary_package_label, 0, 3)
        layout.addWidget(QtWidgets.QLabel("素材"), 1, 0)
        layout.addWidget(self.summary_assets_label, 1, 1)
        layout.addWidget(QtWidgets.QLabel("兼容"), 1, 2)
        layout.addWidget(self.summary_targets_label, 1, 3)
        layout.addWidget(QtWidgets.QLabel("模式"), 0, 4)
        layout.addWidget(self.summary_mode_label, 0, 5)

        for column in (1, 3, 5):
            layout.setColumnStretch(column, 1)
        return group

    def _build_prepare_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_login_group())
        layout.addWidget(self._build_package_group())
        layout.addStretch(1)
        return page

    def _build_metadata_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_metadata_group())
        layout.addStretch(1)
        return page

    def _build_assets_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_assets_group())
        layout.addStretch(1)
        return page

    def _build_targets_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_targets_group())
        layout.addStretch(1)
        return page

    def _wrap_scroll_area(self, widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        area.setFrameShape(
            QtWidgets.QFrame.Shape.NoFrame if hasattr(QtWidgets.QFrame, "Shape") else QtWidgets.QFrame.NoFrame
        )
        return area

    def _build_log_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("任务日志")
        layout = QtWidgets.QVBoxLayout(group)
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit)
        return group

    def _build_login_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("登录")
        layout = QtWidgets.QGridLayout(group)

        self.username_edit = QtWidgets.QLineEdit()
        self.password_edit = QtWidgets.QLineEdit()
        self.password_edit.setEchoMode(
            QtWidgets.QLineEdit.EchoMode.Password
            if hasattr(QtWidgets.QLineEdit, "EchoMode")
            else QtWidgets.QLineEdit.Password
        )
        self.session_label_edit = QtWidgets.QLineEdit()
        self.session_label_edit.setPlaceholderText("扫码登录时作为 session 标识，默认 manual-login")

        self.login_button = QtWidgets.QPushButton("账号密码登录")
        self.browser_login_button = QtWidgets.QPushButton("微信扫码登录")
        self.sync_button = QtWidgets.QPushButton("同步能力缓存")
        self.login_status_label = QtWidgets.QLabel("未登录")

        self.login_button.clicked.connect(self._handle_password_login)
        self.browser_login_button.clicked.connect(self._handle_browser_login)
        self.sync_button.clicked.connect(self._handle_sync_capabilities)

        layout.addWidget(QtWidgets.QLabel("账号"), 0, 0)
        layout.addWidget(self.username_edit, 0, 1)
        layout.addWidget(QtWidgets.QLabel("密码"), 0, 2)
        layout.addWidget(self.password_edit, 0, 3)
        layout.addWidget(self.login_button, 0, 4)
        layout.addWidget(self.browser_login_button, 0, 5)
        layout.addWidget(QtWidgets.QLabel("Session 标签"), 1, 0)
        layout.addWidget(self.session_label_edit, 1, 1, 1, 2)
        layout.addWidget(self.sync_button, 1, 4)
        layout.addWidget(self.login_status_label, 1, 5)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(3, 2)
        return group

    def _build_package_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("商店操作与待上传包")
        layout = QtWidgets.QGridLayout(group)

        self.package_list = QtWidgets.QListWidget()
        self.package_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
            if hasattr(QtWidgets.QAbstractItemView, "SelectionMode")
            else QtWidgets.QAbstractItemView.ExtendedSelection
        )
        self.add_files_button = QtWidgets.QPushButton("添加包文件")
        self.add_dir_button = QtWidgets.QPushButton("选择包目录")
        self.clear_packages_button = QtWidgets.QPushButton("清空")
        self.analyze_button = QtWidgets.QPushButton("分析包")

        self.add_files_button.clicked.connect(self._add_package_files)
        self.add_dir_button.clicked.connect(self._add_package_directory)
        self.clear_packages_button.clicked.connect(self._clear_packages)
        self.analyze_button.clicked.connect(self._handle_analyze)

        self.asset_dir_edit = QtWidgets.QLineEdit()
        self.asset_dir_button = QtWidgets.QPushButton("选择资源目录")
        self.asset_dir_button.clicked.connect(self._choose_asset_directory)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("自动判断", "auto")
        self.mode_combo.addItem("新应用首提", "new")
        self.mode_combo.addItem("已有应用更新", "update")
        self.mode_combo.currentIndexChanged.connect(self._update_match_controls)

        self.match_combo = QtWidgets.QComboBox()
        self.match_combo.currentIndexChanged.connect(self._handle_match_changed)
        self.replace_assets_checkbox = QtWidgets.QCheckBox("已有应用更新时替换图标/截图")
        self.batch_summary_label = QtWidgets.QLabel("当前为单应用流程。")
        self.batch_summary_label.setWordWrap(True)
        self.batch_manager_button = QtWidgets.QPushButton("批量分组管理")
        self.batch_manager_button.clicked.connect(self._open_batch_groups_dialog)
        self.replace_assets_checkbox.setChecked(True)
        self.package_list.setMinimumHeight(220)

        layout.addWidget(self.package_list, 0, 0, 5, 3)
        layout.addWidget(self.add_files_button, 0, 3)
        layout.addWidget(self.add_dir_button, 0, 4)
        layout.addWidget(self.clear_packages_button, 0, 5)
        layout.addWidget(self.analyze_button, 0, 6)
        layout.addWidget(QtWidgets.QLabel("资源目录"), 1, 3)
        layout.addWidget(self.asset_dir_edit, 1, 4, 1, 2)
        layout.addWidget(self.asset_dir_button, 1, 6)
        layout.addWidget(QtWidgets.QLabel("提交流程"), 2, 3)
        layout.addWidget(self.mode_combo, 2, 4)
        layout.addWidget(QtWidgets.QLabel("匹配到的商店应用"), 2, 5)
        layout.addWidget(self.match_combo, 2, 6)
        layout.addWidget(self.replace_assets_checkbox, 3, 3, 1, 4)
        layout.addWidget(self.batch_summary_label, 4, 3, 1, 3)
        layout.addWidget(self.batch_manager_button, 4, 6)
        layout.setColumnStretch(0, 2)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(4, 1)
        return group

    def _build_metadata_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("商店字段与版本发布")
        layout = QtWidgets.QVBoxLayout(group)

        self.mode_hint_label = QtWidgets.QLabel()
        self.mode_hint_label.setWordWrap(True)
        layout.addWidget(self.mode_hint_label)

        self.store_app_label = QtWidgets.QLabel("当前未选择商店中的已有应用。")
        self.store_app_label.setWordWrap(True)
        layout.addWidget(self.store_app_label)

        self.app_name_edit = QtWidgets.QLineEdit()
        self.website_edit = QtWidgets.QLineEdit()
        self.category_combo = QtWidgets.QComboBox()
        self._configure_numeric_combo(self.category_combo, placeholder="输入或选择分类 ID")
        self.short_desc_edit = QtWidgets.QLineEdit()
        self.full_desc_edit = QtWidgets.QPlainTextEdit()
        self.full_desc_edit.setMinimumHeight(220)

        self.basic_info_group = QtWidgets.QGroupBox("应用基础信息")
        basic_layout = QtWidgets.QGridLayout(self.basic_info_group)
        basic_layout.addWidget(QtWidgets.QLabel("应用名"), 0, 0)
        basic_layout.addWidget(self.app_name_edit, 0, 1, 1, 3)
        basic_layout.addWidget(QtWidgets.QLabel("分类 ID"), 1, 0)
        basic_layout.addWidget(self.category_combo, 1, 1)
        basic_layout.addWidget(QtWidgets.QLabel("官网"), 1, 2)
        basic_layout.addWidget(self.website_edit, 1, 3)
        basic_layout.addWidget(QtWidgets.QLabel("一句话简介"), 2, 0)
        basic_layout.addWidget(self.short_desc_edit, 2, 1, 1, 3)
        basic_layout.addWidget(QtWidgets.QLabel("详细描述"), 3, 0)
        basic_layout.addWidget(self.full_desc_edit, 3, 1, 1, 3)
        basic_layout.setColumnStretch(1, 2)
        basic_layout.setColumnStretch(3, 2)
        layout.addWidget(self.basic_info_group)

        self.pkg_name_label = QtWidgets.QLabel("-")
        self.version_label = QtWidgets.QLabel("-")
        self.arch_label = QtWidgets.QLabel("-")
        self.family_label = QtWidgets.QLabel("-")
        self.region_group_widget = QtWidgets.QWidget()
        region_layout = QtWidgets.QHBoxLayout(self.region_group_widget)
        region_layout.setContentsMargins(0, 0, 0, 0)
        region_layout.setSpacing(12)
        for code, label in REGION_OPTIONS:
            checkbox = QtWidgets.QCheckBox(label)
            self.region_checkboxes[code] = checkbox
            region_layout.addWidget(checkbox)
        region_layout.addStretch(1)
        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setPlaceholderText("对应网页中的更新说明 / 新版本介绍")
        self.release_key_edit = QtWidgets.QLineEdit()
        self.pkg_channel_edit = QtWidgets.QLineEdit()

        self.version_group = QtWidgets.QGroupBox("版本发布（对应网页的包上传、更新说明、系统版本管理）")
        version_layout = QtWidgets.QGridLayout(self.version_group)
        version_layout.addWidget(QtWidgets.QLabel("包名"), 0, 0)
        version_layout.addWidget(self.pkg_name_label, 0, 1)
        version_layout.addWidget(QtWidgets.QLabel("版本"), 0, 2)
        version_layout.addWidget(self.version_label, 0, 3)
        version_layout.addWidget(QtWidgets.QLabel("架构"), 1, 0)
        version_layout.addWidget(self.arch_label, 1, 1)
        version_layout.addWidget(QtWidgets.QLabel("包类型"), 1, 2)
        version_layout.addWidget(self.family_label, 1, 3)
        version_layout.addWidget(QtWidgets.QLabel("地区"), 2, 0)
        version_layout.addWidget(self.region_group_widget, 2, 1)
        version_layout.addWidget(QtWidgets.QLabel("更新说明"), 2, 2)
        version_layout.addWidget(self.note_edit, 2, 3)
        self.show_advanced_checkbox = QtWidgets.QCheckBox("显示高级参数（内部字段）")
        self.show_advanced_checkbox.toggled.connect(self._toggle_advanced_fields)
        version_layout.addWidget(self.show_advanced_checkbox, 3, 0, 1, 2)
        self.advanced_fields_widget = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QGridLayout(self.advanced_fields_widget)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.addWidget(QtWidgets.QLabel("Release Key"), 0, 0)
        advanced_layout.addWidget(self.release_key_edit, 0, 1)
        advanced_layout.addWidget(QtWidgets.QLabel("包通道"), 0, 2)
        advanced_layout.addWidget(self.pkg_channel_edit, 0, 3)
        self.advanced_fields_widget.setVisible(False)
        version_layout.addWidget(self.advanced_fields_widget, 4, 0, 1, 4)
        version_layout.setColumnStretch(1, 2)
        version_layout.setColumnStretch(3, 2)
        layout.addWidget(self.version_group)
        layout.addStretch(1)
        return group

    def _build_assets_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("图标、截图与预处理")
        layout = QtWidgets.QGridLayout(group)

        self.icon_path_edit = QtWidgets.QLineEdit()
        self.icon_browse_button = QtWidgets.QPushButton("选择图标")
        self.icon_browse_button.clicked.connect(self._choose_icon)
        self.icon_preview = QtWidgets.QLabel("无图标")
        self.icon_preview.setMinimumSize(96, 96)
        self.icon_preview.setFrameShape(
            QtWidgets.QFrame.Shape.Box if hasattr(QtWidgets.QFrame, "Shape") else QtWidgets.QFrame.Box
        )
        self.icon_preview.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )

        self.screenshot_list = QtWidgets.QListWidget()
        self.screenshot_list.setViewMode(
            QtWidgets.QListView.ViewMode.IconMode
            if hasattr(QtWidgets.QListView, "ViewMode")
            else QtWidgets.QListView.IconMode
        )
        self.screenshot_list.setIconSize(QtCore.QSize(180, 100))
        self.screenshot_list.setResizeMode(
            QtWidgets.QListView.ResizeMode.Adjust
            if hasattr(QtWidgets.QListView, "ResizeMode")
            else QtWidgets.QListView.Adjust
        )
        self.screenshot_list.setMinimumHeight(320)

        self.screenshot_add_button = QtWidgets.QPushButton("添加截图")
        self.screenshot_clear_button = QtWidgets.QPushButton("清空截图")
        self.preprocess_button = QtWidgets.QPushButton("按商店规格预处理素材")
        self.capture_button = QtWidgets.QPushButton("自动截图")
        self.capture_options_summary_label = QtWidgets.QLabel()
        self.capture_options_summary_label.setWordWrap(True)
        self.capture_options_button = QtWidgets.QPushButton("自动截图参数…")

        self.screenshot_add_button.clicked.connect(self._choose_screenshots)
        self.screenshot_clear_button.clicked.connect(self._clear_screenshots)
        self.preprocess_button.clicked.connect(self._handle_preprocess_assets)
        self.capture_button.clicked.connect(self._handle_capture_screenshots)
        self.capture_options_button.clicked.connect(self._open_capture_options_dialog)

        layout.addWidget(QtWidgets.QLabel("图标"), 0, 0)
        layout.addWidget(self.icon_path_edit, 0, 1, 1, 3)
        layout.addWidget(self.icon_browse_button, 0, 4)
        layout.addWidget(self.icon_preview, 0, 5, 3, 1)
        layout.addWidget(QtWidgets.QLabel("截图"), 1, 0)
        layout.addWidget(self.screenshot_list, 1, 1, 2, 4)
        layout.addWidget(self.screenshot_add_button, 3, 1)
        layout.addWidget(self.screenshot_clear_button, 3, 2)
        layout.addWidget(self.preprocess_button, 3, 3)
        layout.addWidget(self.capture_button, 3, 4)
        layout.addWidget(self.capture_options_summary_label, 4, 1, 1, 3)
        layout.addWidget(self.capture_options_button, 4, 4)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(3, 2)
        layout.setColumnStretch(4, 2)
        return group

    def _build_targets_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("按包配置系统版本")
        layout = QtWidgets.QVBoxLayout(group)
        action_row = QtWidgets.QHBoxLayout()
        self.targets_enable_all_button = QtWidgets.QPushButton("系统线全选")
        self.targets_disable_all_button = QtWidgets.QPushButton("系统线全不选")
        self.targets_baseline_select_all_button = QtWidgets.QPushButton("所有基线全选")
        self.targets_baseline_clear_button = QtWidgets.QPushButton("所有基线清空")
        self.targets_enable_all_button.clicked.connect(lambda: self._set_all_target_rows_checked(True))
        self.targets_disable_all_button.clicked.connect(lambda: self._set_all_target_rows_checked(False))
        self.targets_baseline_select_all_button.clicked.connect(lambda: self._set_all_target_baselines(True))
        self.targets_baseline_clear_button.clicked.connect(lambda: self._set_all_target_baselines(False))
        action_row.addWidget(self.targets_enable_all_button)
        action_row.addWidget(self.targets_disable_all_button)
        action_row.addWidget(self.targets_baseline_select_all_button)
        action_row.addWidget(self.targets_baseline_clear_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        self.targets_table = QtWidgets.QTableWidget(0, 5)
        self.targets_table.setHorizontalHeaderLabels(["启用", "包文件", "架构", "系统线", "Baseline"])
        self.targets_table.horizontalHeader().setStretchLastSection(True)
        self.targets_table.verticalHeader().setVisible(False)
        self.targets_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
            if hasattr(QtWidgets.QAbstractItemView, "SelectionBehavior")
            else QtWidgets.QAbstractItemView.SelectRows
        )
        self.targets_table.setMinimumHeight(360)
        self.targets_table.itemChanged.connect(self._handle_targets_item_changed)
        layout.addWidget(self.targets_table)
        return group

    def _build_actions_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("执行")
        layout = QtWidgets.QHBoxLayout(group)
        self.output_dir_edit = QtWidgets.QLineEdit()
        self.output_dir_button = QtWidgets.QPushButton("选择输出目录")
        self.submit_button = QtWidgets.QPushButton("提交到商店")
        self.open_output_button = QtWidgets.QPushButton("打开输出目录")

        self.output_dir_button.clicked.connect(self._choose_output_directory)
        self.submit_button.clicked.connect(self._handle_submit)
        self.open_output_button.clicked.connect(self._open_output_directory)

        layout.addWidget(QtWidgets.QLabel("输出目录"))
        layout.addWidget(self.output_dir_edit)
        layout.addWidget(self.output_dir_button)
        layout.addWidget(self.submit_button)
        layout.addWidget(self.open_output_button)
        layout.setStretch(1, 1)
        return group

    def _apply_defaults(self) -> None:
        self._restore_preference_controls()
        self.replace_assets_checkbox.setChecked(True)
        self._append_log(f"Qt 绑定：{QT_BINDING}")
        self._append_log(f"能力缓存目录：{DEFAULT_CAPABILITY_CACHE_DIR}")
        self._append_log(f"表单偏好文件：{DEFAULT_PREFERENCES_PATH}")
        self._update_capture_options_summary()
        self._restore_cached_capabilities()
        self._update_match_controls()
        self._apply_submission_mode_ui()
        self._refresh_summary()
        self._start_cached_login_restore()

    def _restore_cached_capabilities(self) -> None:
        try:
            cache = load_or_sync_capabilities()
        except Exception:
            self.capability_cache = None
            self._append_log("未检测到本地系统能力缓存，登录后将自动加载。")
            return
        self.capability_cache = cache
        self._append_log("已加载本地系统能力缓存。")
        if self.analysis_state is not None:
            if self.analysis_state.is_batch:
                self._populate_targets(build_target_options_for_groups(cache, package_groups=self.analysis_state.package_groups))
            else:
                self._populate_targets(build_target_options(cache, package_group=self.analysis_state.package_group))

    def _load_capability_cache_task(self, client, *, log: Callable[[str], None]):
        try:
            return load_or_sync_capabilities(client, log=log)
        except Exception as exc:
            log(f"自动加载系统能力缓存失败：{exc}")
            return None

    def _refresh_summary(self) -> None:
        login_text = f"已登录: {self.login_context.account_label}" if self.login_context is not None else "未登录"
        self.summary_login_label.setText(login_text)

        if self.analysis_state is not None:
            if self.analysis_state.is_batch:
                group_count = len(self.analysis_state.groups)
                package_count = sum(len(group.package_group.packages) for group in self.analysis_state.groups)
                package_text = f"{group_count} 个应用 / {package_count} 个包"
            else:
                group = self.analysis_state.package_group
                package_text = f"{group.pkg_name} {group.pkg_version} / {', '.join(group.pkg_arches)}"
        else:
            package_count = self.package_list.count()
            package_text = f"已选 {package_count} 个包，待分析" if package_count else "未选择包"
        self.summary_package_label.setText(package_text)

        screenshot_count = self.screenshot_list.count()
        if self.asset_bundle is not None:
            asset_text = f"{'有图标' if self.asset_bundle.icon_path else '无图标'} / {len(self.asset_bundle.screenshot_paths)} 张已预处理"
        elif self.icon_path_edit.text().strip() or screenshot_count:
            asset_text = f"待预处理 / 当前 {screenshot_count} 张截图"
        else:
            asset_text = "未准备"
        self.summary_assets_label.setText(asset_text)

        total_targets = self.targets_table.rowCount()
        selected_targets = 0
        for row in range(total_targets):
            item = self.targets_table.item(row, 0)
            if item is not None and item.checkState() == CHECKED:
                selected_targets += 1
        self.summary_targets_label.setText(
            f"{selected_targets}/{total_targets} 条系统线" if total_targets else "未加载"
        )
        self.summary_mode_label.setText(self._effective_mode_summary())

    def _toggle_advanced_fields(self, checked: bool) -> None:
        self.advanced_fields_widget.setVisible(checked)

    def _selected_match(self) -> StoreAppMatch | None:
        if self._is_batch_analysis():
            return None
        if not self.match_combo.isEnabled():
            return None
        data = self.match_combo.currentData()
        return data if isinstance(data, StoreAppMatch) else None

    def _effective_mode(self) -> str:
        if self._is_batch_analysis():
            return "update"
        mode = self.mode_combo.currentData()
        if mode == "update":
            return "update"
        if mode == "auto" and self._selected_match() is not None:
            return "update"
        return "new"

    def _effective_mode_summary(self) -> str:
        if self._is_batch_analysis():
            return "批量分组提交"
        mode = self.mode_combo.currentData()
        effective = self._effective_mode()
        if mode == "auto":
            return "自动判断 -> 已有应用更新" if effective == "update" else "自动判断 -> 新应用首提"
        return self.mode_combo.currentText()

    def _group_key(self, package_group: PackageGroup) -> str:
        return "|".join(
            (
                package_group.pkg_name,
                package_group.pkg_version,
                package_group.package_family,
                package_group.package_format,
            )
        )

    def _entry_for_group_key(self, group_key: str) -> AnalysisEntry | None:
        if self.analysis_state is None:
            return None
        for entry in self.analysis_state.groups:
            if self._group_key(entry.package_group) == group_key:
                return entry
        return None

    def _match_for_config(self, group_key: str, config: BatchGroupConfig) -> StoreAppMatch | None:
        entry = self._entry_for_group_key(group_key)
        if entry is None:
            return None
        if config.selected_match_app_id:
            for match in entry.existing_matches:
                if match.app_id == config.selected_match_app_id:
                    return match
        if len(entry.existing_matches) == 1:
            return entry.existing_matches[0]
        return None

    def _initialize_batch_group_configs(self, state: AnalysisState) -> None:
        default_category = self._combo_text(self.category_combo) or (
            self.preferences.recent_category_ids[0] if self.preferences.recent_category_ids else "1"
        )
        default_regions = self._current_region_codes()
        default_asset_dir = self.asset_dir_edit.text().strip()
        default_note = self.note_edit.text().strip()
        configs: dict[str, BatchGroupConfig] = {}
        for entry in state.groups:
            package_group = entry.package_group
            config = BatchGroupConfig(
                group_key=self._group_key(package_group),
                submission_mode="auto",
                selected_match_app_id=(entry.existing_matches[0].app_id if len(entry.existing_matches) == 1 else ""),
                app_name_zh=package_group.display_name,
                website=package_group.homepage,
                short_desc_zh=package_group.short_description,
                full_desc_zh=package_group.full_description,
                category_id=default_category,
                region_codes=default_regions,
                asset_dir=default_asset_dir,
                replace_assets=False,
                note_zh=default_note,
                auto_translate_en=True,
                metadata_edited=False,
                editor_edited=False,
                manual_en_edited=False,
            )
            configs[config.group_key] = config
        self.batch_group_configs = configs

    def _batch_group_config(self, group_key: str) -> BatchGroupConfig | None:
        return self.batch_group_configs.get(group_key)

    def _batch_group_entry(self, group_key: str) -> AnalysisEntry | None:
        return self._entry_for_group_key(group_key)

    def _batch_group_output_dir(self, package_group: PackageGroup, *, category: str) -> Path:
        return (
            self._current_output_dir()
            / category
            / f"{self._safe_output_segment(package_group.pkg_name)}-{self._safe_output_segment(package_group.pkg_version)}"
        )

    def _safe_output_segment(self, value: str) -> str:
        normalized = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
        normalized = normalized.strip("-._")
        return normalized or "app"

    def _refresh_batch_dialog_view(self) -> None:
        if self._is_batch_analysis():
            self.batch_groups_dialog.refresh_view()
            self._update_batch_management_summary()
        self._refresh_summary()

    def _selected_batch_match(self, group_key: str) -> StoreAppMatch | None:
        config = self._batch_group_config(group_key)
        if config is None:
            return None
        return self._match_for_config(group_key, config)

    def _apply_batch_group_existing_detail(
        self,
        group_key: str,
        detail: dict,
        *,
        source_app_id: str,
    ) -> None:
        config = self._batch_group_config(group_key)
        entry = self._batch_group_entry(group_key)
        if config is None or entry is None:
            return
        if config.editor_edited:
            return
        defaults = build_existing_detail_editor_defaults(detail, fallback_name=entry.package_group.display_name)
        config.app_name_zh = str(defaults.get("app_name_zh", "")).strip()
        config.website = str(defaults.get("website", "")).strip()
        config.short_desc_zh = str(defaults.get("short_desc_zh", "")).strip()
        config.full_desc_zh = str(defaults.get("full_desc_zh", "")).strip()
        config.category_id = str(defaults.get("category_id", "")).strip() or config.category_id or "1"
        region_codes = tuple(str(code).strip() for code in (defaults.get("region_codes") or ()) if str(code).strip())
        if region_codes:
            config.region_codes = region_codes
        config.app_name_en = str(defaults.get("app_name_en", "")).strip()
        config.short_desc_en = str(defaults.get("short_desc_en", "")).strip()
        config.full_desc_en = str(defaults.get("full_desc_en", "")).strip()
        config.note_en = str(defaults.get("note_en", "")).strip()
        config.manual_en_edited = False
        self._append_log(f"{entry.package_group.pkg_name}: 已回填商店现有资料 (app_id={source_app_id})。")
        self._refresh_batch_dialog_view()

    def _load_batch_match_detail_task(
        self,
        match: StoreAppMatch,
        *,
        log: Callable[[str], None],
    ) -> tuple[str, dict]:
        detail = fetch_existing_app_detail(self.login_context.client, match, log=log)
        return match.app_id, detail

    def _handle_batch_group_match_requested(self, group_key: str) -> None:
        if self.login_context is None or not self._is_batch_analysis():
            return
        config = self._batch_group_config(group_key)
        if config is None or config.submission_mode == "new":
            return
        match = self._selected_batch_match(group_key)
        if match is None:
            return
        cached = self.batch_existing_app_details.get(match.app_id)
        if cached is not None:
            self._apply_batch_group_existing_detail(group_key, cached, source_app_id=match.app_id)
            return
        self._run_worker(
            self._load_batch_match_detail_task,
            match,
            on_success=lambda result, g=group_key: self._on_batch_group_match_detail_ready(g, result),
        )

    def _on_batch_group_match_detail_ready(self, group_key: str, result: tuple[str, dict]) -> None:
        app_id, detail = result
        self.batch_existing_app_details[app_id] = detail
        match = self._selected_batch_match(group_key)
        if match is None or match.app_id != app_id:
            return
        self._apply_batch_group_existing_detail(group_key, detail, source_app_id=app_id)

    def _batch_status_counts(self) -> tuple[int, int, int]:
        if not self._is_batch_analysis() or self.analysis_state is None:
            return 0, 0, 0
        unique_matches = 0
        missing_matches = 0
        ambiguous_matches = 0
        for entry in self.analysis_state.groups:
            match_count = len(entry.existing_matches)
            if match_count == 1:
                unique_matches += 1
            elif match_count == 0:
                missing_matches += 1
            else:
                ambiguous_matches += 1
        return unique_matches, missing_matches, ambiguous_matches

    def _batch_mode_counts(self) -> tuple[int, int, int]:
        if not self._is_batch_analysis() or self.analysis_state is None:
            return 0, 0, 0
        auto_count = 0
        update_count = 0
        new_count = 0
        for entry in self.analysis_state.groups:
            config = self.batch_group_configs.get(self._group_key(entry.package_group))
            mode = config.submission_mode if config is not None else "auto"
            if mode == "update":
                update_count += 1
            elif mode == "new":
                new_count += 1
            else:
                auto_count += 1
        return auto_count, update_count, new_count

    def _update_batch_management_summary(self) -> None:
        if not self._is_batch_analysis() or self.analysis_state is None:
            self.batch_summary_label.setText("当前为单应用流程。")
            self.batch_manager_button.setEnabled(False)
            return
        unique_matches, missing_matches, ambiguous_matches = self._batch_status_counts()
        auto_count, update_count, new_count = self._batch_mode_counts()
        group_count = len(self.analysis_state.groups)
        self.batch_summary_label.setText(
            f"批量分组：{group_count} 组；自动 {auto_count} 组；更新 {update_count} 组；新提 {new_count} 组；"
            f"唯一匹配 {unique_matches} 组；未找到 {missing_matches} 组；冲突 {ambiguous_matches} 组。"
        )
        self.batch_manager_button.setEnabled(True)

    def _apply_submission_mode_ui(self) -> None:
        is_batch = self._is_batch_analysis()
        effective_mode = self._effective_mode()
        is_new = effective_mode == "new"
        match = self._selected_match()
        if is_batch:
            self.mode_hint_label.setText(
                "当前为批量分组提交流程：会按包名和版本分组逐个处理，每组都可以选择自动判断、批量更新或批量提新。"
                "详细配置在“批量分组管理”子窗口中完成。"
            )
        elif is_new:
            self.mode_hint_label.setText(
                "当前将按新应用首提处理：会提交应用基础信息、图标截图、包文件、更新说明和系统适配。"
            )
        else:
            self.mode_hint_label.setText(
                "当前将按已有应用更新处理：可以修改应用名、分类、官网、简介和详情，并提交更新说明、包文件、系统适配；"
                "也可选择是否替换图标/截图。"
            )
        if is_batch:
            unique_matches, missing_matches, ambiguous_matches = self._batch_status_counts()
            auto_count, update_count, new_count = self._batch_mode_counts()
            group_count = len(self.analysis_state.groups) if self.analysis_state is not None else 0
            self.store_app_label.setText(
                f"批量提交流程：共 {group_count} 组；自动 {auto_count} 组；批量更新 {update_count} 组；批量提新 {new_count} 组；"
                f"唯一匹配 {unique_matches} 组；未找到 {missing_matches} 组；匹配冲突 {ambiguous_matches} 组。"
                "详细配置请点“批量分组管理”。"
            )
        elif match is not None:
            self.store_app_label.setText(f"目标商店应用：{match.app_name or match.pkg_name} (app_id={match.app_id})")
        else:
            self.store_app_label.setText("当前未选择商店中的已有应用。")

        editable_metadata = not is_batch
        self.mode_combo.setEnabled(not is_batch)
        self.app_name_edit.setReadOnly(not editable_metadata)
        self.website_edit.setReadOnly(not editable_metadata)
        self.short_desc_edit.setReadOnly(not editable_metadata)
        self.full_desc_edit.setReadOnly(not editable_metadata)
        self.category_combo.setEnabled(editable_metadata)
        for checkbox in self.region_checkboxes.values():
            checkbox.setEnabled(editable_metadata)
        if is_batch:
            self.replace_assets_checkbox.setChecked(False)
        self.replace_assets_checkbox.setEnabled(effective_mode == "update" and not is_batch)
        self.icon_path_edit.setReadOnly(is_batch)
        self.icon_browse_button.setEnabled(not is_batch)
        self.screenshot_add_button.setEnabled(not is_batch)
        self.screenshot_clear_button.setEnabled(not is_batch)
        self.preprocess_button.setEnabled(not is_batch)
        self.capture_button.setEnabled(not is_batch)
        self.capture_options_button.setEnabled(not is_batch)
        self.basic_info_group.setTitle(
            "应用基础信息（批量模式下沿用商店现有资料）"
            if is_batch
            else (
                "应用基础信息（新应用首提会创建；已有应用更新时会覆盖商店现有资料）"
                if is_new
                else "应用基础信息（已有应用更新时可修改并覆盖商店现有资料）"
            )
        )
        self._update_batch_management_summary()

    def _is_batch_analysis(self) -> bool:
        return self.analysis_state is not None and self.analysis_state.is_batch

    def _configure_numeric_combo(self, combo: QtWidgets.QComboBox, *, placeholder: str) -> None:
        combo.setEditable(True)
        combo.setInsertPolicy(
            QtWidgets.QComboBox.InsertPolicy.NoInsert
            if hasattr(QtWidgets.QComboBox, "InsertPolicy")
            else QtWidgets.QComboBox.NoInsert
        )
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText(placeholder)
            line_edit.setValidator(QtGui.QIntValidator(1, 999999, combo))

    def _restore_preference_controls(self) -> None:
        output_dir = self.preferences.last_output_dir or str(DEFAULT_OUTPUT_ROOT.resolve())
        self.output_dir_edit.setText(output_dir)
        self.asset_dir_edit.setText(self.preferences.last_asset_dir)
        self.release_key_edit.setText(self.preferences.last_release_key or "stable")
        self.pkg_channel_edit.setText(self.preferences.last_pkg_channel or "stable")
        self.session_label_edit.setText(self.preferences.last_session_account)
        self._reset_editable_combo(
            self.category_combo,
            self.preferences.recent_category_ids,
            current=(self.preferences.recent_category_ids[0] if self.preferences.recent_category_ids else "1"),
        )
        saved_region = self.preferences.recent_regions[0] if self.preferences.recent_regions else "1"
        self._set_region_codes(tuple(token.strip() for token in saved_region.split(",") if token.strip()) or ("1",))

    def _reset_editable_combo(
        self,
        combo: QtWidgets.QComboBox,
        values: tuple[str, ...],
        *,
        current: str,
    ) -> None:
        block_state = combo.blockSignals(True)
        combo.clear()
        for value in values:
            combo.addItem(value, value)
        normalized = current.strip()
        if normalized and combo.findText(normalized) < 0:
            combo.addItem(normalized, normalized)
        if normalized:
            combo.setEditText(normalized)
            combo.setCurrentIndex(combo.findText(normalized))
        elif combo.count():
            combo.setCurrentIndex(0)
        combo.blockSignals(block_state)

    def _combo_text(self, combo: QtWidgets.QComboBox) -> str:
        return combo.currentText().strip()

    def _set_combo_value(self, combo: QtWidgets.QComboBox, value: str) -> None:
        normalized = value.strip()
        if normalized and combo.findText(normalized) < 0:
            combo.addItem(normalized, normalized)
        combo.setEditText(normalized)

    def _persist_preferences(
        self,
        *,
        category_id: str = "",
        region: str = "",
        output_dir: str | None = None,
        asset_dir: str | None = None,
        release_key: str | None = None,
        pkg_channel: str | None = None,
        session_account: str | None = None,
    ) -> None:
        recent_category_ids = self.preferences.recent_category_ids
        recent_regions = self.preferences.recent_regions
        if category_id.strip().isdigit():
            recent_category_ids = remember_value(recent_category_ids, category_id)
        if region.strip() and all(token.strip().isdigit() for token in region.split(",")):
            recent_regions = remember_value(recent_regions, region)
        self.preferences = UIPreferences(
            recent_category_ids=recent_category_ids,
            recent_regions=recent_regions,
            last_output_dir=output_dir if output_dir is not None else self.preferences.last_output_dir,
            last_asset_dir=asset_dir if asset_dir is not None else self.preferences.last_asset_dir,
            last_release_key=release_key if release_key is not None else self.preferences.last_release_key,
            last_pkg_channel=pkg_channel if pkg_channel is not None else self.preferences.last_pkg_channel,
            last_session_account=(
                session_account if session_account is not None else self.preferences.last_session_account
            ),
        )
        self.preference_store.save(self.preferences)
        current_category = category_id or self._combo_text(self.category_combo) or "1"
        if self.store_category_options:
            self._set_category_options(self.store_category_options, current_id=current_category)
        else:
            self._reset_editable_combo(
                self.category_combo,
                self.preferences.recent_category_ids,
                current=current_category,
            )
        if region:
            self._set_region_codes(tuple(token.strip() for token in region.split(",") if token.strip()))

    def _current_category_id(self) -> int:
        raw = self._combo_text(self.category_combo) or "1"
        if raw.isdigit():
            value = int(raw)
        else:
            current_data = self.category_combo.currentData()
            if current_data is not None and str(current_data).strip().isdigit():
                value = int(str(current_data).strip())
            elif raw.endswith(")") and "(" in raw:
                value = int(raw.rsplit("(", 1)[1].rstrip(") ").strip())
            else:
                raise ValueError("分类 ID 无法识别。")
        if value <= 0:
            raise ValueError("分类 ID 必须是正整数。")
        return value

    def _set_region_codes(self, region_codes: tuple[str, ...]) -> None:
        normalized = set(region_codes or ("1",))
        for code, checkbox in self.region_checkboxes.items():
            checkbox.setChecked(code in normalized)

    def _current_region_codes(self) -> tuple[str, ...]:
        result = tuple(code for code, checkbox in self.region_checkboxes.items() if checkbox.isChecked())
        if not result:
            raise ValueError("至少选择一个上架地区。")
        return result

    def _current_region_text(self) -> str:
        return ",".join(self._current_region_codes())

    def _set_category_options(self, options: tuple[StoreCategoryOption, ...], *, current_id: str = "") -> None:
        self.store_category_options = options
        block_state = self.category_combo.blockSignals(True)
        current_text = self._combo_text(self.category_combo)
        self.category_combo.clear()
        for option in options:
            if option.category_id == "0":
                continue
            label = f"{option.name} ({option.category_id})"
            self.category_combo.addItem(label, option.category_id)
        normalized = current_id.strip() or current_text or (
            self.preferences.recent_category_ids[0] if self.preferences.recent_category_ids else "1"
        )
        index = self.category_combo.findData(normalized)
        if index >= 0:
            self.category_combo.setCurrentIndex(index)
        else:
            self.category_combo.setEditText(normalized)
        self.category_combo.blockSignals(block_state)

    def _load_category_options_task(self, client, *, log: Callable[[str], None]) -> tuple[StoreCategoryOption, ...]:
        try:
            return fetch_category_options(client, log=log)
        except Exception as exc:
            log(f"加载应用分类失败，将保留手动输入：{exc}")
            return ()

    def _add_package_files(self) -> None:
        files, _ = get_open_file_names(
            self,
            "选择包文件",
            "",
            "Packages (*.deb *.uab *.layer);;All Files (*)",
        )
        self._add_package_paths(tuple(files))

    def _add_package_directory(self) -> None:
        directory = get_existing_directory(self, "选择包目录")
        if not directory:
            return
        self._add_package_paths(tuple(str(path) for path in find_package_files(directory)))

    def _add_package_paths(self, paths: tuple[str, ...] | list[str]) -> None:
        existing_texts = {
            self.package_list.item(index).text()
            for index in range(self.package_list.count())
        }
        changed = False
        for path in paths:
            normalized = str(Path(path).expanduser().resolve())
            if normalized in existing_texts:
                continue
            self.package_list.addItem(normalized)
            existing_texts.add(normalized)
            changed = True
        if not changed:
            return
        self.analysis_state = None
        self.batch_group_configs = {}
        self.asset_bundle = None
        self.existing_app_detail = None
        self._refresh_summary()

    def _clear_packages(self) -> None:
        self.package_list.clear()
        self.analysis_state = None
        self.batch_group_configs = {}
        self.asset_bundle = None
        self.existing_app_detail = None
        self._refresh_summary()

    def _choose_asset_directory(self) -> None:
        directory = get_existing_directory(self, "选择资源目录")
        if directory:
            self.asset_dir_edit.setText(directory)
            self.asset_bundle = None
            self._persist_preferences(asset_dir=directory)
            self._refresh_summary()

    def _choose_icon(self) -> None:
        path, _ = get_open_file_name(
            self,
            "选择图标",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.ico);;All Files (*)",
        )
        if not path:
            return
        self.icon_path_edit.setText(path)
        self._set_icon_preview(Path(path))
        self.asset_bundle = None
        self._refresh_summary()

    def _choose_screenshots(self) -> None:
        files, _ = get_open_file_names(
            self,
            "选择截图",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)",
        )
        for path in files:
            self._append_screenshot(Path(path))
        if files:
            self.asset_bundle = None
            self._refresh_summary()

    def _clear_screenshots(self) -> None:
        self.screenshot_list.clear()
        self.asset_bundle = None
        self._refresh_summary()

    def _choose_output_directory(self) -> None:
        directory = get_existing_directory(self, "选择输出目录")
        if directory:
            self.output_dir_edit.setText(directory)
            self._persist_preferences(output_dir=directory)

    def _open_capture_options_dialog(self) -> None:
        if self.capture_options_dialog.open_editor():
            self._update_capture_options_summary()

    def _open_batch_groups_dialog(self) -> None:
        if not self._is_batch_analysis() or self.analysis_state is None:
            QtWidgets.QMessageBox.information(self, "当前不是批量模式", "请先分析多个不同应用或版本的包。")
            return
        self.batch_groups_dialog.set_state(
            entries=self.analysis_state.groups,
            configs=self.batch_group_configs,
            category_options=self.store_category_options,
        )
        self.batch_groups_dialog.open_dialog()

    def _handle_batch_group_preprocess_requested(self, group_key: str) -> None:
        entry = self._batch_group_entry(group_key)
        config = self._batch_group_config(group_key)
        if entry is None or config is None:
            return
        output_dir = self._batch_group_output_dir(entry.package_group, category="batch-preprocessed")
        self._run_worker(
            preprocess_submission_assets,
            entry.package_group,
            asset_dir=(Path(config.asset_dir).expanduser().resolve() if config.asset_dir.strip() else self._current_asset_dir()),
            manual_icon_path=None,
            manual_screenshot_paths=tuple(Path(path).expanduser().resolve() for path in config.manual_screenshot_paths),
            output_dir=output_dir,
            on_success=lambda bundle, g=group_key: self._on_batch_group_assets_ready(g, bundle),
        )

    def _on_batch_group_assets_ready(self, group_key: str, bundle: AssetBundle) -> None:
        config = self._batch_group_config(group_key)
        if config is None:
            return
        config.prepared_icon_path = str(bundle.icon_path) if bundle.icon_path is not None else ""
        config.prepared_screenshot_paths = tuple(str(path) for path in bundle.screenshot_paths)
        config.asset_warnings = tuple(bundle.warnings)
        self._append_log(
            f"分组素材已预处理：{group_key}，图标 {'已准备' if bundle.icon_path else '缺失'}，有效截图 {len(bundle.screenshot_paths)} 张。"
        )
        self._refresh_batch_dialog_view()

    def _handle_batch_group_capture_requested(self, group_key: str) -> None:
        entry = self._batch_group_entry(group_key)
        config = self._batch_group_config(group_key)
        if entry is None or config is None:
            return
        output_dir = self._batch_group_output_dir(entry.package_group, category="batch-capture")
        self._run_worker(
            capture_screenshots_for_group,
            entry.package_group,
            output_dir=output_dir,
            **self.capture_options_dialog.values(),
            on_success=lambda screenshots, g=group_key: self._on_batch_group_capture_ready(g, screenshots),
        )

    def _on_batch_group_capture_ready(self, group_key: str, screenshots: tuple[Path, ...]) -> None:
        config = self._batch_group_config(group_key)
        if config is None:
            return
        config.manual_screenshot_paths = tuple(str(path) for path in screenshots)
        config.prepared_icon_path = ""
        config.prepared_screenshot_paths = ()
        if len(screenshots) < 3:
            config.asset_warnings = (
                f"自动截图仅保留 {len(screenshots)} 张有效截图，请再补足到至少 3 张后提交新应用或替换素材。",
            )
        else:
            config.asset_warnings = ()
        self._append_log(f"分组自动截图完成：{group_key}，保留 {len(screenshots)} 张截图。")
        self._refresh_batch_dialog_view()

    def _handle_batch_group_english_requested(self, group_key: str) -> None:
        config = self._batch_group_config(group_key)
        if config is None:
            return
        self._run_worker(
            generate_english_listing_texts,
            app_name_zh=config.app_name_zh,
            short_desc_zh=config.short_desc_zh,
            full_desc_zh=config.full_desc_zh,
            note_zh=config.note_zh.strip() or self.note_edit.text().strip(),
            on_success=lambda translated, g=group_key: self._on_batch_group_english_ready(g, translated),
        )

    def _on_batch_group_english_ready(self, group_key: str, translated: dict[str, str]) -> None:
        config = self._batch_group_config(group_key)
        if config is None:
            return
        config.app_name_en = str(translated.get("name", "")).strip()
        config.short_desc_en = str(translated.get("brief_info", "")).strip()
        config.full_desc_en = str(translated.get("desc_info", "")).strip()
        config.note_en = str(translated.get("update_desc", "")).strip()
        config.manual_en_edited = True
        config.editor_edited = True
        self._append_log(f"已生成分组英文文案：{group_key}")
        self._refresh_batch_dialog_view()
        self._apply_submission_mode_ui()
        self._refresh_summary()

    def _update_capture_options_summary(self) -> None:
        self.capture_options_summary_label.setText(self.capture_options_dialog.summary_text())

    def _open_output_directory(self) -> None:
        output_dir = Path(self.output_dir_edit.text().strip() or DEFAULT_OUTPUT_ROOT)
        output_dir.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(output_dir)))

    def _set_icon_preview(self, path: Path | None) -> None:
        if path is None or not path.exists():
            self.icon_preview.setPixmap(QtGui.QPixmap())
            self.icon_preview.setText("无图标")
            return
        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull():
            self.icon_preview.setPixmap(QtGui.QPixmap())
            self.icon_preview.setText(path.name)
            return
        self.icon_preview.setText("")
        self.icon_preview.setPixmap(
            pixmap.scaled(
                96,
                96,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio
                if hasattr(QtCore.Qt, "AspectRatioMode")
                else QtCore.Qt.KeepAspectRatio,
            )
        )

    def _append_screenshot(self, path: Path) -> None:
        normalized = path.expanduser().resolve()
        for index in range(self.screenshot_list.count()):
            item = self.screenshot_list.item(index)
            if item.data(USER_ROLE) == str(normalized):
                return
        item = QtWidgets.QListWidgetItem(normalized.name)
        item.setData(USER_ROLE, str(normalized))
        pixmap = QtGui.QPixmap(str(normalized))
        if not pixmap.isNull():
            item.setIcon(QtGui.QIcon(pixmap))
        self.screenshot_list.addItem(item)

    def _current_package_paths(self) -> list[Path]:
        return [Path(self.package_list.item(index).text()) for index in range(self.package_list.count())]

    def _current_manual_screenshots(self) -> tuple[Path, ...]:
        return tuple(
            Path(self.screenshot_list.item(index).data(USER_ROLE))
            for index in range(self.screenshot_list.count())
        )

    def _current_asset_dir(self) -> Path | None:
        raw = self.asset_dir_edit.text().strip()
        return Path(raw).expanduser().resolve() if raw else None

    def _current_manual_icon(self) -> Path | None:
        raw = self.icon_path_edit.text().strip()
        return Path(raw).expanduser().resolve() if raw else None

    def _current_output_dir(self) -> Path:
        raw = self.output_dir_edit.text().strip()
        return Path(raw or DEFAULT_OUTPUT_ROOT).expanduser().resolve()

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{timestamp}] {message}")

    def _set_busy(self, busy: bool) -> None:
        controls = [
            self.login_button,
            self.browser_login_button,
            self.sync_button,
            self.add_files_button,
            self.add_dir_button,
            self.clear_packages_button,
            self.analyze_button,
            self.asset_dir_button,
            self.icon_browse_button,
            self.screenshot_add_button,
            self.screenshot_clear_button,
            self.preprocess_button,
            self.capture_button,
            self.capture_options_button,
            self.batch_manager_button,
            self.targets_enable_all_button,
            self.targets_disable_all_button,
            self.targets_baseline_select_all_button,
            self.targets_baseline_clear_button,
            self.submit_button,
        ]
        for control in controls:
            control.setEnabled(not busy)
        self.batch_groups_dialog.set_busy(busy)

    def _run_worker(self, fn: Callable[..., Any], *args, on_success: Callable[[Any], None], **kwargs) -> None:
        if self._worker_thread is not None:
            QtWidgets.QMessageBox.warning(self, "Busy", "已有任务正在执行，请稍候。")
            return
        thread = TaskThread(fn, *args, **kwargs)
        thread.setParent(self)
        thread.log.connect(self._append_log)
        thread.succeeded.connect(self._handle_worker_finished)
        thread.failed.connect(self._handle_worker_failed)
        thread.finished.connect(thread.deleteLater)
        self._worker = thread
        self._worker_success_handler = on_success
        self._worker_thread = thread
        self._set_busy(True)
        thread.start()

    @Slot(object)
    def _handle_worker_finished(self, result: Any) -> None:
        thread = self._worker_thread
        on_success = self._worker_success_handler
        self._worker = None
        self._worker_success_handler = None
        self._worker_thread = None
        if thread is not None and thread.isRunning():
            thread.wait()
        self._set_busy(False)
        self._apply_submission_mode_ui()
        if on_success is not None:
            on_success(result)

    @Slot(str)
    def _handle_worker_failed(self, error_text: str) -> None:
        thread = self._worker_thread
        self._worker = None
        self._worker_success_handler = None
        self._worker_thread = None
        if thread is not None and thread.isRunning():
            thread.wait()
        self._set_busy(False)
        self._apply_submission_mode_ui()
        self._append_log(error_text.strip())
        QtWidgets.QMessageBox.critical(self, "任务失败", self._summarize_worker_error(error_text))

    def _summarize_worker_error(self, error_text: str) -> str:
        normalized = error_text.strip()
        if (
            "无法连接英文文案生成服务" in normalized
            or "ConnectionRefusedError" in normalized
            or "Failed to establish a new connection" in normalized
            or "[Errno 111]" in normalized
        ):
            return (
                "无法连接英文文案生成服务。\n"
                "请先启动可用的 OpenAI 兼容接口，"
                "或检查 APPSTORE_AI_BASE_URL / APPSTORE_AI_MODEL / APPSTORE_AI_API_KEY 配置。"
            )
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return "后台任务失败。"
        last_line = lines[-1]
        if len(last_line) > 300:
            last_line = f"{last_line[:300]}..."
        return last_line

    def _handle_password_login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            QtWidgets.QMessageBox.warning(self, "缺少登录信息", "请输入账号和密码。")
            return
        self._run_worker(
            login_with_credentials,
            username,
            password,
            on_success=self._on_login_success,
        )

    def _start_cached_login_restore(self) -> None:
        preferred_account = self.preferences.last_session_account.strip() or self.session_label_edit.text().strip()
        self._run_worker(
            try_restore_cached_login,
            preferred_account,
            on_success=self._on_cached_login_checked,
        )

    def _on_cached_login_checked(self, context: LoginContext | None) -> None:
        if context is None:
            self._refresh_summary()
            return
        self._append_log("启动时已自动恢复缓存登录态。")
        self._on_login_success(context)

    def _handle_browser_login(self) -> None:
        label = self.session_label_edit.text().strip() or self.username_edit.text().strip() or "manual-login"
        try:
            dialog = WechatQrLoginDialog(label, self)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "无法打开微信扫码登录", str(exc))
            return
        self._append_log("正在提取微信扫码二维码，并等待扫码确认。")
        state = dialog.open_and_capture()
        if state is None:
            self._append_log("微信扫码登录已取消。")
            return
        self._run_worker(
            login_with_browser_state,
            state,
            on_success=self._on_login_success,
        )

    def _on_login_success(self, context: LoginContext) -> None:
        self.login_context = context
        self.batch_existing_app_details = {}
        session_account = context.session_state_path.stem if context.session_state_path is not None else ""
        if session_account:
            self.session_label_edit.setText(session_account)
        self._persist_preferences(session_account=session_account or self.session_label_edit.text().strip())
        self.login_status_label.setText(f"已登录: {context.account_label}")
        self._append_log(f"当前登录模式：{context.login_mode}")
        self._refresh_summary()
        self._run_worker(
            self._load_category_options_task,
            context.client,
            on_success=self._on_category_options_ready,
        )

    def _on_category_options_ready(self, options: tuple[StoreCategoryOption, ...]) -> None:
        if options:
            self._set_category_options(options)
            self._append_log("应用分类列表已同步。")
            if self._is_batch_analysis():
                self._update_batch_management_summary()
        if self.login_context is not None and self.capability_cache is None:
            self._run_worker(
                self._load_capability_cache_task,
                self.login_context.client,
                on_success=self._on_capabilities_ready,
            )
            return
        self._refresh_summary()

    def _handle_sync_capabilities(self) -> None:
        if self.login_context is None:
            QtWidgets.QMessageBox.warning(self, "未登录", "请先登录。")
            return
        self._run_worker(
            sync_capabilities,
            self.login_context.client,
            on_success=self._on_capabilities_ready,
        )

    def _on_capabilities_ready(self, cache) -> None:
        if cache is None:
            self._refresh_summary()
            return
        self.capability_cache = cache
        self._append_log("能力缓存已就绪。")
        if self.analysis_state is not None:
            if self.analysis_state.is_batch:
                self._populate_targets(build_target_options_for_groups(cache, package_groups=self.analysis_state.package_groups))
            else:
                self._populate_targets(build_target_options(cache, package_group=self.analysis_state.package_group))
        self._refresh_summary()

    def _handle_analyze(self) -> None:
        package_paths = self._current_package_paths()
        if not package_paths:
            QtWidgets.QMessageBox.warning(self, "未选择包文件", "请先选择至少一个包文件。")
            return
        self._run_worker(
            self._analyze_packages_task,
            package_paths,
            self._current_asset_dir(),
            on_success=self._on_analysis_ready,
        )

    def _analyze_packages_task(self, package_paths: list[Path], asset_dir: Path | None, *, log: Callable[[str], None]) -> AnalysisState:
        log("分析包文件与元数据。")
        package_groups = analyze_package_groups(package_paths)
        from ui.assets import detect_asset_candidates

        groups: list[AnalysisEntry] = []
        for package_group in package_groups:
            existing_matches: tuple[StoreAppMatch, ...] = ()
            if self.login_context is not None:
                try:
                    existing_matches = find_existing_apps(self.login_context.client, pkg_name=package_group.pkg_name)
                    if existing_matches:
                        log(f"{package_group.pkg_name}: 商店中已找到 {len(existing_matches)} 个同包名应用。")
                    else:
                        log(f"{package_group.pkg_name}: 商店中未找到同包名应用。")
                except Exception as exc:
                    log(f"{package_group.pkg_name}: 查询商店应用失败：{exc}")
            icon_path, screenshot_paths = detect_asset_candidates(package_group, asset_dir=asset_dir)
            groups.append(
                AnalysisEntry(
                    package_group=package_group,
                    icon_path=icon_path,
                    screenshot_paths=screenshot_paths,
                    existing_matches=existing_matches,
                )
            )
        return AnalysisState(
            groups=tuple(groups),
            asset_dir=asset_dir,
        )

    def _on_analysis_ready(self, state: AnalysisState) -> None:
        self.analysis_state = state
        self.batch_group_configs = {}
        self.asset_bundle = None
        self.existing_app_detail = None
        if state.is_batch:
            self._initialize_batch_group_configs(state)
            group_count = len(state.groups)
            package_count = sum(len(entry.package_group.packages) for entry in state.groups)
            family_labels = sorted({f"{entry.package_group.package_family}/{entry.package_group.package_format}" for entry in state.groups})
            group_labels = ", ".join(entry.package_group.pkg_name for entry in state.groups[:3])
            if len(state.groups) > 3:
                group_labels = f"{group_labels}, +{len(state.groups) - 3}"
            self.app_name_edit.clear()
            self.website_edit.clear()
            self.short_desc_edit.clear()
            self.full_desc_edit.clear()
            self.pkg_name_label.setText(group_labels or f"{group_count} 个应用")
            self.version_label.setText(f"{group_count} 组")
            self.arch_label.setText(f"{package_count} 个包")
            self.family_label.setText(", ".join(family_labels))
            if not self.note_edit.text().strip():
                self.note_edit.setText("批量更新")
            self._populate_matches(())
            self.icon_path_edit.clear()
            self._set_icon_preview(None)
            self.screenshot_list.clear()
            if self.capability_cache is not None:
                self._populate_targets(build_target_options_for_groups(self.capability_cache, package_groups=state.package_groups))
            self._append_log(f"已分析 {group_count} 个应用组，共 {package_count} 个包。")
            for entry in state.groups:
                group = entry.package_group
                self._append_log(f"  - {group.pkg_name} {group.pkg_version} [{', '.join(group.pkg_arches)}]")
        else:
            group = state.package_group
            self.app_name_edit.setText(group.display_name)
            self.pkg_name_label.setText(group.pkg_name)
            self.version_label.setText(group.pkg_version)
            self.arch_label.setText(", ".join(group.pkg_arches))
            self.family_label.setText(f"{group.package_family}/{group.package_format}")
            self.website_edit.setText(group.homepage)
            self.short_desc_edit.setText(group.short_description)
            self.full_desc_edit.setPlainText(group.full_description)
            self.note_edit.setText(f"更新 {group.pkg_version}")
            self._populate_matches(state.existing_matches)
            self.icon_path_edit.setText(str(state.icon_path) if state.icon_path else "")
            self._set_icon_preview(state.icon_path)
            self.screenshot_list.clear()
            for path in state.screenshot_paths:
                self._append_screenshot(path)
            if self.capability_cache is not None:
                self._populate_targets(build_target_options(self.capability_cache, package_group=group))
            self._append_log(f"已分析包：{group.pkg_name} {group.pkg_version} [{', '.join(group.pkg_arches)}]")

        self._apply_submission_mode_ui()
        self._refresh_summary()
        self.workflow_tabs.setCurrentIndex(1)

    def _populate_matches(self, matches: tuple[StoreAppMatch, ...]) -> None:
        self.existing_app_detail = None
        self.match_combo.clear()
        for match in matches:
            label = f"{match.app_name or match.pkg_name} (app_id={match.app_id})"
            self.match_combo.addItem(label, match)
        current_mode = self.mode_combo.currentData()
        if current_mode not in {"new", "update"}:
            self.mode_combo.setCurrentIndex(self.mode_combo.findData("auto"))
        elif current_mode == "update" and not matches:
            self.mode_combo.setCurrentIndex(self.mode_combo.findData("auto"))
        self._update_match_controls()

    def _handle_match_changed(self) -> None:
        if self._is_batch_analysis():
            self.existing_app_detail = None
            self._apply_submission_mode_ui()
            return
        if self.mode_combo.currentData() not in {"auto", "update"}:
            self.existing_app_detail = None
            self._apply_submission_mode_ui()
            return
        data = self.match_combo.currentData()
        if data is None or self.login_context is None:
            self.existing_app_detail = None
            self._apply_submission_mode_ui()
            return
        self._run_worker(
            fetch_existing_app_detail,
            self.login_context.client,
            data,
            on_success=self._on_existing_detail_ready,
        )

    def _on_existing_detail_ready(self, detail: dict) -> None:
        self.existing_app_detail = detail
        detail_data = detail.get("datas") if isinstance(detail.get("datas"), dict) else detail
        lan_infos = detail_data.get("app_lan_infos") or []
        lan_info = lan_infos[0] if lan_infos else {}
        basic_info = detail_data.get("app_basic_info") or {}
        if self._effective_mode() == "update" or not self.app_name_edit.text().strip():
            self.app_name_edit.setText(str(lan_info.get("name", "")).strip())
        if self._effective_mode() == "update" or not self.short_desc_edit.text().strip():
            self.short_desc_edit.setText(str(lan_info.get("brief_info", "")).strip())
        if self._effective_mode() == "update" or not self.full_desc_edit.toPlainText().strip():
            self.full_desc_edit.setPlainText(str(lan_info.get("desc_info", "")).strip())
        if self._effective_mode() == "update" or not self.website_edit.text().strip():
            self.website_edit.setText(str(basic_info.get("website", "")).strip())
        category_value = basic_info.get("category_id")
        category_id = str(category_value).strip() if category_value not in (None, "") else ""
        if category_id and (self._effective_mode() == "update" or self._combo_text(self.category_combo) in {"", "1"}):
            if self.store_category_options:
                self._set_category_options(self.store_category_options, current_id=category_id)
            else:
                self._set_combo_value(self.category_combo, category_id)
        fit_info = detail_data.get("app_fit_info") or {}
        region_codes = tuple(
            str(item.get("code", "")).strip()
            for item in (fit_info.get("region") or [])
            if str(item.get("code", "")).strip()
        )
        if not region_codes:
            region_value = basic_info.get("region")
            region_codes = tuple(token.strip() for token in str(region_value or "").split(",") if token.strip())
        if region_codes:
            self._set_region_codes(region_codes)
        region = ",".join(region_codes)
        self._persist_preferences(category_id=category_id, region=region)
        self._append_log("已加载现有应用详情。")
        self._apply_submission_mode_ui()
        self._refresh_summary()

    def _update_match_controls(self) -> None:
        if self._is_batch_analysis():
            self.match_combo.setEnabled(False)
            self._apply_submission_mode_ui()
            self._refresh_summary()
            return
        is_update = self.mode_combo.currentData() in {"auto", "update"}
        self.match_combo.setEnabled(is_update)
        self._apply_submission_mode_ui()
        self._refresh_summary()

    def _handle_targets_item_changed(self, _item: QtWidgets.QTableWidgetItem) -> None:
        self._refresh_summary()

    def _baseline_display_name(self, baseline_id: str, baseline_options: tuple[tuple[str, str], ...]) -> str:
        for candidate_id, minor_version in baseline_options:
            if candidate_id == baseline_id:
                return minor_version or baseline_id
        return baseline_id

    def _baseline_summary_text(
        self,
        baseline_options: tuple[tuple[str, str], ...],
        selected_ids: tuple[str, ...],
    ) -> str:
        if not baseline_options:
            return "未返回具体版本"
        all_ids = tuple(baseline_id for baseline_id, _minor_version in baseline_options)
        selected = tuple(baseline_id for baseline_id in selected_ids if baseline_id in all_ids)
        if not selected:
            return "未选择基线"
        labels = [self._baseline_display_name(baseline_id, baseline_options) for baseline_id in selected]
        preview = "、".join(labels[:3])
        if len(labels) > 3:
            preview = f"{preview} 等 {len(labels)} 项"
        if len(selected) == len(all_ids):
            return f"全选：{preview}"
        return f"已选 {len(selected)} 项：{preview}"

    def _baseline_tooltip_text(
        self,
        baseline_options: tuple[tuple[str, str], ...],
        selected_ids: tuple[str, ...],
    ) -> str:
        if not baseline_options:
            return "当前商店能力缓存没有为这条系统线返回可选的具体版本。"
        selected = tuple(
            baseline_id
            for baseline_id in selected_ids
            if baseline_id in {candidate_id for candidate_id, _minor_version in baseline_options}
        )
        if not selected:
            return "当前系统线还未选择任何基线。"
        lines = [
            f"{self._baseline_display_name(baseline_id, baseline_options)} ({baseline_id})"
            for baseline_id in selected
        ]
        return "\n".join(lines)

    def _set_baseline_button_state(
        self,
        button: QtWidgets.QPushButton,
        baseline_options: tuple[tuple[str, str], ...],
        selected_ids: tuple[str, ...],
    ) -> None:
        all_ids = tuple(baseline_id for baseline_id, _minor_version in baseline_options)
        normalized: list[str] = []
        for baseline_id in selected_ids:
            if baseline_id in all_ids and baseline_id not in normalized:
                normalized.append(baseline_id)
        selected = tuple(normalized)
        button.setProperty("baseline_options", baseline_options)
        button.setProperty("selected_baseline_ids", selected)
        button.setText(self._baseline_summary_text(baseline_options, selected))
        button.setToolTip(self._baseline_tooltip_text(baseline_options, selected))
        button.setEnabled(bool(baseline_options))

    def _open_baseline_selector(self) -> None:
        button = self.sender()
        if not isinstance(button, QtWidgets.QPushButton):
            return
        baseline_options = tuple(button.property("baseline_options") or ())
        selected_ids = tuple(button.property("selected_baseline_ids") or ())
        if not baseline_options:
            return
        dialog = BaselineSelectionDialog(
            title="选择基线版本",
            baseline_options=baseline_options,
            selected_ids=selected_ids,
            parent=self,
        )
        result = dialog.open_selector()
        if result is None:
            return
        self._set_baseline_button_state(button, baseline_options, result)
        self._refresh_summary()

    def _set_all_target_rows_checked(self, checked: bool) -> None:
        target_state = CHECKED if checked else UNCHECKED
        for row in range(self.targets_table.rowCount()):
            item = self.targets_table.item(row, 0)
            if item is None:
                continue
            item.setCheckState(target_state)
        self._refresh_summary()

    def _set_all_target_baselines(self, select_all: bool) -> None:
        for row in range(self.targets_table.rowCount()):
            button = self.targets_table.cellWidget(row, 4)
            if not isinstance(button, QtWidgets.QPushButton):
                continue
            baseline_options = tuple(button.property("baseline_options") or ())
            if not baseline_options:
                continue
            selected_ids = tuple(baseline_id for baseline_id, _minor_version in baseline_options) if select_all else ()
            self._set_baseline_button_state(button, baseline_options, selected_ids)
        self._refresh_summary()

    def _populate_targets(self, options: tuple[SystemTargetOption, ...]) -> None:
        self.targets_table.setRowCount(0)
        for row, option in enumerate(options):
            self.targets_table.insertRow(row)

            check_item = QtWidgets.QTableWidgetItem()
            item_is_user_checkable = (
                QtCore.Qt.ItemFlag.ItemIsUserCheckable
                if hasattr(QtCore.Qt, "ItemFlag")
                else QtCore.Qt.ItemIsUserCheckable
            )
            item_is_enabled = (
                QtCore.Qt.ItemFlag.ItemIsEnabled
                if hasattr(QtCore.Qt, "ItemFlag")
                else QtCore.Qt.ItemIsEnabled
            )
            check_item.setFlags(
                check_item.flags()
                | item_is_user_checkable
                | item_is_enabled
            )
            check_item.setCheckState(CHECKED if option.selected else UNCHECKED)
            check_item.setData(USER_ROLE, option)
            self.targets_table.setItem(row, 0, check_item)

            package_item = QtWidgets.QTableWidgetItem(option.package_label)
            package_item.setToolTip(option.package_path)
            self.targets_table.setItem(row, 1, package_item)
            self.targets_table.setItem(row, 2, QtWidgets.QTableWidgetItem(option.package_arch))
            self.targets_table.setItem(row, 3, QtWidgets.QTableWidgetItem(f"{option.label} ({option.code})"))
            baseline_button = QtWidgets.QPushButton()
            baseline_button.clicked.connect(self._open_baseline_selector)
            selected_baseline_ids = option.selected_baseline_ids or ((option.baseline_id,) if option.baseline_id else ())
            self._set_baseline_button_state(baseline_button, option.baseline_options, selected_baseline_ids)
            self.targets_table.setCellWidget(row, 4, baseline_button)
        self._refresh_summary()

    def _collect_targets(self) -> tuple[SystemTargetOption, ...]:
        options: list[SystemTargetOption] = []
        for row in range(self.targets_table.rowCount()):
            item = self.targets_table.item(row, 0)
            baseline_widget = self.targets_table.cellWidget(row, 4)
            if item is None or baseline_widget is None:
                continue
            original = item.data(USER_ROLE)
            if not isinstance(original, SystemTargetOption):
                continue
            selected_baseline_ids: tuple[str, ...]
            baseline_id: str
            if isinstance(baseline_widget, QtWidgets.QPushButton):
                selected_baseline_ids = tuple(baseline_widget.property("selected_baseline_ids") or ())
                baseline_id = selected_baseline_ids[0] if selected_baseline_ids else ""
                baseline_options = tuple(baseline_widget.property("baseline_options") or original.baseline_options)
            else:
                selected_baseline_ids = (str(baseline_widget.currentData() or ""),) if str(baseline_widget.currentData() or "") else ()
                baseline_id = selected_baseline_ids[0] if selected_baseline_ids else ""
                baseline_options = tuple(
                    (baseline_widget.itemData(index), baseline_widget.itemText(index))
                    for index in range(baseline_widget.count())
                    if baseline_widget.itemData(index)
                )
            options.append(
                SystemTargetOption(
                    package_path=original.package_path,
                    package_label=original.package_label,
                    package_arch=original.package_arch,
                    code=original.code,
                    label=original.label,
                    package_family=original.package_family,
                    baseline_options=baseline_options,
                    selected=item.checkState() == CHECKED,
                    baseline_id=baseline_id,
                    selected_baseline_ids=selected_baseline_ids,
                    unsupported_baseline_ids=original.unsupported_baseline_ids,
                )
            )
        return tuple(options)

    def _handle_preprocess_assets(self) -> None:
        if self.analysis_state is None:
            QtWidgets.QMessageBox.warning(self, "未分析包", "请先分析包文件。")
            return
        if self._is_batch_analysis():
            QtWidgets.QMessageBox.information(
                self,
                "请在批量分组里操作",
                "批量模式下请打开“批量分组管理”，对每个分组单独执行素材预处理或自动截图。",
            )
            return
        self._start_preprocess_assets()

    def _start_preprocess_assets(self, on_success: Callable[[AssetBundle], None] | None = None) -> None:
        if self.analysis_state is None or self._is_batch_analysis():
            return
        self._run_worker(
            preprocess_submission_assets,
            self.analysis_state.package_group,
            asset_dir=self._current_asset_dir(),
            manual_icon_path=self._current_manual_icon(),
            manual_screenshot_paths=self._current_manual_screenshots(),
            output_dir=self._current_output_dir() / "preprocessed",
            on_success=on_success or self._on_assets_ready,
        )

    def _on_assets_ready(self, bundle: AssetBundle) -> None:
        self.asset_bundle = bundle
        self.icon_path_edit.setText(str(bundle.icon_path) if bundle.icon_path else "")
        self._set_icon_preview(bundle.icon_path)
        self.screenshot_list.clear()
        for path in bundle.screenshot_paths:
            self._append_screenshot(path)
        self._append_log("素材预处理完成。")
        self._refresh_summary()

    def _handle_capture_screenshots(self) -> None:
        if self.analysis_state is None:
            QtWidgets.QMessageBox.warning(self, "未分析包", "请先分析包文件。")
            return
        if self._is_batch_analysis():
            QtWidgets.QMessageBox.information(
                self,
                "请在批量分组里操作",
                "批量模式下请打开“批量分组管理”，对当前分组单独执行自动截图。",
            )
            return
        self._run_worker(
            capture_screenshots_for_group,
            self.analysis_state.package_group,
            output_dir=self._current_output_dir() / "capture",
            **self.capture_options_dialog.values(),
            on_success=self._on_capture_ready,
        )

    def _on_capture_ready(self, screenshots: tuple[Path, ...]) -> None:
        self.asset_bundle = None
        self.screenshot_list.clear()
        for path in screenshots:
            self._append_screenshot(path)
        if len(screenshots) < 3:
            self._append_log(f"自动截图仅保留 {len(screenshots)} 张有效截图，请补充到至少 3 张后再预处理或提交。")
            QtWidgets.QMessageBox.warning(
                self,
                "自动截图数量不足",
                (
                    f"当前只保留了 {len(screenshots)} 张有效截图。\n"
                    "这些截图已经加入列表。请再手动补充截图，或在已有应用更新时取消“替换图标/截图”。"
                ),
            )
        else:
            self._append_log("自动截图已写入列表，请继续执行预处理。")
        self._refresh_summary()
        self.workflow_tabs.setCurrentIndex(2)

    def _continue_submit_after_preprocess(self, bundle: AssetBundle) -> None:
        self._on_assets_ready(bundle)
        self._handle_submit()

    def _messagebox_yes(self):
        if hasattr(QtWidgets.QMessageBox, "StandardButton"):
            return QtWidgets.QMessageBox.StandardButton.Yes
        return QtWidgets.QMessageBox.Yes

    def _build_batch_submission_plans(self) -> tuple[BatchGroupSubmissionPlan, ...]:
        if self.analysis_state is None:
            return ()
        plans: list[BatchGroupSubmissionPlan] = []
        global_note = self.note_edit.text().strip()
        for entry in self.analysis_state.groups:
            package_group = entry.package_group
            group_key = self._group_key(package_group)
            config = self.batch_group_configs.get(group_key)
            if config is None:
                continue
            plans.append(
                BatchGroupSubmissionPlan(
                    package_group=package_group,
                    submission_mode=config.submission_mode,
                    selected_match=self._match_for_config(group_key, config),
                    app_name_zh=config.app_name_zh,
                    website=config.website,
                    short_desc_zh=config.short_desc_zh,
                    full_desc_zh=config.full_desc_zh,
                    category_id=config.category_id,
                    region_codes=config.region_codes,
                    asset_dir=(Path(config.asset_dir).expanduser().resolve() if config.asset_dir.strip() else self._current_asset_dir()),
                    replace_assets=config.replace_assets,
                    note_zh=config.note_zh.strip() or global_note,
                    app_name_en=config.app_name_en,
                    short_desc_en=config.short_desc_en,
                    full_desc_en=config.full_desc_en,
                    note_en=config.note_en,
                    auto_translate_en=config.auto_translate_en,
                    manual_screenshot_paths=tuple(Path(path).expanduser().resolve() for path in config.manual_screenshot_paths),
                    prepared_icon_path=(Path(config.prepared_icon_path).expanduser().resolve() if config.prepared_icon_path.strip() else None),
                    prepared_screenshot_paths=tuple(
                        Path(path).expanduser().resolve() for path in config.prepared_screenshot_paths
                    ),
                    asset_warnings=config.asset_warnings,
                    metadata_edited=config.metadata_edited,
                    manual_en_edited=config.manual_en_edited,
                )
            )
        return tuple(plans)

    def _handle_submit(self) -> None:
        if self.login_context is None:
            QtWidgets.QMessageBox.warning(self, "未登录", "请先登录。")
            return
        if self.analysis_state is None:
            QtWidgets.QMessageBox.warning(self, "未分析包", "请先分析包文件。")
            return
        if self.capability_cache is None:
            try:
                self.capability_cache = load_or_sync_capabilities(self.login_context.client)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "缺少能力缓存", str(exc))
                return
            if self.analysis_state.is_batch:
                self._populate_targets(build_target_options_for_groups(self.capability_cache, package_groups=self.analysis_state.package_groups))
            else:
                self._populate_targets(build_target_options(self.capability_cache, package_group=self.analysis_state.package_group))

        mode = self.mode_combo.currentData()
        selected_match = self._selected_match()
        effective_mode = self._effective_mode()
        replace_assets = self.replace_assets_checkbox.isChecked()
        if self._is_batch_analysis():
            self._persist_preferences(
                output_dir=str(self._current_output_dir()),
                asset_dir=self.asset_dir_edit.text().strip(),
                release_key=self.release_key_edit.text().strip() or "stable",
                pkg_channel=self.pkg_channel_edit.text().strip() or "stable",
            )
            plans = self._build_batch_submission_plans()
            if not plans:
                QtWidgets.QMessageBox.warning(self, "缺少批量计划", "当前没有可提交的批量分组。")
                return
            self._append_log("本次提交模式：批量分组提交")
            self._run_worker(
                submit_applications_batch,
                login=self.login_context,
                plans=plans,
                cache=self.capability_cache,
                note=self.note_edit.text().strip(),
                release_key=self.release_key_edit.text().strip(),
                pkg_channel=self.pkg_channel_edit.text().strip(),
                selected_targets=self._collect_targets(),
                output_dir=self._current_output_dir() / datetime.now().strftime("%Y%m%d-%H%M%S"),
                on_success=self._on_submit_ready,
            )
            return
        requires_assets = effective_mode == "new" or replace_assets
        if requires_assets and self.asset_bundle is None:
            reply = QtWidgets.QMessageBox.question(
                self,
                "尚未预处理素材",
                "还没有执行素材预处理。现在自动执行预处理并继续上传吗？",
            )
            if reply != self._messagebox_yes():
                return
            self._start_preprocess_assets(on_success=self._continue_submit_after_preprocess)
            return

        if mode == "update" and selected_match is None:
            QtWidgets.QMessageBox.warning(self, "未选择现有应用", "已有应用更新模式下需要选择商店里的目标应用。")
            return
        assets = self.asset_bundle or AssetBundle(
            icon_source=None,
            screenshot_sources=(),
            icon_path=None,
            screenshot_paths=(),
            validation_report=None,
            warnings=(),
        )
        common_submit_kwargs = dict(
            login=self.login_context,
            package_group=self.analysis_state.package_group,
            cache=self.capability_cache,
            note=self.note_edit.text().strip(),
            release_key=self.release_key_edit.text().strip(),
            pkg_channel=self.pkg_channel_edit.text().strip(),
            assets=assets,
            selected_targets=self._collect_targets(),
            output_dir=self._current_output_dir() / datetime.now().strftime("%Y%m%d-%H%M%S"),
        )
        if effective_mode == "new":
            try:
                category_id = self._current_category_id()
                region_codes = self._current_region_codes()
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "表单值无效", str(exc))
                return
            self._persist_preferences(
                category_id=str(category_id),
                region=",".join(region_codes),
                output_dir=str(self._current_output_dir()),
                asset_dir=self.asset_dir_edit.text().strip(),
                release_key=self.release_key_edit.text().strip() or "stable",
                pkg_channel=self.pkg_channel_edit.text().strip() or "stable",
            )
            submit_kwargs = dict(
                app_name_zh=self.app_name_edit.text().strip(),
                website=self.website_edit.text().strip(),
                short_desc_zh=self.short_desc_edit.text().strip(),
                full_desc_zh=self.full_desc_edit.toPlainText().strip(),
                keywords_zh="",
                category_id=category_id,
                region_codes=region_codes,
                **common_submit_kwargs,
            )
        else:
            try:
                category_id = self._current_category_id()
                current_region = self._current_region_text()
                region_codes = self._current_region_codes()
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "表单值无效", str(exc))
                return
            self._persist_preferences(
                category_id=str(category_id),
                region=current_region,
                output_dir=str(self._current_output_dir()),
                asset_dir=self.asset_dir_edit.text().strip(),
                release_key=self.release_key_edit.text().strip() or "stable",
                pkg_channel=self.pkg_channel_edit.text().strip() or "stable",
            )
            submit_kwargs = dict(
                app_name_zh=self.app_name_edit.text().strip(),
                website=self.website_edit.text().strip(),
                short_desc_zh=self.short_desc_edit.text().strip(),
                full_desc_zh=self.full_desc_edit.toPlainText().strip(),
                category_id=category_id,
                region_codes=region_codes,
                **common_submit_kwargs,
            )
        self._append_log(f"本次提交模式：{'已有应用更新' if effective_mode == 'update' else '新应用首提'}")
        if effective_mode == "update":
            self._run_worker(
                submit_existing_application,
                match=selected_match,
                replace_assets=replace_assets,
                on_success=self._on_submit_ready,
                **submit_kwargs,
            )
        else:
            self._run_worker(
                submit_new_application,
                on_success=self._on_submit_ready,
                **submit_kwargs,
            )

    def _on_submit_ready(self, result: SubmissionResult) -> None:
        self._append_log(f"上传完成，报告路径：{result.report_path}")
        QtWidgets.QMessageBox.information(
            self,
            "上传完成",
            f"上传任务完成。\n报告文件：{result.report_path}",
        )


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app._codex_qt_translators = install_qt_translations(app)
    window = MainWindow()
    window.show()
    if hasattr(app, "exec"):
        return app.exec()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
