from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appstore.translation import LANGUAGE_LABELS
from ui.assets import AssetBundle
from ui.backend import REGION_OPTIONS, StoreAppMatch, StoreCategoryOption
from ui.native_file_dialog import get_existing_directory
from ui.package_meta import PackageGroup
from ui.qt_compat import Signal, USER_ROLE, QtWidgets


@dataclass
class BatchGroupConfig:
    group_key: str
    submission_mode: str = "auto"
    selected_match_app_id: str = ""
    app_name_zh: str = ""
    website: str = ""
    short_desc_zh: str = ""
    full_desc_zh: str = ""
    category_id: str = "1"
    region_codes: tuple[str, ...] = ("1",)
    asset_dir: str = ""
    replace_assets: bool = False
    note_zh: str = ""
    app_name_en: str = ""
    short_desc_en: str = ""
    full_desc_en: str = ""
    note_en: str = ""
    auto_translate_en: bool = True
    manual_screenshot_paths: tuple[str, ...] = ()
    prepared_icon_path: str = ""
    prepared_screenshot_paths: tuple[str, ...] = ()
    asset_warnings: tuple[str, ...] = ()
    metadata_edited: bool = False
    editor_edited: bool = False
    manual_en_edited: bool = False


class CaptureOptionsDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自动截图参数")
        self.resize(760, 240)

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "这些参数只在自动识别启动方式或窗口失败时才需要填写。大多数应用保持默认即可。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QtWidgets.QGridLayout()
        self.sudo_password_edit = QtWidgets.QLineEdit()
        self.sudo_password_edit.setEchoMode(
            QtWidgets.QLineEdit.EchoMode.Password
            if hasattr(QtWidgets.QLineEdit, "EchoMode")
            else QtWidgets.QLineEdit.Password
        )
        self.sudo_password_edit.setPlaceholderText("deb 自动截图提权密码，可留空")
        self.launch_command_edit = QtWidgets.QLineEdit()
        self.launch_command_edit.setPlaceholderText("可选：自定义启动命令")
        self.desktop_file_edit = QtWidgets.QLineEdit()
        self.desktop_file_edit.setPlaceholderText("可选：指定 desktop 文件名或路径")
        self.window_name_edit = QtWidgets.QLineEdit()
        self.window_name_edit.setPlaceholderText("可选：指定窗口标题")
        self.window_class_edit = QtWidgets.QLineEdit()
        self.window_class_edit.setPlaceholderText("可选：指定窗口类名")

        form.addWidget(QtWidgets.QLabel("提权密码"), 0, 0)
        form.addWidget(self.sudo_password_edit, 0, 1)
        form.addWidget(QtWidgets.QLabel("启动命令"), 0, 2)
        form.addWidget(self.launch_command_edit, 0, 3)
        form.addWidget(QtWidgets.QLabel("desktop 文件"), 1, 0)
        form.addWidget(self.desktop_file_edit, 1, 1)
        form.addWidget(QtWidgets.QLabel("窗口标题"), 1, 2)
        form.addWidget(self.window_name_edit, 1, 3)
        form.addWidget(QtWidgets.QLabel("窗口类名"), 2, 0)
        form.addWidget(self.window_class_edit, 2, 1)
        form.setColumnStretch(1, 2)
        form.setColumnStretch(3, 2)
        layout.addLayout(form)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.reset_button = QtWidgets.QPushButton("恢复默认")
        self.reset_button.clicked.connect(self.reset_fields)
        button_row.addWidget(self.reset_button)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
            if hasattr(QtWidgets.QDialogButtonBox, "StandardButton")
            else QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        button_row.addWidget(buttons)
        layout.addLayout(button_row)

    def reset_fields(self) -> None:
        self.sudo_password_edit.clear()
        self.launch_command_edit.clear()
        self.desktop_file_edit.clear()
        self.window_name_edit.clear()
        self.window_class_edit.clear()

    def values(self) -> dict[str, str]:
        return {
            "sudo_password": self.sudo_password_edit.text(),
            "launch_command": self.launch_command_edit.text().strip(),
            "desktop_file": self.desktop_file_edit.text().strip(),
            "window_name": self.window_name_edit.text().strip(),
            "window_class": self.window_class_edit.text().strip(),
        }

    def summary_text(self) -> str:
        options = self.values()
        customized: list[str] = []
        if options["sudo_password"]:
            customized.append("已填提权密码")
        if options["launch_command"]:
            customized.append("自定义启动命令")
        if options["desktop_file"]:
            customized.append("指定 desktop 文件")
        if options["window_name"]:
            customized.append("指定窗口标题")
        if options["window_class"]:
            customized.append("指定窗口类名")
        if not customized:
            return "自动截图参数：默认（自动识别启动方式和窗口）"
        return f"自动截图参数：已自定义 {len(customized)} 项，" + "、".join(customized)

    def open_editor(self) -> bool:
        if hasattr(self, "exec"):
            result = self.exec()
        else:
            result = self.exec_()
        if hasattr(QtWidgets.QDialog, "DialogCode"):
            return result == QtWidgets.QDialog.DialogCode.Accepted
        return result == QtWidgets.QDialog.Accepted


class BatchGroupsDialog(QtWidgets.QDialog):
    state_changed = Signal()
    preprocess_requested = Signal(str)
    capture_requested = Signal(str)
    english_translation_requested = Signal(str)
    match_requested = Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量分组管理")
        self.resize(1260, 920)

        self._entries: tuple[object, ...] = ()
        self._configs: dict[str, BatchGroupConfig] = {}
        self._category_options: tuple[StoreCategoryOption, ...] = ()
        self._page_size = 6
        self._page_index = 0
        self._current_group_key = ""
        self._editor_updating = False
        self.region_checkboxes: dict[str, QtWidgets.QCheckBox] = {}

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "这里按分页管理本次批量提交流程。每组都可以单独选择“自动判断 / 批量更新 / 批量提新”，"
            "并维护匹配应用、分类、地区、多语言文案和素材目录。选择“其他地区”后会展开英文文案页，"
            "可直接填写，也可以按中文自动生成。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        summary_row = QtWidgets.QHBoxLayout()
        self.summary_label = QtWidgets.QLabel("当前没有可展示的批量分组。")
        self.summary_label.setWordWrap(True)
        summary_row.addWidget(self.summary_label, 1)
        self.prev_page_button = QtWidgets.QPushButton("上一页")
        self.prev_page_button.clicked.connect(self._go_prev_page)
        self.next_page_button = QtWidgets.QPushButton("下一页")
        self.next_page_button.clicked.connect(self._go_next_page)
        self.page_label = QtWidgets.QLabel("第 0 / 0 页")
        summary_row.addWidget(self.prev_page_button)
        summary_row.addWidget(self.page_label)
        summary_row.addWidget(self.next_page_button)
        layout.addLayout(summary_row)

        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["包名", "版本", "架构", "模式", "匹配状态", "素材", "目标应用"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
            if hasattr(QtWidgets.QAbstractItemView, "SelectionBehavior")
            else QtWidgets.QAbstractItemView.SelectRows
        )
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
            if hasattr(QtWidgets.QAbstractItemView, "EditTrigger")
            else QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.table.itemSelectionChanged.connect(self._handle_table_selection_changed)
        layout.addWidget(self.table, 2)

        self.editor_group = QtWidgets.QGroupBox("当前分组设置")
        editor_layout = QtWidgets.QGridLayout(self.editor_group)

        self.group_title_label = QtWidgets.QLabel("未选择分组")
        self.group_title_label.setWordWrap(True)
        editor_layout.addWidget(self.group_title_label, 0, 0, 1, 4)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("自动判断", "auto")
        self.mode_combo.addItem("批量更新", "update")
        self.mode_combo.addItem("批量提新", "new")
        self.mode_combo.currentIndexChanged.connect(self._handle_mode_changed)
        self.match_combo = QtWidgets.QComboBox()
        self.match_combo.currentIndexChanged.connect(self._handle_match_changed)
        self.asset_dir_edit = QtWidgets.QLineEdit()
        self.asset_dir_edit.textChanged.connect(self._save_current_editor_state)
        self.asset_dir_button = QtWidgets.QPushButton("选择素材目录")
        self.asset_dir_button.clicked.connect(self._choose_asset_directory)
        self.asset_status_label = QtWidgets.QLabel("-")
        self.asset_status_label.setWordWrap(True)
        self.replace_assets_checkbox = QtWidgets.QCheckBox("更新时替换图标/截图（新应用首提会自动要求素材）")
        self.replace_assets_checkbox.toggled.connect(self._save_current_editor_state)
        self.preprocess_assets_button = QtWidgets.QPushButton("检测并预处理当前分组素材")
        self.preprocess_assets_button.clicked.connect(self._request_preprocess_assets)
        self.capture_assets_button = QtWidgets.QPushButton("对当前分组自动截图")
        self.capture_assets_button.clicked.connect(self._request_capture_assets)

        self.category_combo = QtWidgets.QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.setInsertPolicy(
            QtWidgets.QComboBox.InsertPolicy.NoInsert
            if hasattr(QtWidgets.QComboBox, "InsertPolicy")
            else QtWidgets.QComboBox.NoInsert
        )
        category_line_edit = self.category_combo.lineEdit()
        if category_line_edit is not None:
            category_line_edit.setPlaceholderText("输入或选择分类 ID")
        self.category_combo.currentIndexChanged.connect(self._save_current_editor_state)
        self.category_combo.editTextChanged.connect(self._save_current_editor_state)
        self.app_name_edit = QtWidgets.QLineEdit()
        self.app_name_edit.textChanged.connect(self._save_current_editor_state)
        self.website_edit = QtWidgets.QLineEdit()
        self.website_edit.textChanged.connect(self._save_current_editor_state)
        self.short_desc_edit = QtWidgets.QLineEdit()
        self.short_desc_edit.textChanged.connect(self._save_current_editor_state)
        self.full_desc_edit = QtWidgets.QPlainTextEdit()
        self.full_desc_edit.setMinimumHeight(120)
        self.full_desc_edit.textChanged.connect(self._save_current_editor_state)
        self.note_zh_edit = QtWidgets.QPlainTextEdit()
        self.note_zh_edit.setMaximumHeight(80)
        self.note_zh_edit.textChanged.connect(self._save_current_editor_state)

        self.app_name_en_edit = QtWidgets.QLineEdit()
        self.app_name_en_edit.textChanged.connect(self._save_current_editor_state)
        self.short_desc_en_edit = QtWidgets.QLineEdit()
        self.short_desc_en_edit.textChanged.connect(self._save_current_editor_state)
        self.full_desc_en_edit = QtWidgets.QPlainTextEdit()
        self.full_desc_en_edit.setMinimumHeight(120)
        self.full_desc_en_edit.textChanged.connect(self._save_current_editor_state)
        self.note_en_edit = QtWidgets.QPlainTextEdit()
        self.note_en_edit.setMaximumHeight(80)
        self.note_en_edit.textChanged.connect(self._save_current_editor_state)
        self.auto_translate_checkbox = QtWidgets.QCheckBox("英文缺失时自动生成")
        self.auto_translate_checkbox.setChecked(True)
        self.auto_translate_checkbox.toggled.connect(self._save_current_editor_state)
        self.generate_english_button = QtWidgets.QPushButton("根据中文生成英文")
        self.generate_english_button.clicked.connect(self._request_generate_english)
        self.english_hint_label = QtWidgets.QLabel("仅在勾选“其他地区”时提交英文文案。")
        self.english_hint_label.setWordWrap(True)

        self.localized_tabs = QtWidgets.QTabWidget()
        self.localized_tabs.addTab(self._build_language_page("zh_CN"), LANGUAGE_LABELS.get("zh_CN", "中文"))
        self.localized_tabs.addTab(self._build_language_page("en_US"), LANGUAGE_LABELS.get("en_US", "英文"))

        self.region_widget = QtWidgets.QWidget()
        region_layout = QtWidgets.QHBoxLayout(self.region_widget)
        region_layout.setContentsMargins(0, 0, 0, 0)
        region_layout.setSpacing(12)
        for code, label in REGION_OPTIONS:
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.toggled.connect(self._save_current_editor_state)
            self.region_checkboxes[code] = checkbox
            region_layout.addWidget(checkbox)
        region_layout.addStretch(1)

        editor_layout.addWidget(QtWidgets.QLabel("提交流程"), 1, 0)
        editor_layout.addWidget(self.mode_combo, 1, 1)
        editor_layout.addWidget(QtWidgets.QLabel("匹配到的商店应用"), 1, 2)
        editor_layout.addWidget(self.match_combo, 1, 3)
        editor_layout.addWidget(QtWidgets.QLabel("素材目录"), 2, 0)
        editor_layout.addWidget(self.asset_dir_edit, 2, 1, 1, 2)
        editor_layout.addWidget(self.asset_dir_button, 2, 3)
        editor_layout.addWidget(QtWidgets.QLabel("素材状态"), 3, 0)
        editor_layout.addWidget(self.asset_status_label, 3, 1, 1, 3)
        editor_layout.addWidget(self.replace_assets_checkbox, 4, 1, 1, 3)
        editor_layout.addWidget(self.preprocess_assets_button, 5, 1, 1, 2)
        editor_layout.addWidget(self.capture_assets_button, 5, 3)
        editor_layout.addWidget(QtWidgets.QLabel("分类 ID"), 6, 0)
        editor_layout.addWidget(self.category_combo, 6, 1)
        editor_layout.addWidget(QtWidgets.QLabel("官网"), 6, 2)
        editor_layout.addWidget(self.website_edit, 6, 3)
        editor_layout.addWidget(QtWidgets.QLabel("地区"), 7, 0)
        editor_layout.addWidget(self.region_widget, 7, 1, 1, 3)
        editor_layout.addWidget(self.localized_tabs, 8, 0, 1, 4)
        editor_layout.setColumnStretch(1, 2)
        editor_layout.setColumnStretch(3, 2)
        layout.addWidget(self.editor_group, 3)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        close_button = QtWidgets.QPushButton("完成")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def set_state(
        self,
        *,
        entries: tuple[object, ...],
        configs: dict[str, BatchGroupConfig],
        category_options: tuple[StoreCategoryOption, ...],
    ) -> None:
        self._entries = entries
        self._configs = configs
        self._category_options = category_options
        self._page_index = 0
        self._current_group_key = ""
        self._render_page()

    def refresh_view(self) -> None:
        self._render_page()

    def set_busy(self, busy: bool) -> None:
        for widget in (
            self.prev_page_button,
            self.next_page_button,
            self.table,
            self.mode_combo,
            self.match_combo,
            self.asset_dir_edit,
            self.asset_dir_button,
            self.replace_assets_checkbox,
            self.preprocess_assets_button,
            self.capture_assets_button,
            self.category_combo,
            self.website_edit,
            self.region_widget,
            self.localized_tabs,
            self.generate_english_button,
            self.auto_translate_checkbox,
        ):
            widget.setEnabled(not busy)

    def open_dialog(self) -> None:
        if hasattr(self, "exec"):
            self.exec()
        else:
            self.exec_()

    def _build_language_page(self, lan: str) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(page)
        if lan == "zh_CN":
            layout.addWidget(QtWidgets.QLabel("应用名"), 0, 0)
            layout.addWidget(self.app_name_edit, 0, 1)
            layout.addWidget(QtWidgets.QLabel("一句话简介"), 1, 0)
            layout.addWidget(self.short_desc_edit, 1, 1)
            layout.addWidget(QtWidgets.QLabel("更新说明"), 2, 0)
            layout.addWidget(self.note_zh_edit, 2, 1)
            layout.addWidget(QtWidgets.QLabel("详细描述"), 3, 0)
            layout.addWidget(self.full_desc_edit, 3, 1)
        else:
            control_row = QtWidgets.QHBoxLayout()
            control_row.addWidget(self.auto_translate_checkbox)
            control_row.addStretch(1)
            control_row.addWidget(self.generate_english_button)
            english_header = QtWidgets.QWidget()
            english_header.setLayout(control_row)
            layout.addWidget(self.english_hint_label, 0, 0, 1, 2)
            layout.addWidget(english_header, 1, 0, 1, 2)
            layout.addWidget(QtWidgets.QLabel("英文应用名"), 2, 0)
            layout.addWidget(self.app_name_en_edit, 2, 1)
            layout.addWidget(QtWidgets.QLabel("英文一句话简介"), 3, 0)
            layout.addWidget(self.short_desc_en_edit, 3, 1)
            layout.addWidget(QtWidgets.QLabel("英文更新说明"), 4, 0)
            layout.addWidget(self.note_en_edit, 4, 1)
            layout.addWidget(QtWidgets.QLabel("英文详细描述"), 5, 0)
            layout.addWidget(self.full_desc_en_edit, 5, 1)
        layout.setColumnStretch(1, 1)
        return page

    def _current_page_entries(self) -> tuple[object, ...]:
        start = self._page_index * self._page_size
        end = start + self._page_size
        return self._entries[start:end]

    def _page_count(self) -> int:
        if not self._entries:
            return 0
        return (len(self._entries) + self._page_size - 1) // self._page_size

    def _go_prev_page(self) -> None:
        if self._page_index <= 0:
            return
        self._page_index -= 1
        self._render_page()

    def _go_next_page(self) -> None:
        if self._page_index + 1 >= self._page_count():
            return
        self._page_index += 1
        self._render_page()

    def _render_page(self) -> None:
        self.table.setRowCount(0)
        page_entries = self._current_page_entries()
        for row, entry in enumerate(page_entries):
            package_group = getattr(entry, "package_group", None)
            if not isinstance(package_group, PackageGroup):
                continue
            config = self._config_for_group(package_group)
            self.table.insertRow(row)

            pkg_item = QtWidgets.QTableWidgetItem(package_group.pkg_name)
            pkg_item.setData(USER_ROLE, self._group_key(package_group))
            self.table.setItem(row, 0, pkg_item)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(package_group.pkg_version))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(", ".join(package_group.pkg_arches)))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(self._mode_summary(entry, config)))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(self._match_status(entry, config)))
            self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(self._asset_status(entry, config)))
            self.table.setItem(row, 6, QtWidgets.QTableWidgetItem(self._target_app_text(entry, config)))

        page_count = self._page_count()
        self.page_label.setText(f"第 {self._page_index + 1 if page_count else 0} / {page_count} 页")
        self.prev_page_button.setEnabled(self._page_index > 0)
        self.next_page_button.setEnabled(self._page_index + 1 < page_count)
        self.summary_label.setText(self._summary_text())

        if self.table.rowCount():
            preferred_row = 0
            if self._current_group_key:
                for row in range(self.table.rowCount()):
                    item = self.table.item(row, 0)
                    if item is not None and item.data(USER_ROLE) == self._current_group_key:
                        preferred_row = row
                        break
            self.table.selectRow(preferred_row)
        else:
            self._current_group_key = ""
            self._clear_editor()

    def _summary_text(self) -> str:
        if not self._entries:
            return "当前没有可展示的批量分组。"
        auto_count = 0
        update_count = 0
        new_count = 0
        for entry in self._entries:
            package_group = getattr(entry, "package_group", None)
            if not isinstance(package_group, PackageGroup):
                continue
            config = self._config_for_group(package_group)
            if config.submission_mode == "update":
                update_count += 1
            elif config.submission_mode == "new":
                new_count += 1
            else:
                auto_count += 1
        return (
            f"共 {len(self._entries)} 组；自动判断 {auto_count} 组；批量更新 {update_count} 组；"
            f"批量提新 {new_count} 组。"
        )

    def _handle_table_selection_changed(self) -> None:
        group_key = self._selected_group_key()
        if not group_key:
            self._clear_editor()
            return
        self._current_group_key = group_key
        entry = self._entry_by_group_key(group_key)
        if entry is None:
            self._clear_editor()
            return
        self._load_editor(entry)
        self._request_match_detail()

    def _selected_group_key(self) -> str:
        current_row = self.table.currentRow()
        if current_row < 0:
            return ""
        item = self.table.item(current_row, 0)
        if item is None:
            return ""
        return str(item.data(USER_ROLE) or "").strip()

    def _entry_by_group_key(self, group_key: str):
        for entry in self._entries:
            package_group = getattr(entry, "package_group", None)
            if isinstance(package_group, PackageGroup) and self._group_key(package_group) == group_key:
                return entry
        return None

    def _config_for_group(self, package_group: PackageGroup) -> BatchGroupConfig:
        return self._configs[self._group_key(package_group)]

    def _group_key(self, package_group: PackageGroup) -> str:
        return "|".join(
            (
                package_group.pkg_name,
                package_group.pkg_version,
                package_group.package_family,
                package_group.package_format,
            )
        )

    def _load_editor(self, entry) -> None:
        package_group = getattr(entry, "package_group", None)
        if not isinstance(package_group, PackageGroup):
            self._clear_editor()
            return
        config = self._config_for_group(package_group)
        self._editor_updating = True
        try:
            self.group_title_label.setText(
                f"{package_group.pkg_name} {package_group.pkg_version} | 架构：{', '.join(package_group.pkg_arches)}"
            )
            self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(config.submission_mode)))
            self.match_combo.clear()
            self.match_combo.addItem("不指定现有应用", "")
            existing_matches = tuple(getattr(entry, "existing_matches", ()) or ())
            for match in existing_matches:
                label = f"{match.app_name or match.pkg_name} (app_id={match.app_id})"
                self.match_combo.addItem(label, match.app_id)
            match_index = self.match_combo.findData(config.selected_match_app_id)
            self.match_combo.setCurrentIndex(match_index if match_index >= 0 else 0)
            self.asset_dir_edit.setText(config.asset_dir)
            self.asset_status_label.setText(self._asset_status(entry, config))
            self.replace_assets_checkbox.setChecked(config.replace_assets)
            self._set_category_options(current_id=config.category_id)
            self.app_name_edit.setText(config.app_name_zh)
            self.website_edit.setText(config.website)
            self.short_desc_edit.setText(config.short_desc_zh)
            self.full_desc_edit.setPlainText(config.full_desc_zh)
            self.note_zh_edit.setPlainText(config.note_zh)
            self.app_name_en_edit.setText(config.app_name_en)
            self.short_desc_en_edit.setText(config.short_desc_en)
            self.full_desc_en_edit.setPlainText(config.full_desc_en)
            self.note_en_edit.setPlainText(config.note_en)
            self.auto_translate_checkbox.setChecked(config.auto_translate_en)
            self._set_region_codes(config.region_codes)
            self._apply_editor_mode_ui()
        finally:
            self._editor_updating = False

    def _clear_editor(self) -> None:
        self._editor_updating = True
        try:
            self.group_title_label.setText("未选择分组")
            self.mode_combo.setCurrentIndex(0)
            self.match_combo.clear()
            self.asset_dir_edit.clear()
            self.asset_status_label.setText("-")
            self.replace_assets_checkbox.setChecked(False)
            self._set_category_options(current_id="1")
            self.app_name_edit.clear()
            self.website_edit.clear()
            self.short_desc_edit.clear()
            self.full_desc_edit.clear()
            self.note_zh_edit.clear()
            self.app_name_en_edit.clear()
            self.short_desc_en_edit.clear()
            self.full_desc_en_edit.clear()
            self.note_en_edit.clear()
            self.auto_translate_checkbox.setChecked(True)
            self._set_region_codes(("1",))
            self._apply_editor_mode_ui()
        finally:
            self._editor_updating = False

    def _set_category_options(self, *, current_id: str) -> None:
        current_text = current_id.strip() or "1"
        block_state = self.category_combo.blockSignals(True)
        self.category_combo.clear()
        for option in self._category_options:
            if option.category_id == "0":
                continue
            self.category_combo.addItem(f"{option.name} ({option.category_id})", option.category_id)
        index = self.category_combo.findData(current_text)
        if index >= 0:
            self.category_combo.setCurrentIndex(index)
        else:
            self.category_combo.setEditText(current_text)
        self.category_combo.blockSignals(block_state)

    def _set_region_codes(self, region_codes: tuple[str, ...]) -> None:
        normalized = set(region_codes or ("1",))
        for code, checkbox in self.region_checkboxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(code in normalized)
            checkbox.blockSignals(False)

    def _current_region_codes(self) -> tuple[str, ...]:
        result = tuple(code for code, checkbox in self.region_checkboxes.items() if checkbox.isChecked())
        return result or ("1",)

    def _current_category_text(self) -> str:
        current_data = self.category_combo.currentData()
        if current_data:
            return str(current_data).strip()
        return self.category_combo.currentText().strip() or "1"

    def _save_current_editor_state(self) -> None:
        if self._editor_updating or not self._current_group_key:
            return
        config = self._configs.get(self._current_group_key)
        if config is None:
            return
        previous_metadata = (
            config.app_name_zh,
            config.website,
            config.short_desc_zh,
            config.full_desc_zh,
            config.category_id,
            config.region_codes,
        )
        previous_editor_content = (
            config.app_name_zh,
            config.website,
            config.short_desc_zh,
            config.full_desc_zh,
            config.category_id,
            config.region_codes,
            config.note_zh,
            config.app_name_en,
            config.short_desc_en,
            config.full_desc_en,
            config.note_en,
        )
        previous_english = (
            config.app_name_en,
            config.short_desc_en,
            config.full_desc_en,
            config.note_en,
        )
        previous_asset_dir = config.asset_dir
        config.submission_mode = str(self.mode_combo.currentData() or "auto")
        config.selected_match_app_id = str(self.match_combo.currentData() or "").strip()
        config.asset_dir = self.asset_dir_edit.text().strip()
        config.replace_assets = self.replace_assets_checkbox.isChecked()
        config.app_name_zh = self.app_name_edit.text().strip()
        config.website = self.website_edit.text().strip()
        config.short_desc_zh = self.short_desc_edit.text().strip()
        config.full_desc_zh = self.full_desc_edit.toPlainText().strip()
        config.note_zh = self.note_zh_edit.toPlainText().strip()
        config.app_name_en = self.app_name_en_edit.text().strip()
        config.short_desc_en = self.short_desc_en_edit.text().strip()
        config.full_desc_en = self.full_desc_en_edit.toPlainText().strip()
        config.note_en = self.note_en_edit.toPlainText().strip()
        config.auto_translate_en = self.auto_translate_checkbox.isChecked()
        config.category_id = self._current_category_text()
        config.region_codes = self._current_region_codes()
        if config.asset_dir != previous_asset_dir:
            config.prepared_icon_path = ""
            config.prepared_screenshot_paths = ()
            config.asset_warnings = ()
        current_metadata = (
            config.app_name_zh,
            config.website,
            config.short_desc_zh,
            config.full_desc_zh,
            config.category_id,
            config.region_codes,
        )
        if current_metadata != previous_metadata:
            config.metadata_edited = True
        current_editor_content = (
            config.app_name_zh,
            config.website,
            config.short_desc_zh,
            config.full_desc_zh,
            config.category_id,
            config.region_codes,
            config.note_zh,
            config.app_name_en,
            config.short_desc_en,
            config.full_desc_en,
            config.note_en,
        )
        if current_editor_content != previous_editor_content:
            config.editor_edited = True
        current_english = (
            config.app_name_en,
            config.short_desc_en,
            config.full_desc_en,
            config.note_en,
        )
        if current_english != previous_english:
            config.manual_en_edited = True
        self._apply_editor_mode_ui()
        self._refresh_current_row()
        entry = self._entry_by_group_key(self._current_group_key)
        if entry is not None:
            self.asset_status_label.setText(self._asset_status(entry, config))
        self.summary_label.setText(self._summary_text())
        self.state_changed.emit()

    def _handle_match_changed(self) -> None:
        self._save_current_editor_state()
        self._request_match_detail()

    def _handle_mode_changed(self) -> None:
        self._save_current_editor_state()
        self._request_match_detail()

    def _apply_editor_mode_ui(self) -> None:
        mode = str(self.mode_combo.currentData() or "auto")
        update_enabled = mode in {"auto", "update"}
        self.match_combo.setEnabled(update_enabled)
        self.asset_dir_edit.setEnabled(True)
        self.asset_dir_button.setEnabled(True)
        other_region_enabled = "2" in self._current_region_codes()
        english_tab_index = 1
        self.localized_tabs.setTabEnabled(english_tab_index, other_region_enabled)
        for widget in (
            self.app_name_en_edit,
            self.short_desc_en_edit,
            self.full_desc_en_edit,
            self.note_en_edit,
            self.auto_translate_checkbox,
            self.generate_english_button,
            self.english_hint_label,
        ):
            widget.setEnabled(other_region_enabled)
        if not other_region_enabled and self.localized_tabs.currentIndex() == 1:
            self.localized_tabs.setCurrentIndex(0)

    def _choose_asset_directory(self) -> None:
        directory = get_existing_directory(self, "选择当前分组素材目录")
        if not directory:
            return
        self.asset_dir_edit.setText(directory)

    def _request_preprocess_assets(self) -> None:
        if self._current_group_key:
            self.preprocess_requested.emit(self._current_group_key)

    def _request_capture_assets(self) -> None:
        if self._current_group_key:
            self.capture_requested.emit(self._current_group_key)

    def _request_generate_english(self) -> None:
        if self._current_group_key:
            self.english_translation_requested.emit(self._current_group_key)

    def _request_match_detail(self) -> None:
        if self._editor_updating or not self._current_group_key:
            return
        if str(self.mode_combo.currentData() or "auto") == "new":
            return
        self.match_requested.emit(self._current_group_key)

    def _refresh_current_row(self) -> None:
        current_row = self.table.currentRow()
        if current_row < 0:
            return
        entry = self._entry_by_group_key(self._current_group_key)
        if entry is None:
            return
        package_group = getattr(entry, "package_group", None)
        if not isinstance(package_group, PackageGroup):
            return
        config = self._config_for_group(package_group)
        self.table.setItem(current_row, 3, QtWidgets.QTableWidgetItem(self._mode_summary(entry, config)))
        self.table.setItem(current_row, 4, QtWidgets.QTableWidgetItem(self._match_status(entry, config)))
        self.table.setItem(current_row, 5, QtWidgets.QTableWidgetItem(self._asset_status(entry, config)))
        self.table.setItem(current_row, 6, QtWidgets.QTableWidgetItem(self._target_app_text(entry, config)))

    def _mode_summary(self, entry, config: BatchGroupConfig) -> str:
        if config.submission_mode == "update":
            return "批量更新"
        if config.submission_mode == "new":
            return "批量提新"
        return "自动判断"

    def _match_status(self, entry, config: BatchGroupConfig) -> str:
        existing_matches = tuple(getattr(entry, "existing_matches", ()) or ())
        if config.selected_match_app_id:
            return "已指定现有应用"
        if len(existing_matches) == 1:
            return "唯一匹配"
        if not existing_matches:
            return "未找到"
        return f"匹配冲突（{len(existing_matches)}）"

    def _target_app_text(self, entry, config: BatchGroupConfig) -> str:
        existing_matches = tuple(getattr(entry, "existing_matches", ()) or ())
        for match in existing_matches:
            if match.app_id == config.selected_match_app_id:
                return f"{match.app_name or match.pkg_name} (app_id={match.app_id})"
        if config.submission_mode == "new":
            return "新应用首提"
        if config.submission_mode == "auto" and not config.selected_match_app_id:
            return "自动判断"
        return "未指定"

    def _asset_status(self, entry, config: BatchGroupConfig) -> str:
        if config.prepared_icon_path or config.prepared_screenshot_paths:
            parts = [
                "已预处理",
                "有图标" if config.prepared_icon_path else "无图标",
                f"{len(config.prepared_screenshot_paths)} 张有效截图",
            ]
            if config.asset_warnings:
                parts.append(f"{len(config.asset_warnings)} 条警告")
            return "；".join(parts)
        if config.manual_screenshot_paths:
            parts = [f"已自动截图 {len(config.manual_screenshot_paths)} 张", "待执行预处理"]
            if config.asset_dir:
                parts.append("已指定素材目录")
            return "；".join(parts)
        icon_path = getattr(entry, "icon_path", None)
        screenshot_paths = tuple(getattr(entry, "screenshot_paths", ()) or ())
        parts = [
            "检测到图标" if icon_path is not None else "未检测到图标",
            f"{len(screenshot_paths)} 张候选截图",
        ]
        if config.asset_dir:
            parts.append("已指定素材目录")
        if config.replace_assets:
            parts.append("提交时替换素材")
        return "；".join(parts)
