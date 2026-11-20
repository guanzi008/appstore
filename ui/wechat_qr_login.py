from __future__ import annotations

import base64
from threading import Event, Thread

from appstore.session_state import BrowserSessionState
from ui.qt_compat import QtCore, QtGui, QtWidgets, Signal, exec_dialog
from ui.wechat_qr_backend import WechatQrCancelled, run_wechat_qr_login


class WechatQrLoginWorker(QtCore.QObject):
    qr_ready = Signal(bytes)
    status_changed = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, account_label: str) -> None:
        super().__init__()
        self.account_label = account_label
        self._cancelled = Event()

    def start(self) -> None:
        Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._cancelled.set()

    def _run(self) -> None:
        try:
            state = run_wechat_qr_login(
                self.account_label,
                event_callback=self._handle_event,
                is_cancelled=self._cancelled.is_set,
            )
        except WechatQrCancelled:
            pass
        except Exception as exc:
            if not self._cancelled.is_set():
                self.failed.emit(str(exc) or "扫码登录失败。")
        else:
            if not self._cancelled.is_set():
                self.succeeded.emit(state)
        finally:
            self.finished.emit()

    def _handle_event(self, payload: dict) -> None:
        if self._cancelled.is_set():
            return
        if payload.get("event") == "qr":
            self.qr_ready.emit(base64.b64decode(payload["image_base64"]))
        elif payload.get("event") == "status":
            self.status_changed.emit(payload["message"])


class WechatQrLoginDialog(QtWidgets.QDialog):
    def __init__(self, account_label: str, parent=None) -> None:
        super().__init__(parent)
        self.account_label = account_label.strip() or "manual-login"
        self.session_state: BrowserSessionState | None = None
        self._worker: WechatQrLoginWorker | None = None
        self._restart_after_stop = False
        self._closing = False

        self.setWindowTitle("微信扫码登录")
        self.resize(460, 620)

        self.title_label = QtWidgets.QLabel("请使用微信扫码登录统信账号")
        self.title_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.tip_label = QtWidgets.QLabel("扫码后，请在手机微信中确认登录。")
        self.tip_label.setWordWrap(True)
        self.tip_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )
        self.qr_label = QtWidgets.QLabel("正在加载二维码…")
        self.qr_label.setMinimumSize(360, 360)
        self.qr_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )
        self.qr_label.setFrameShape(
            QtWidgets.QFrame.Shape.Box if hasattr(QtWidgets.QFrame, "Shape") else QtWidgets.QFrame.Box
        )
        self.status_label = QtWidgets.QLabel("准备中。")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter if hasattr(QtCore.Qt, "AlignmentFlag") else QtCore.Qt.AlignCenter
        )
        self.refresh_button = QtWidgets.QPushButton("刷新二维码")
        self.cancel_button = QtWidgets.QPushButton("取消")

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.cancel_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.tip_label)
        layout.addWidget(self.qr_label, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(button_layout)

        self.refresh_button.clicked.connect(self._handle_refresh)
        self.cancel_button.clicked.connect(self.reject)

        self._start_worker()

    def open_and_capture(self) -> BrowserSessionState | None:
        if exec_dialog(self) == self._accepted_code():
            return self.session_state
        return None

    def _accepted_code(self) -> int:
        if hasattr(QtWidgets.QDialog, "DialogCode"):
            return QtWidgets.QDialog.DialogCode.Accepted
        return QtWidgets.QDialog.Accepted

    def _start_worker(self) -> None:
        self._restart_after_stop = False
        worker = WechatQrLoginWorker(self.account_label)
        worker.qr_ready.connect(self._on_qr_ready)
        worker.status_changed.connect(self._on_status_changed)
        worker.succeeded.connect(self._on_login_succeeded)
        worker.failed.connect(self._on_login_failed)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self.refresh_button.setEnabled(False)
        self.status_label.setText("正在加载二维码。")
        self.qr_label.setPixmap(QtGui.QPixmap())
        self.qr_label.setText("正在加载二维码…")
        worker.start()

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _handle_refresh(self) -> None:
        if self._worker is not None:
            self._restart_after_stop = True
            self._stop_worker()
            self.refresh_button.setEnabled(False)
            self.qr_label.setText("正在刷新二维码…")
            return
        self._start_worker()

    def _on_worker_finished(self) -> None:
        self._worker = None
        if self._restart_after_stop and not self._closing:
            self._start_worker()

    def _on_qr_ready(self, image_bytes: bytes) -> None:
        if self._closing or self._restart_after_stop:
            return
        self.refresh_button.setEnabled(True)
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(image_bytes)
        if pixmap.isNull():
            self.qr_label.setText("二维码加载失败")
            return
        self.qr_label.setText("")
        self.qr_label.setPixmap(
            pixmap.scaled(
                320,
                320,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio
                if hasattr(QtCore.Qt, "AspectRatioMode")
                else QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation
                if hasattr(QtCore.Qt, "TransformationMode")
                else QtCore.Qt.SmoothTransformation,
            )
        )

    def _on_status_changed(self, message: str) -> None:
        if not self._closing and not self._restart_after_stop:
            self.status_label.setText(message.strip() or "等待扫码。")

    def _on_login_succeeded(self, state: BrowserSessionState) -> None:
        if self._closing or self._restart_after_stop:
            return
        self.session_state = state
        self.accept()

    def _on_login_failed(self, error_text: str) -> None:
        if self._closing or self._restart_after_stop:
            return
        self.refresh_button.setEnabled(True)
        self.qr_label.setText("请刷新二维码")
        self.status_label.setText("二维码加载或扫码登录失败。")
        QtWidgets.QMessageBox.critical(self, "微信扫码登录失败", error_text)

    def reject(self) -> None:
        self._closing = True
        self._stop_worker()
        super().reject()

    def closeEvent(self, event) -> None:
        self._closing = True
        self._stop_worker()
        super().closeEvent(event)
