"""
main.py — Thin loader for the Dark Carnival Cognitive RPA agent.

This file is the ONLY thing PyInstaller compiles into logic-bearing code, and it contains
exactly two concerns:

  1. the PySide6 GUI (control panel, dev console, log view), and
  2. the ``importlib`` loader that imports/reloads the external business-logic scripts.

**No automation logic lives here.** Vision, input synthesis, the Dota state machine and
the learning store are raw ``.py`` files in ``scripts/`` next to the executable, loaded at
runtime and hot-swappable without restarting the app.

Run in dev:      python main.py
Build the exe:   pyinstaller DarkCarnival.spec
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from pathlib import Path

# The frozen shell packages (loader / OTA / paths / settings) live beside this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import __version__, paths
from app.loader import DEFAULT_ORDER, ScriptHost
from app.ota import OtaClient, OtaConfig
from app.settings import Settings

try:
    from PySide6.QtCore import QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QFont, QKeySequence, QShortcut, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSpinBox,
        QSplitter,
        QStatusBar,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover - GUI is optional on CI / headless boxes
    print("PySide6 is required to run the GUI:  pip install PySide6", file=sys.stderr)
    raise


# ======================================================================================
# Logging
# ======================================================================================

def setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-12s %(message)s", "%H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.setLevel(logging.INFO)
    root.addHandler(stream)
    try:
        fh = logging.FileHandler(paths.log_file(), encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:  # pragma: no cover
        pass


log = logging.getLogger("dc.gui")


# ======================================================================================
# Qt bridge — the worker thread talks to the GUI only through signals
# ======================================================================================

class Bridge(QObject):
    """Thread-safe signal relay between the executor worker and the Qt main thread."""

    logged = Signal(str, str)      # level, message
    state_changed = Signal(str)
    cycle_done = Signal(object)
    stats_updated = Signal(dict)
    action = Signal(str)
    finished = Signal(dict)


class AgentWorker(threading.Thread):
    """Runs ``executor.Executor.run()`` off the GUI thread."""

    daemon = True

    def __init__(self, host: ScriptHost, settings: Settings, bridge: Bridge) -> None:
        super().__init__(name="dc-agent")
        self.host = host
        self.settings = settings
        self.bridge = bridge
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.executor = None

    def build(self):
        """Construct the executor from the *currently loaded* script modules."""
        executor_mod = self.host.get("executor")
        logic = self.host.get("dota_logic")
        vision_mod = self.host.get("vision")
        input_mod = self.host.get("input_handler")
        learning_mod = self.host.get("learning")

        cfg = logic.LoopConfig(
            max_cycles=self.settings.max_cycles,
            hero_pool=tuple(self.settings.hero_pool),
            ticket_target=dict(self.settings.ticket_target),
            leave_early=self.settings.leave_early,
            poll_interval=self.settings.poll_interval,
            simulate=self.settings.simulate,
            assets_dir=str(paths.assets_dir()),
        )
        events = executor_mod.ExecutorEvents(
            on_log=lambda level, msg: self.bridge.logged.emit(level, msg),
            on_state=lambda s: self.bridge.state_changed.emit(s),
            on_cycle=lambda rec: self.bridge.cycle_done.emit(rec),
            on_stats=lambda st: self.bridge.stats_updated.emit(st),
            on_action=lambda a: self.bridge.action.emit(a),
        )
        vision = vision_mod.Vision(
            ocr=vision_mod.EasyOcrBackend(["ru", "en"], gpu=self.settings.ocr_gpu),
            config=vision_mod.VisionConfig(
                assets_dir=str(paths.assets_dir()),
                match_threshold=self.settings.match_threshold,
                upscale=self.settings.upscale,
            ),
        )
        inputs = input_mod.InputHandler(simulate=self.settings.simulate)
        brain = learning_mod.LearningStore(str(paths.brain_file()))
        return executor_mod.Executor(
            vision=vision, inputs=inputs, logic=logic, brain=brain, config=cfg,
            events=events, stop_event=self.stop_event, pause_event=self.pause_event,
        )

    def run(self) -> None:
        summary: dict = {}
        try:
            self.executor = self.build()
            summary = self.executor.run() or {}
        except Exception as exc:
            self.bridge.logged.emit("error", f"agent crashed: {exc!r}")
            self.bridge.logged.emit("debug", traceback.format_exc())
        finally:
            self.bridge.finished.emit(summary)


# ======================================================================================
# Main window
# ======================================================================================

DARK_QSS = """
QWidget      { background:#14161b; color:#d7dae0; font-size:13px; }
QGroupBox    { border:1px solid #2a2e37; border-radius:6px; margin-top:10px; padding-top:8px; }
QGroupBox::title { subcontrol-origin: margin; left:10px; color:#8f98a8; }
QPushButton  { background:#232832; border:1px solid #333a47; border-radius:5px; padding:7px 14px; }
QPushButton:hover    { background:#2c323e; }
QPushButton:disabled { color:#5a6070; background:#1b1e25; }
QPushButton#primary  { background:#2f6f4f; border-color:#3d8c64; font-weight:bold; }
QPushButton#primary:hover { background:#38865f; }
QPushButton#danger   { background:#7a3030; border-color:#9c4040; font-weight:bold; }
QPushButton#danger:hover  { background:#8f3838; }
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget {
    background:#0f1115; border:1px solid #2a2e37; border-radius:4px; padding:4px;
    selection-background-color:#2f6f4f;
}
QTabBar::tab { background:#1b1e25; padding:8px 16px; border:1px solid #2a2e37; }
QTabBar::tab:selected { background:#232832; color:#ffffff; }
QLabel#state { font-size:17px; font-weight:bold; color:#6fc49a; }
"""

LEVEL_COLORS = {
    "debug": "#6b7280", "info": "#d7dae0", "warning": "#e0b464",
    "error": "#e06c75", "success": "#6fc49a",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Dark Carnival — Cognitive RPA v{__version__}")
        self.resize(1180, 780)

        self.settings = Settings.load(paths.settings_file())
        self.bridge = Bridge()
        self.worker: AgentWorker | None = None

        self.host = ScriptHost(
            directory=paths.scripts_dir(),
            order=DEFAULT_ORDER,
            backups=paths.backups_dir(),
            on_log=lambda level, msg: self.bridge.logged.emit(level, msg),
        )
        self.ota = OtaClient(self._ota_config())

        self._build_ui()
        self._wire_signals()
        self._load_settings_into_ui()

        QTimer.singleShot(50, self._initial_load)

    # ---------------------------------------------------------------- UI construction

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._build_control_tab(), "Control")
        tabs.addTab(self._build_console_tab(), "Dev Console")
        tabs.addTab(self._build_ota_tab(), "GitHub OTA")

        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMaximumBlockCount(5000)

        log_box = QGroupBox("Live log")
        lv = QVBoxLayout(log_box)
        lv.addWidget(self.log_view)
        btns = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.log_view.clear)
        open_log = QPushButton("Open log file")
        open_log.clicked.connect(lambda: self.append_log("info", f"log file: {paths.log_file()}"))
        btns.addWidget(clear)
        btns.addWidget(open_log)
        btns.addStretch(1)
        lv.addLayout(btns)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(tabs)
        splitter.addWidget(log_box)
        splitter.setSizes([520, 260])
        self.setCentralWidget(splitter)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(f"scripts: {paths.scripts_dir()}")

        panic = QShortcut(QKeySequence("F12"), self)
        panic.activated.connect(self.on_stop)

    def _build_control_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # --- status row
        status_box = QGroupBox("Agent")
        grid = QGridLayout(status_box)
        self.state_label = QLabel("idle")
        self.state_label.setObjectName("state")
        self.cycle_label = QLabel("cycles: 0")
        self.tickets_label = QLabel("tickets: —")
        self.action_label = QLabel("last action: —")
        grid.addWidget(QLabel("State:"), 0, 0)
        grid.addWidget(self.state_label, 0, 1)
        grid.addWidget(self.cycle_label, 0, 2)
        grid.addWidget(self.action_label, 1, 0, 1, 2)
        grid.addWidget(self.tickets_label, 1, 2)
        layout.addWidget(status_box)

        # --- controls
        ctl = QHBoxLayout()
        self.btn_start = QPushButton("▶  Start")
        self.btn_start.setObjectName("primary")
        self.btn_pause = QPushButton("⏸  Pause")
        self.btn_stop = QPushButton("■  Stop  (F12)")
        self.btn_stop.setObjectName("danger")
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            ctl.addWidget(b)
        ctl.addStretch(1)
        layout.addLayout(ctl)

        # --- loop settings
        cfg_box = QGroupBox("Loop configuration")
        form = QFormLayout(cfg_box)
        self.in_cycles = QSpinBox()
        self.in_cycles.setRange(0, 9999)
        self.in_cycles.setSpecialValueText("unlimited")
        self.in_poll = QDoubleSpinBox()
        self.in_poll.setRange(0.5, 30.0)
        self.in_poll.setSingleStep(0.5)
        self.in_simulate = QCheckBox("Simulation mode (never touch the real mouse/keyboard)")
        self.in_leave = QCheckBox("Leave as soon as «Игру можно безопасно покинуть» appears")
        self.in_gpu = QCheckBox("Use GPU for EasyOCR")
        self.in_threshold = QDoubleSpinBox()
        self.in_threshold.setRange(0.5, 1.0)
        self.in_threshold.setSingleStep(0.02)
        form.addRow("Max cycles", self.in_cycles)
        form.addRow("Poll interval (s)", self.in_poll)
        form.addRow("OCR match threshold", self.in_threshold)
        form.addRow("", self.in_simulate)
        form.addRow("", self.in_leave)
        form.addRow("", self.in_gpu)
        layout.addWidget(cfg_box)

        # --- hero pool
        hero_box = QGroupBox("Hero pool (dual-asset DB — emoji for rewards, portrait for picks)")
        hv = QVBoxLayout(hero_box)
        self.hero_list = QListWidget()
        self.hero_list.setSelectionMode(QListWidget.NoSelection)
        hv.addWidget(self.hero_list)
        hrow = QHBoxLayout()
        self.btn_refresh_heroes = QPushButton("Reload hero DB")
        hrow.addWidget(self.btn_refresh_heroes)
        hrow.addStretch(1)
        hv.addLayout(hrow)
        layout.addWidget(hero_box, 1)

        return page

    def _build_console_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(
            "Business logic runs from external <b>.py</b> files beside the executable. "
            "Edit or paste code here and hit <b>Apply &amp; Reload</b> — "
            "<code>importlib.reload()</code> swaps it into the running process instantly."
        ))

        row = QHBoxLayout()
        self.script_picker = QComboBox()
        self.script_picker.addItems(list(DEFAULT_ORDER))
        self.btn_open_script = QPushButton("Load file into editor")
        self.btn_apply = QPushButton("Apply && Reload")
        self.btn_apply.setObjectName("primary")
        self.btn_reload_all = QPushButton("Reload ALL scripts")
        self.btn_rollback = QPushButton("Rollback")
        row.addWidget(QLabel("Script:"))
        row.addWidget(self.script_picker)
        row.addWidget(self.btn_open_script)
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_reload_all)
        row.addWidget(self.btn_rollback)
        row.addStretch(1)
        layout.addLayout(row)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.setPlaceholderText("# Paste replacement code for the selected script…")
        layout.addWidget(self.editor, 1)

        self.script_status = QPlainTextEdit(readOnly=True)
        self.script_status.setFont(QFont("Consolas", 9))
        self.script_status.setMaximumHeight(130)
        layout.addWidget(self.script_status)
        return page

    def _build_ota_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        box = QGroupBox("GitHub source")
        form = QFormLayout(box)
        self.in_owner = QLineEdit()
        self.in_repo = QLineEdit()
        self.in_branch = QLineEdit()
        self.in_path = QLineEdit()
        self.in_token = QLineEdit()
        self.in_token.setEchoMode(QLineEdit.Password)
        self.in_token.setPlaceholderText("optional — only for private repos / rate limits")
        self.in_autosync = QCheckBox("Sync automatically on startup")
        form.addRow("Owner", self.in_owner)
        form.addRow("Repository", self.in_repo)
        form.addRow("Branch", self.in_branch)
        form.addRow("Scripts path", self.in_path)
        form.addRow("Token", self.in_token)
        form.addRow("", self.in_autosync)
        layout.addWidget(box)

        row = QHBoxLayout()
        self.btn_sync = QPushButton("⟳  Sync from GitHub && Hot-Reload")
        self.btn_sync.setObjectName("primary")
        self.btn_list_remote = QPushButton("List remote scripts")
        self.btn_save_settings = QPushButton("Save settings")
        row.addWidget(self.btn_sync)
        row.addWidget(self.btn_list_remote)
        row.addWidget(self.btn_save_settings)
        row.addStretch(1)
        layout.addLayout(row)

        self.ota_output = QPlainTextEdit(readOnly=True)
        self.ota_output.setFont(QFont("Consolas", 9))
        layout.addWidget(self.ota_output, 1)
        return page

    # ------------------------------------------------------------------------ wiring

    def _wire_signals(self) -> None:
        self.bridge.logged.connect(self.append_log)
        self.bridge.state_changed.connect(self.on_state_changed)
        self.bridge.cycle_done.connect(self.on_cycle_done)
        self.bridge.stats_updated.connect(self.on_stats)
        self.bridge.action.connect(lambda a: self.action_label.setText(f"last action: {a}"))
        self.bridge.finished.connect(self.on_finished)

        self.btn_start.clicked.connect(self.on_start)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_refresh_heroes.clicked.connect(self.refresh_hero_list)

        self.btn_open_script.clicked.connect(self.on_open_script)
        self.btn_apply.clicked.connect(self.on_apply_script)
        self.btn_reload_all.clicked.connect(self.on_reload_all)
        self.btn_rollback.clicked.connect(self.on_rollback)

        self.btn_sync.clicked.connect(self.on_sync)
        self.btn_list_remote.clicked.connect(self.on_list_remote)
        self.btn_save_settings.clicked.connect(self.on_save_settings)

    def _ota_config(self) -> OtaConfig:
        s = self.settings
        return OtaConfig(owner=s.ota_owner, repo=s.ota_repo, branch=s.ota_branch,
                         path=s.ota_path, token=s.ota_token)

    def _load_settings_into_ui(self) -> None:
        s = self.settings
        self.in_cycles.setValue(s.max_cycles)
        self.in_poll.setValue(s.poll_interval)
        self.in_threshold.setValue(s.match_threshold)
        self.in_simulate.setChecked(s.simulate)
        self.in_leave.setChecked(s.leave_early)
        self.in_gpu.setChecked(s.ocr_gpu)
        self.in_owner.setText(s.ota_owner)
        self.in_repo.setText(s.ota_repo)
        self.in_branch.setText(s.ota_branch)
        self.in_path.setText(s.ota_path)
        self.in_token.setText(s.ota_token)
        self.in_autosync.setChecked(s.auto_sync_on_start)

    def _collect_settings(self) -> Settings:
        s = self.settings
        s.max_cycles = self.in_cycles.value()
        s.poll_interval = self.in_poll.value()
        s.match_threshold = self.in_threshold.value()
        s.simulate = self.in_simulate.isChecked()
        s.leave_early = self.in_leave.isChecked()
        s.ocr_gpu = self.in_gpu.isChecked()
        s.ota_owner = self.in_owner.text().strip()
        s.ota_repo = self.in_repo.text().strip()
        s.ota_branch = self.in_branch.text().strip() or "main"
        s.ota_path = self.in_path.text().strip()
        s.ota_token = self.in_token.text().strip()
        s.auto_sync_on_start = self.in_autosync.isChecked()
        s.hero_pool = [
            self.hero_list.item(i).data(Qt.UserRole)
            for i in range(self.hero_list.count())
            if self.hero_list.item(i).checkState() == Qt.Checked
        ] or s.hero_pool
        return s

    # --------------------------------------------------------------------- behaviour

    def append_log(self, level: str, message: str) -> None:
        color = LEVEL_COLORS.get(level, "#d7dae0")
        safe = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self.log_view.appendHtml(f'<span style="color:{color}">{safe}</span>')
        self.log_view.moveCursor(QTextCursor.End)

    def _initial_load(self) -> None:
        self.append_log("info", f"scripts directory: {paths.scripts_dir()}")
        missing = self.host.missing()
        if missing:
            self.append_log("warning", f"missing external scripts: {', '.join(missing)}")
        results = self.host.load_all()
        for r in results:
            self.append_log("success" if r.ok else "error",
                            f"{'✓' if r.ok else '✗'} {r.name}.py"
                            + (f"  ({r.duration * 1000:.0f} ms, {r.source_hash})" if r.ok
                               else f"  {r.error}"))
        self.refresh_script_status()
        self.refresh_hero_list()
        if self.settings.auto_sync_on_start:
            self.on_sync()

    def refresh_script_status(self) -> None:
        lines = []
        for name, info in self.host.status().items():
            mark = "✓" if info["loaded"] else ("•" if info["exists"] else "✗")
            lines.append(f"{mark} {name:<14} hash={info['hash'] or '-':<12} {info['path']}")
        self.script_status.setPlainText("\n".join(lines))
        self.status.showMessage(
            "all scripts loaded" if self.host.healthy else "⚠ some scripts failed to load"
        )

    def refresh_hero_list(self) -> None:
        self.hero_list.clear()
        try:
            logic = self.host.get("dota_logic")
        except Exception as exc:
            self.append_log("error", f"hero DB unavailable: {exc}")
            return
        for hero in logic.all_heroes():
            item = QListWidgetItem(
                f"{hero.name_ru:<22} [{hero.key}]   tickets: {', '.join(hero.grants_tickets)}"
                f"   emoji={hero.emoji_img}  portrait={hero.portrait_img}"
            )
            item.setData(Qt.UserRole, hero.key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if hero.key in self.settings.hero_pool else Qt.Unchecked)
            self.hero_list.addItem(item)
        self.append_log("info", f"hero DB: {self.hero_list.count()} heroes loaded")

    # -- agent control

    def on_start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.host.healthy:
            QMessageBox.warning(self, "Scripts not loaded",
                                "Some external scripts failed to load. Fix them in the "
                                "Dev Console before starting the agent.")
            return
        self.settings = self._collect_settings()
        self.settings.save(paths.settings_file())
        if not self.settings.simulate:
            answer = QMessageBox.question(
                self, "Live mode",
                "Simulation mode is OFF — the agent will control the real mouse and "
                "keyboard.\n\nStart in LIVE mode?",
            )
            if answer != QMessageBox.Yes:
                return
        self.worker = AgentWorker(self.host, self.settings, self.bridge)
        self.worker.start()
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.append_log("success", "agent started"
                        + (" (SIMULATION)" if self.settings.simulate else " (LIVE)"))

    def on_pause(self) -> None:
        if not self.worker:
            return
        if self.worker.pause_event.is_set():
            self.worker.pause_event.clear()
            self.btn_pause.setText("⏸  Pause")
            self.append_log("info", "resumed")
        else:
            self.worker.pause_event.set()
            self.btn_pause.setText("▶  Resume")
            self.append_log("warning", "paused")

    def on_stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.worker.stop_event.set()
            self.worker.pause_event.clear()
            self.append_log("warning", "stop requested…")
        self.btn_stop.setEnabled(False)

    def on_finished(self, summary: dict) -> None:
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸  Pause")
        self.btn_stop.setEnabled(False)
        self.state_label.setText("idle")
        self.append_log("info", f"session summary: {summary}")

    def on_state_changed(self, state: str) -> None:
        self.state_label.setText(state)

    def on_cycle_done(self, record) -> None:
        self.cycle_label.setText(f"cycles: {getattr(record, 'index', 0) + 1}")

    def on_stats(self, stats: dict) -> None:
        tickets = stats.get("tickets", {})
        self.tickets_label.setText(
            "tickets: " + (", ".join(f"{k}×{v}" for k, v in tickets.items()) or "—")
        )

    # -- dev console

    def on_open_script(self) -> None:
        name = self.script_picker.currentText()
        try:
            self.editor.setPlainText(self.host.source_of(name))
            self.append_log("info", f"loaded {name}.py into the editor")
        except OSError as exc:
            self.append_log("error", f"cannot read {name}.py: {exc}")

    def on_apply_script(self) -> None:
        name = self.script_picker.currentText()
        source = self.editor.toPlainText()
        if not source.strip():
            self.append_log("warning", "editor is empty — nothing to apply")
            return
        try:
            result = self.host.write_script(name, source, reload=True)
        except Exception as exc:
            self.append_log("error", f"rejected: {exc}")
            return
        self.append_log("success" if result.ok else "error",
                        f"{name}.py → {'reloaded' if result.ok else result.error}")
        self.refresh_script_status()
        self.refresh_hero_list()

    def on_reload_all(self) -> None:
        if self.worker and self.worker.is_alive():
            self.append_log("warning", "stop the agent before reloading all scripts")
            return
        results = self.host.reload_all()
        for r in results:
            self.append_log("success" if r.ok else "error",
                            f"{'✓' if r.ok else '✗'} {r.name}.py {r.error}")
        self.refresh_script_status()
        self.refresh_hero_list()

    def on_rollback(self) -> None:
        name = self.script_picker.currentText()
        if self.host.rollback(name):
            self.append_log("success", f"{name}.py rolled back and reloaded")
        self.refresh_script_status()

    # -- OTA

    def on_save_settings(self) -> None:
        self.settings = self._collect_settings()
        self.settings.save(paths.settings_file())
        self.ota = OtaClient(self._ota_config())
        self.append_log("success", f"settings saved → {paths.settings_file()}")

    def on_list_remote(self) -> None:
        self.on_save_settings()
        try:
            names = self.ota.list_remote_scripts()
        except Exception as exc:
            self.ota_output.appendPlainText(f"✗ {exc}")
            return
        self.ota_output.appendPlainText(
            f"remote scripts in {self.ota.config.slug}@{self.ota.config.branch}"
            f"/{self.ota.config.path}:\n  " + "\n  ".join(names)
        )

    def on_sync(self) -> None:
        if self.worker and self.worker.is_alive():
            self.append_log("warning", "stop the agent before syncing scripts")
            return
        self.on_save_settings()
        self.ota_output.appendPlainText(
            f"→ syncing from {self.ota.config.slug}@{self.ota.config.branch}…")
        results = self.ota.sync(
            self.host,
            on_log=lambda level, msg: (self.append_log(level, msg),
                                       self.ota_output.appendPlainText(f"  {msg}")),
        )
        changed = sum(1 for r in results if r.changed)
        failed = [r.name for r in results if not r.ok]
        self.ota_output.appendPlainText(
            f"← done: {changed} updated, {len(results) - changed - len(failed)} unchanged, "
            f"{len(failed)} failed {failed if failed else ''}")
        self.refresh_script_status()
        self.refresh_hero_list()

    # -- lifecycle

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.worker and self.worker.is_alive():
            self.worker.stop_event.set()
            self.worker.join(timeout=5.0)
        try:
            self._collect_settings().save(paths.settings_file())
        except Exception:  # pragma: no cover
            pass
        super().closeEvent(event)


def main() -> int:
    setup_logging()
    log.info("Dark Carnival RPA v%s starting (frozen=%s)", __version__, paths.is_frozen())
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
