# -*- coding: utf-8 -*-
"""시험 PDF를 고르고 주석 PDF를 받는 창."""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QIconEngine, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gemini import redact
from job import process_pdfs, save_download
from models import list_available
from settings import ROOT_DIR, load, load_key, read_cached_model, write_cached_model


STYLE = """
QWidget {
  background: #0c1014;
  color: #d5ece4;
  font-family: "Malgun Gothic";
  font-size: 12px;
}
QLabel {
  background: transparent;
  color: #d5ece4;
}
QLabel#model {
  color: #8eb8ad;
  font-size: 12px;
}
QLabel#pane {
  color: #d5ece4;
  font-size: 12px;
  padding: 0;
  margin: 0;
}
QPushButton {
  background: #0c1014;
  color: #d5ece4;
  border: 1px solid #2a3a36;
  padding: 3px 8px;
}
QPushButton:hover {
  background: #15201c;
  border-color: #3d5c54;
}
QPushButton:disabled {
  color: #5a6460;
  background: #0c1014;
  border: 1px solid #0c1014;
}
QPushButton#run {
  background: #e09a32;
  color: #1a1206;
  border: 1px solid #e09a32;
}
QPushButton#run:hover {
  background: #f0b04a;
  color: #1a1206;
  border-color: #f0b04a;
}
QPushButton#run:disabled {
  background: #0c1014;
  color: #5a6460;
  border: 1px solid #0c1014;
}
QListWidget, QPlainTextEdit {
  background: #090d10;
  color: #d5ece4;
  border: 1px solid #24312d;
  font-family: Consolas, "Malgun Gothic";
  selection-background-color: #16332c;
  selection-color: #d5ece4;
  padding: 2px;
}
QListWidget::item {
  padding: 2px 4px;
}
QListWidget::item:selected,
QListWidget::item:selected:active,
QListWidget::item:selected:!active {
  background: #16332c;
  color: #d5ece4;
}
QDialog, QMessageBox {
  background: #0c1014;
}
"""

ICON_DIR = Path(__file__).resolve().parent / "icons"
ICON_PX = 14


class _SvgIconEngine(QIconEngine):
    """SVG를 그릴 때마다 요청 크기에 맞춘다."""

    def __init__(self, normal, disabled):
        super().__init__()
        self._normal = normal
        self._disabled = disabled
        self._renderers = {}

    def clone(self):
        return _SvgIconEngine(self._normal, self._disabled)

    def _renderer(self, mode):
        disabled = mode == QIcon.Disabled
        key = "disabled" if disabled else "normal"
        renderer = self._renderers.get(key)
        if renderer is None:
            renderer = QSvgRenderer(self._disabled if disabled else self._normal)
            self._renderers[key] = renderer
        return renderer

    def paint(self, painter, rect, mode, state):
        renderer = self._renderer(mode)
        if not renderer.isValid():
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(painter, QRectF(rect))
        painter.restore()

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(max(1, size.width()), max(1, size.height()))
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap


def make_icon(name, color):
    source = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    normal = source.replace("#d5ece4", color).encode("utf-8")
    disabled = source.replace("#d5ece4", "#3e4c48").encode("utf-8")
    return QIcon(_SvgIconEngine(normal, disabled))


def window_icon():
    renderer = QSvgRenderer(str(ICON_DIR / "app.svg"))
    icon = QIcon()
    for side in (16, 32, 48, 256):
        pixmap = QPixmap(side, side)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def attach_icon(button, name, color="#d5ece4"):
    button.setIcon(make_icon(name, color))
    button.setIconSize(QSize(ICON_PX, ICON_PX))


class Spinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._on = False
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._turn)
        self.setFixedSize(12, 12)
        self.hide()

    def start(self):
        self._on = True
        self.show()
        self._timer.start()
        self.update()

    def stop(self):
        self._on = False
        self._timer.stop()
        self.hide()

    def _turn(self):
        self._angle = (self._angle + 24) % 360
        self.update()

    def paintEvent(self, _event):
        if not self._on:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#8ec8bc"))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(self.rect().adjusted(2, 2, -2, -2), int(self._angle * 16), 110 * 16)
        painter.end()


class IndexWorker(QThread):
    logged = Signal(str)
    produced = Signal(object)

    def __init__(self, pdf_paths):
        super().__init__()
        self.pdf_paths = list(pdf_paths)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            process_pdfs(
                self.pdf_paths,
                self.logged.emit,
                self.produced.emit,
                lambda: self._stop,
            )
        except Exception as error:
            self.logged.emit("실패: " + redact(error))


class ModelWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def run(self):
        secret = ""
        try:
            key = load_key()
            secret = key.value
            models = list_available(key.provider, key.value)
            self.ready.emit((key.provider, models))
        except Exception as error:
            self.failed.emit(redact(error, secret))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.model_worker = None
        self.setWindowTitle("ExamPatternLab")
        self.setMinimumSize(560, 360)
        self.resize(560, 360)
        self._build()

    def _build(self):
        root = QWidget()
        root.setAcceptDrops(True)
        root.installEventFilter(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.add_button = QPushButton("PDF 추가")
        self.remove_button = QPushButton("빼기")
        self.model_button = QPushButton("모델")
        self.model_label = QLabel(self.model_caption())
        self.model_label.setObjectName("model")
        self.download_button = QPushButton("다운로드")
        self.download_button.setEnabled(False)
        self.spinner = Spinner()
        self.run_button = QPushButton("실행")
        self.run_button.setObjectName("run")
        self.setWindowIcon(window_icon())
        attach_icon(self.add_button, "add")
        attach_icon(self.remove_button, "remove")
        attach_icon(self.model_button, "model")
        attach_icon(self.download_button, "download")
        attach_icon(self.run_button, "run", "#1a1206")
        actions = QHBoxLayout()
        actions.setSpacing(4)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.download_button)
        actions.addWidget(self.spinner)
        actions.addWidget(self.run_button)
        bar.addWidget(self.add_button)
        bar.addWidget(self.remove_button)
        bar.addWidget(self.model_button)
        bar.addWidget(self.model_label)
        bar.addStretch(1)
        bar.addLayout(actions)
        layout.addLayout(bar)

        mono = QFont()
        mono.setFamilies(["Consolas", "Malgun Gothic"])
        mono.setPointSize(9)

        self.input_list = QListWidget()
        self.input_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.input_list.setFont(mono)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setAcceptDrops(False)
        self.log_box.setFont(mono)
        self.output_list = QListWidget()
        self.output_list.setFont(mono)
        body = QGridLayout()
        body.setSpacing(6)
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(self._pane("입력", self.input_list), 0, 0)
        body.addLayout(self._pane("결과", self.output_list), 1, 0)
        body.addLayout(self._pane("로그", self.log_box), 0, 1, 2, 1)
        body.setRowStretch(0, 2)
        body.setRowStretch(1, 1)
        body.setColumnStretch(0, 1)
        body.setColumnStretch(1, 1)
        layout.addLayout(body, 1)

        self.add_button.clicked.connect(self.add_pdfs)
        self.remove_button.clicked.connect(self.remove_selected)
        self.run_button.clicked.connect(self.start_job)
        self.model_button.clicked.connect(self.choose_model)
        self.download_button.clicked.connect(self.download_selected)
        self.output_list.itemSelectionChanged.connect(self.refresh_download)

    def _pane(self, title, widget):
        block = QVBoxLayout()
        block.setSpacing(2)
        block.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("pane")
        block.addWidget(label)
        block.addWidget(widget, 1)
        return block

    def log(self, message):
        self.log_box.appendPlainText(str(message))

    def set_running(self, running):
        for button in (self.add_button, self.remove_button, self.run_button):
            button.setEnabled(not running)
        if running:
            self.spinner.start()
        else:
            self.spinner.stop()

    def add_pdfs(self):
        start = ROOT_DIR
        paths, _selected = QFileDialog.getOpenFileNames(
            self,
            "PDF 추가",
            str(start),
            "PDF (*.pdf)",
        )
        added = self.add_paths(paths)
        if paths and added == 0:
            self.log("이미 목록에 있는 PDF입니다.")

    def eventFilter(self, watched, event):
        if watched is self.centralWidget():
            kind = event.type()
            if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if self._drop_pdfs(event.mimeData()):
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
            if kind == QEvent.Type.Drop:
                paths = self._drop_pdfs(event.mimeData())
                if paths:
                    added = self.add_paths(paths)
                    if added == 0:
                        self.log("이미 목록에 있는 PDF입니다.")
                    event.acceptProposedAction()
                else:
                    event.ignore()
                return True
        return super().eventFilter(watched, event)

    def _drop_pdfs(self, mime):
        if not self.add_button.isEnabled() or not mime.hasUrls():
            return []
        paths = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if Path(path).suffix.lower() == ".pdf":
                paths.append(path)
        return paths

    def add_paths(self, paths):
        existing = {
            self.input_list.item(index).data(Qt.UserRole)
            for index in range(self.input_list.count())
        }
        added = 0
        for raw in paths:
            resolved = str(Path(raw).resolve())
            if resolved in existing or Path(resolved).suffix.lower() != ".pdf":
                continue
            existing.add(resolved)
            item = QListWidgetItem(Path(resolved).name)
            item.setData(Qt.UserRole, resolved)
            item.setToolTip(resolved)
            self.input_list.addItem(item)
            added += 1
        return added

    def remove_selected(self):
        for item in self.input_list.selectedItems():
            self.input_list.takeItem(self.input_list.row(item))

    def start_job(self):
        paths = [
            self.input_list.item(index).data(Qt.UserRole)
            for index in range(self.input_list.count())
        ]
        if not paths:
            self.log("넣을 PDF가 없습니다.")
            return
        try:
            config = load()
        except Exception as error:
            self.log("실패: " + redact(error))
            return
        if config.key.provider != "GEMINI":
            self.log(f"실패: CURRENT_KEY={config.key.provider} 는 아직 호출할 수 없습니다. 지금은 GEMINI만 문항 색인을 실행합니다.")
            return
        self.set_running(True)
        self.worker = IndexWorker(paths)
        self.worker.logged.connect(self.log)
        self.worker.produced.connect(self.add_output)
        self.worker.finished.connect(self.job_finished)
        self.worker.start()

    def job_finished(self):
        self.set_running(False)
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()

    def add_output(self, held):
        item = QListWidgetItem(held.name)
        item.setData(Qt.UserRole, held.name)
        item.setData(Qt.UserRole + 1, held.data)
        item.setData(Qt.UserRole + 2, False)
        item.setToolTip("이 창 안에만 있습니다. 다운로드하면 파일이 생깁니다.")
        self.output_list.addItem(item)
        self.output_list.setCurrentItem(item)
        self.refresh_download()

    def refresh_download(self):
        self.download_button.setEnabled(self.output_list.currentItem() is not None)

    def pending_outputs(self):
        for index in range(self.output_list.count()):
            item = self.output_list.item(index)
            if not item.data(Qt.UserRole + 2):
                return True
        return False

    def download_selected(self):
        item = self.output_list.currentItem()
        if item is None:
            return
        name = item.data(Qt.UserRole)
        data = item.data(Qt.UserRole + 1)
        destination, _selected = QFileDialog.getSaveFileName(
            self,
            "다운로드",
            str(Path.home() / name),
            "PDF (*.pdf)",
        )
        if not destination:
            return
        if not destination.lower().endswith(".pdf"):
            destination += ".pdf"
        try:
            saved = save_download(data, destination)
        except Exception as error:
            self.log(f"실패: {error}")
            return
        item.setData(Qt.UserRole + 2, True)
        self.log(f"다운로드: {saved}")

    def model_caption(self):
        try:
            key = load_key()
            name = read_cached_model(key.provider)
        except Exception as error:
            return redact(error)
        if not name:
            return "모델을 고르세요"
        return name

    def choose_model(self):
        if self.model_worker is not None and self.model_worker.isRunning():
            return
        self.model_button.setEnabled(False)
        self.log("사용 가능한 모델을 조회합니다.")
        self.model_worker = ModelWorker()
        self.model_worker.ready.connect(self.show_model_dialog)
        self.model_worker.failed.connect(self.model_list_failed)
        self.model_worker.finished.connect(self.model_list_finished)
        self.model_worker.start()

    def model_list_failed(self, message):
        self.model_button.setEnabled(True)
        self.log("실패: " + message)

    def model_list_finished(self):
        worker = self.model_worker
        self.model_worker = None
        if worker is not None:
            worker.deleteLater()

    def show_model_dialog(self, payload):
        self.model_button.setEnabled(False)
        try:
            self._pick_model(payload)
        finally:
            self.model_button.setEnabled(True)

    def _pick_model(self, payload):
        provider, models = payload
        dialog = QDialog(self)
        dialog.setWindowTitle("AI 모델 선택")
        dialog.setWindowIcon(self.windowIcon())
        dialog.resize(560, 640)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{provider}  {len(models)}개"))
        listing = QListWidget()
        mono = QFont()
        mono.setFamilies(["Consolas", "Malgun Gothic"])
        mono.setPointSize(11)
        listing.setFont(mono)
        current = ""
        try:
            current = read_cached_model(provider)
        except Exception:
            current = ""
        selected_row = 0
        for index, model in enumerate(models):
            item = QListWidgetItem(model.label)
            item.setData(Qt.UserRole, model.id)
            item.setToolTip(model.id)
            listing.addItem(item)
            if model.id == current:
                selected_row = index
        listing.setCurrentRow(selected_row)
        listing.scrollToItem(listing.currentItem())
        listing.itemDoubleClicked.connect(dialog.accept)
        layout.addWidget(listing, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        ok = QPushButton("선택")
        cancel = QPushButton("취소")
        attach_icon(ok, "check")
        attach_icon(cancel, "close")
        ok.clicked.connect(dialog.accept)
        cancel.clicked.connect(dialog.reject)
        actions.addWidget(ok)
        actions.addWidget(cancel)
        layout.addLayout(actions)
        if dialog.exec() != QDialog.Accepted:
            self.log("모델 선택을 취소했습니다.")
            return
        item = listing.currentItem()
        if item is None:
            self.log("고른 모델이 없습니다.")
            return
        model_id = item.data(Qt.UserRole)
        try:
            write_cached_model(provider, model_id)
        except Exception as error:
            self.log("실패: " + redact(error))
            return
        self.model_label.setText(model_id)
        if self.worker is not None and self.worker.isRunning():
            self.log(f"다음 실행 모델: {model_id}")
            return
        self.log(f"모델: {model_id}")

    def closeEvent(self, event):
        model_worker = self.model_worker
        if model_worker is not None and model_worker.isRunning():
            model_worker.wait(1500)
        worker = self.worker
        running = worker is not None and worker.isRunning()
        pending = self.pending_outputs()
        if running or pending:
            box = QMessageBox(self)
            box.setWindowTitle("닫기")
            if running and pending:
                box.setText("진행 중인 호출이 있고, 아직 파일로 받지 않은 PDF가 있습니다.")
                box.setInformativeText("창을 닫으면 호출이 끊기고, 받지 않은 PDF도 사라집니다.")
            elif running:
                box.setText("진행 중인 호출이 있습니다. 창을 닫으면 호출도 끊깁니다.")
                box.setInformativeText("닫을까요?")
            else:
                box.setText("아직 파일로 받지 않은 PDF가 있습니다.")
                box.setInformativeText("창을 닫으면 그 PDF는 사라집니다.")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            box.setEscapeButton(QMessageBox.Yes)
            box.button(QMessageBox.Yes).setText("계속")
            box.button(QMessageBox.No).setText("닫기")
            if box.exec() != QMessageBox.No:
                event.ignore()
                return
            if running:
                worker.stop()
                event.accept()
                os._exit(0)
        event.accept()


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ExamPatternLab")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(window_icon())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
