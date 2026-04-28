from __future__ import annotations

QT_BINDING = ""
QtNetwork = None
QtWebEngineCore = None
QtWebEngineWidgets = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    try:
        from PySide6 import QtNetwork, QtWebEngineCore, QtWebEngineWidgets
    except ImportError:
        QtNetwork = None
        QtWebEngineCore = None
        QtWebEngineWidgets = None

    QT_BINDING = "PySide6"
    Signal = QtCore.Signal
    Slot = QtCore.Slot
except ImportError:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
        try:
            from PyQt6 import QtNetwork, QtWebEngineCore, QtWebEngineWidgets
        except ImportError:
            QtNetwork = None
            QtWebEngineCore = None
            QtWebEngineWidgets = None

        QT_BINDING = "PyQt6"
        Signal = QtCore.pyqtSignal
        Slot = QtCore.pyqtSlot
    except ImportError:
        try:
            from PyQt5 import QtCore, QtGui, QtWidgets
            try:
                from PyQt5 import QtNetwork, QtWebEngineCore, QtWebEngineWidgets
            except ImportError:
                QtNetwork = None
                QtWebEngineCore = None
                QtWebEngineWidgets = None

            QT_BINDING = "PyQt5"
            Signal = QtCore.pyqtSignal
            Slot = QtCore.pyqtSlot
        except ImportError as exc:
            raise RuntimeError(
                "Qt bindings not found. Install PySide6, PyQt6, or PyQt5 before launching the UI."
            ) from exc


Qt = QtCore.Qt

if hasattr(Qt, "AspectRatioMode"):
    KEEP_ASPECT_RATIO = Qt.AspectRatioMode.KeepAspectRatio
    SMOOTH_TRANSFORMATION = Qt.TransformationMode.SmoothTransformation
    USER_ROLE = Qt.ItemDataRole.UserRole
    CHECKED = Qt.CheckState.Checked
    UNCHECKED = Qt.CheckState.Unchecked
else:
    KEEP_ASPECT_RATIO = Qt.KeepAspectRatio
    SMOOTH_TRANSFORMATION = Qt.SmoothTransformation
    USER_ROLE = Qt.UserRole
    CHECKED = Qt.Checked
    UNCHECKED = Qt.Unchecked


def exec_dialog(dialog) -> int:
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()
