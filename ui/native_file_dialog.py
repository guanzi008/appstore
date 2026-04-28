from __future__ import annotations

import os

from ui.qt_compat import QtCore, QtWidgets, exec_dialog


def get_open_file_names(parent, title: str, directory: str = "", filter_spec: str = "All Files (*)") -> tuple[tuple[str, ...], str]:
    dialog = _build_dialog(
        parent,
        title=title,
        directory=directory,
        filter_spec=filter_spec,
        file_mode=_file_mode_value("ExistingFiles"),
    )
    files, selected_filter = _run_qt_dialog(dialog, allow_multiple=True, fallback_filter=filter_spec)
    return tuple(files), selected_filter


def get_open_file_name(parent, title: str, directory: str = "", filter_spec: str = "All Files (*)") -> tuple[str, str]:
    dialog = _build_dialog(
        parent,
        title=title,
        directory=directory,
        filter_spec=filter_spec,
        file_mode=_file_mode_value("ExistingFile"),
    )
    files, selected_filter = _run_qt_dialog(dialog, allow_multiple=False, fallback_filter=filter_spec)
    return (files[0] if files else ""), selected_filter


def get_existing_directory(parent, title: str, directory: str = "") -> str:
    dialog = _build_dialog(
        parent,
        title=title,
        directory=directory,
        filter_spec="",
        file_mode=_file_mode_value("Directory"),
        show_dirs_only=True,
    )
    files, _ = _run_qt_dialog(dialog, allow_multiple=False, fallback_filter="")
    return files[0] if files else ""


def install_qt_translations(app) -> tuple[QtCore.QTranslator, ...]:
    locale_name = _preferred_locale_name()
    if not locale_name.startswith("zh"):
        return ()
    translator = QtCore.QTranslator(app)
    for qm_path in _translation_candidates(locale_name):
        if translator.load(qm_path):
            app.installTranslator(translator)
            QtCore.QLocale.setDefault(QtCore.QLocale(locale_name))
            return (translator,)
    return ()


def _build_dialog(
    parent,
    *,
    title: str,
    directory: str,
    filter_spec: str,
    file_mode,
    show_dirs_only: bool = False,
):
    dialog = QtWidgets.QFileDialog(parent, title)
    if hasattr(QtWidgets.QFileDialog, "Option"):
        dialog.setOption(QtWidgets.QFileDialog.Option.DontUseNativeDialog, True)
        if show_dirs_only:
            dialog.setOption(QtWidgets.QFileDialog.Option.ShowDirsOnly, True)
    else:
        dialog.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, True)
        if show_dirs_only:
            dialog.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
    if hasattr(QtCore.Qt, "WindowModality"):
        dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
    else:
        dialog.setWindowModality(QtCore.Qt.WindowModal)
    if parent is not None:
        dialog.setModal(True)
    dialog.setFileMode(file_mode)
    if filter_spec:
        dialog.setNameFilters([chunk.strip() for chunk in filter_spec.split(";;") if chunk.strip()])
        default_filter = filter_spec.split(";;", 1)[0].strip()
        if default_filter:
            dialog.selectNameFilter(default_filter)
    normalized_directory = _normalize_directory(directory)
    if normalized_directory:
        dialog.setDirectory(normalized_directory)
    if hasattr(QtWidgets.QFileDialog, "ViewMode"):
        dialog.setViewMode(QtWidgets.QFileDialog.ViewMode.Detail)
    else:
        dialog.setViewMode(QtWidgets.QFileDialog.Detail)
    dialog.resize(1120, 720)
    dialog.setMinimumSize(980, 640)
    return dialog


def _run_qt_dialog(dialog, *, allow_multiple: bool, fallback_filter: str) -> tuple[tuple[str, ...], str]:
    result = exec_dialog(dialog)
    if hasattr(QtWidgets.QDialog, "DialogCode"):
        accepted = result == QtWidgets.QDialog.DialogCode.Accepted
    else:
        accepted = result == QtWidgets.QDialog.Accepted
    if not accepted:
        return (), fallback_filter
    selected_files = tuple(str(path) for path in dialog.selectedFiles() if str(path).strip())
    if not allow_multiple and selected_files:
        selected_files = (selected_files[0],)
    selected_filter = dialog.selectedNameFilter().strip() or fallback_filter
    return selected_files, selected_filter


def _parse_filter_spec(filter_spec: str) -> tuple[dict[str, tuple[str, ...]], ...]:
    filters: list[dict[str, tuple[str, ...]]] = []
    for chunk in filter_spec.split(";;"):
        part = chunk.strip()
        if not part:
            continue
        label = part
        patterns: tuple[str, ...] = ("*",)
        if "(" in part and part.endswith(")"):
            label, _, raw_patterns = part.partition("(")
            label = label.strip() or part
            pattern_text = raw_patterns[:-1].strip()
            parsed_patterns = tuple(piece for piece in pattern_text.split() if piece)
            if parsed_patterns:
                patterns = parsed_patterns
        filters.append({"label": label, "patterns": patterns})
    return tuple(filters)

def _normalize_directory(directory: str) -> str:
    current = str(directory or "").strip()
    if not current:
        return ""
    expanded = os.path.abspath(os.path.expanduser(current))
    if os.path.isdir(expanded):
        return expanded
    parent = os.path.dirname(expanded)
    return parent if parent and os.path.isdir(parent) else ""


def _preferred_locale_name() -> str:
    for key in ("LC_ALL", "LANGUAGE", "LANG"):
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        locale_name = value.split(":", 1)[0].split(".", 1)[0]
        if locale_name:
            return locale_name
    return QtCore.QLocale.system().name() or "zh_CN"


def _translation_candidates(locale_name: str) -> tuple[str, ...]:
    if hasattr(QtCore.QLibraryInfo, "path"):
        if hasattr(QtCore.QLibraryInfo, "LibraryPath"):
            base = QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.TranslationsPath)
        else:
            base = QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.TranslationsPath)
    else:
        base = QtCore.QLibraryInfo.location(QtCore.QLibraryInfo.TranslationsPath)
    language = locale_name.split("_", 1)[0]
    candidates = [os.path.join(base, f"qt_{locale_name}.qm")]
    if language and language != locale_name:
        candidates.append(os.path.join(base, f"qt_{language}.qm"))
    return tuple(candidates)


def _file_mode_value(name: str):
    if hasattr(QtWidgets.QFileDialog, "FileMode"):
        return getattr(QtWidgets.QFileDialog.FileMode, name)
    return getattr(QtWidgets.QFileDialog, name)
