import unittest
from unittest.mock import patch

from ui import native_file_dialog


class NativeFileDialogTests(unittest.TestCase):
    def test_open_file_names_prefers_qt_dialog_result(self) -> None:
        with patch("ui.native_file_dialog._build_dialog", return_value=object()):
            with patch(
                "ui.native_file_dialog._run_qt_dialog",
                return_value=(("/tmp/a.deb", "/tmp/b.deb"), "Packages (*.deb)"),
            ):
                selected, selected_filter = native_file_dialog.get_open_file_names(
                    None,
                    "选择包文件",
                    "",
                    "Packages (*.deb)",
                )

        self.assertEqual(selected, ("/tmp/a.deb", "/tmp/b.deb"))
        self.assertEqual(selected_filter, "Packages (*.deb)")

    def test_existing_directory_returns_first_selected_path(self) -> None:
        with patch("ui.native_file_dialog._build_dialog", return_value=object()):
            with patch("ui.native_file_dialog._run_qt_dialog", return_value=(("/tmp/out",), "")):
                selected = native_file_dialog.get_existing_directory(None, "选择输出目录")

        self.assertEqual(selected, "/tmp/out")

    def test_normalize_directory_uses_parent_for_file_path(self) -> None:
        normalized = native_file_dialog._normalize_directory("/tmp/file.txt")
        self.assertEqual(normalized, "/tmp")

    def test_parse_filter_spec(self) -> None:
        filters = native_file_dialog._parse_filter_spec("Packages (*.deb *.uab *.layer);;All Files (*)")
        self.assertEqual(
            filters,
            (
                {"label": "Packages", "patterns": ("*.deb", "*.uab", "*.layer")},
                {"label": "All Files", "patterns": ("*",)},
            ),
        )

    def test_preferred_locale_name_prefers_lang(self) -> None:
        with patch.dict("os.environ", {"LANG": "zh_CN.UTF-8"}, clear=True):
            self.assertEqual(native_file_dialog._preferred_locale_name(), "zh_CN")

    def test_translation_candidates_include_exact_locale(self) -> None:
        with patch.object(native_file_dialog.QtCore.QLibraryInfo, "path", return_value="/tmp/translations", create=True):
            candidates = native_file_dialog._translation_candidates("zh_CN")

        self.assertEqual(candidates[0], "/tmp/translations/qt_zh_CN.qm")

    def test_file_mode_value_uses_qt_enum(self) -> None:
        value = native_file_dialog._file_mode_value("Directory")
        if hasattr(native_file_dialog.QtWidgets.QFileDialog, "FileMode"):
            self.assertEqual(value, native_file_dialog.QtWidgets.QFileDialog.FileMode.Directory)
        else:
            self.assertEqual(value, native_file_dialog.QtWidgets.QFileDialog.Directory)
