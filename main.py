"""
main.py — Тонкий загрузчик агента «Тёмный карнавал».

Это единственный файл, который компилируется в .exe, и в нём ровно две вещи:

  1. интерфейс на PySide6 (панель управления, билеты, консоль разработчика, OTA);
  2. загрузчик внешних скриптов на ``importlib``.

**Никакой логики автоматизации здесь нет.** Зрение, ввод, машина состояний Dota,
таблица билетов и обучение — это обычные .py-файлы в папке ``scripts/`` рядом с
исполняемым файлом. Они подгружаются в рантайме и заменяются «на лету», без
перезапуска программы.

Запуск из исходников:  python main.py
Сборка .exe:           pyinstaller DarkCarnival.spec
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from pathlib import Path

# Служебные пакеты оболочки (загрузчик / OTA / пути / настройки) лежат рядом.
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
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QSplitter,
        QStatusBar,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover - на CI графики нет
    print("Для работы интерфейса нужен PySide6:  pip install PySide6", file=sys.stderr)
    raise


# ======================================================================================
# Журналирование
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
# Мост между рабочим потоком и интерфейсом
# ======================================================================================

class Bridge(QObject):
    """Потокобезопасная передача событий агента в поток интерфейса."""

    logged = Signal(str, str)      # уровень, сообщение
    state_changed = Signal(str)
    cycle_done = Signal(object)
    stats_updated = Signal(dict)
    action = Signal(str)
    finished = Signal(dict)


class AgentWorker(threading.Thread):
    """Выполняет ``executor.Executor.run()`` вне потока интерфейса."""

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
        """Собрать агента из ТЕКУЩИХ загруженных версий скриптов."""
        executor_mod = self.host.get("executor")
        logic = self.host.get("dota_logic")
        vision_mod = self.host.get("vision")
        input_mod = self.host.get("input_handler")
        learning_mod = self.host.get("learning")

        cfg = logic.LoopConfig(
            max_cycles=self.settings.max_cycles,
            ticket_target=dict(self.settings.ticket_target),
            min_ticket_yield=self.settings.min_ticket_yield,
            avoid_heroes=tuple(self.settings.avoid_heroes),
            tickets_file=str(paths.tickets_file()),
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
            self.bridge.logged.emit("error", f"агент аварийно остановлен: {exc!r}")
            self.bridge.logged.emit("debug", traceback.format_exc())
        finally:
            self.bridge.finished.emit(summary)


# ======================================================================================
# Главное окно
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
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background:#0f1115; border:1px solid #2a2e37; border-radius:4px; padding:4px;
    selection-background-color:#2f6f4f;
}
QTabBar::tab { background:#1b1e25; padding:8px 16px; border:1px solid #2a2e37; }
QTabBar::tab:selected { background:#232832; color:#ffffff; }
QLabel#state { font-size:17px; font-weight:bold; color:#6fc49a; }
QLabel#arcana { font-weight:bold; color:#c9a227; }
QLabel#hint { color:#8f98a8; }
QScrollArea { border:none; }
"""

LEVEL_COLORS = {
    "debug": "#6b7280", "info": "#d7dae0", "warning": "#e0b464",
    "error": "#e06c75", "success": "#6fc49a",
}

#: Русские названия состояний для крупной надписи на панели.
RU_STATES = {
    "unknown": "неизвестный экран", "dashboard": "главное меню",
    "queueing": "поиск игры", "match_found": "игра найдена",
    "hero_pick": "выбор героя", "in_game": "идёт матч",
    "post_game": "итоги матча", "safe_to_leave": "можно выходить",
    "reward_screen": "экран наград", "disconnected": "нет соединения",
    "error": "ошибка", "idle": "ожидание",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Тёмный карнавал — когнитивный агент v{__version__}")
        self.resize(1180, 820)

        self.settings = Settings.load(paths.settings_file())
        self.bridge = Bridge()
        self.worker: AgentWorker | None = None
        self.book = None                     # таблица билетов (из внешнего скрипта)
        self.arcana_rows: dict[str, dict] = {}

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

    # ------------------------------------------------------------- построение окна

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_control_tab(), "Управление")
        self.tabs.addTab(self._build_tickets_tab(), "Билеты")
        self.tabs.addTab(self._build_console_tab(), "Консоль разработчика")
        self.tabs.addTab(self._build_ota_tab(), "Обновление с GitHub")

        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMaximumBlockCount(5000)

        log_box = QGroupBox("Журнал работы")
        lv = QVBoxLayout(log_box)
        lv.addWidget(self.log_view)
        btns = QHBoxLayout()
        clear = QPushButton("Очистить")
        clear.clicked.connect(self.log_view.clear)
        where = QPushButton("Где лежит файл журнала?")
        where.clicked.connect(lambda: self.append_log("info", f"журнал: {paths.log_file()}"))
        btns.addWidget(clear)
        btns.addWidget(where)
        btns.addStretch(1)
        lv.addLayout(btns)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(log_box)
        splitter.setSizes([560, 260])
        self.setCentralWidget(splitter)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(f"скрипты: {paths.scripts_dir()}")

        panic = QShortcut(QKeySequence("F12"), self)
        panic.activated.connect(self.on_stop)

    # -- вкладка «Управление»

    def _build_control_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        status_box = QGroupBox("Агент")
        grid = QGridLayout(status_box)
        self.state_label = QLabel("ожидание")
        self.state_label.setObjectName("state")
        self.cycle_label = QLabel("циклов: 0")
        self.tickets_label = QLabel("билеты: —")
        self.action_label = QLabel("последнее действие: —")
        grid.addWidget(QLabel("Состояние:"), 0, 0)
        grid.addWidget(self.state_label, 0, 1)
        grid.addWidget(self.cycle_label, 0, 2)
        grid.addWidget(self.action_label, 1, 0, 1, 2)
        grid.addWidget(self.tickets_label, 1, 2)
        layout.addWidget(status_box)

        ctl = QHBoxLayout()
        self.btn_start = QPushButton("▶  Старт")
        self.btn_start.setObjectName("primary")
        self.btn_pause = QPushButton("⏸  Пауза")
        self.btn_stop = QPushButton("■  Стоп  (F12)")
        self.btn_stop.setObjectName("danger")
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            ctl.addWidget(b)
        ctl.addStretch(1)
        layout.addLayout(ctl)

        cfg_box = QGroupBox("Параметры цикла")
        form = QFormLayout(cfg_box)
        self.in_yield = QSpinBox()
        self.in_yield.setRange(1, 3)
        self.in_yield.setToolTip(
            "3 — играть только на героях, дающих 3 билета за игру (рекомендуется).\n"
            "Снижать имеет смысл, только если «тройной» герой не определён."
        )
        self.in_cycles = QSpinBox()
        self.in_cycles.setRange(0, 9999)
        self.in_cycles.setSpecialValueText("без ограничения")
        self.in_poll = QDoubleSpinBox()
        self.in_poll.setRange(0.5, 30.0)
        self.in_poll.setSingleStep(0.5)
        self.in_threshold = QDoubleSpinBox()
        self.in_threshold.setRange(0.5, 1.0)
        self.in_threshold.setSingleStep(0.02)
        self.in_simulate = QCheckBox("Режим симуляции (не трогать реальные мышь и клавиатуру)")
        self.in_leave = QCheckBox("Выходить сразу по надписи «Игру можно безопасно покинуть»")
        self.in_gpu = QCheckBox("Использовать видеокарту для EasyOCR")
        form.addRow("Минимум билетов за игру", self.in_yield)
        form.addRow("Сколько циклов сыграть", self.in_cycles)
        form.addRow("Интервал опроса экрана, с", self.in_poll)
        form.addRow("Порог распознавания текста", self.in_threshold)
        form.addRow("", self.in_simulate)
        form.addRow("", self.in_leave)
        form.addRow("", self.in_gpu)
        layout.addWidget(cfg_box)

        hint = QLabel(
            "<b>Порядок действий:</b> 1) заполните вкладку «Билеты» — какой герой даёт ×3 "
            "для нужного аркана;  2) запустите Dota 2 в <b>оконном без рамки</b> режиме;  "
            "3) нажмите «Старт» с включённой симуляцией и убедитесь по журналу, что агент "
            "правильно читает русские надписи;  4) снимите галочку симуляции и запускайте "
            "по-настоящему. <b>F12 — аварийная остановка.</b>"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    # -- вкладка «Билеты»

    def _build_tickets_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(
            "Каждый аркан события — это панель из трёх секций: <b>×1</b>, <b>×2</b> и "
            "<b>×3</b> билета за игру. Нас интересует только нижняя секция <b>×3</b>: "
            "одна игра на таком герое приносит втрое больше при тех же затратах времени.<br>"
            "Откройте в игре панель аркана, посмотрите, кто стоит в секции ×3, и впишите "
            "его имя так, как оно пишется в <b>поиске героев</b> (по-русски). "
            "Столбец «нужно» — сколько билетов этого аркана вы хотите нафармить (0 — не фармить)."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        container = QWidget()
        grid = QGridLayout(container)
        grid.addWidget(QLabel("<b>Аркан</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Герой с отдачей ×3</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Нужно билетов</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Состояние</b>"), 0, 3)
        self.tickets_grid = grid
        self.tickets_container = container

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        row = QHBoxLayout()
        self.btn_tickets_save = QPushButton("💾  Сохранить таблицу билетов")
        self.btn_tickets_save.setObjectName("primary")
        self.btn_tickets_reload = QPushButton("Перечитать из файла")
        self.btn_tickets_open = QPushButton("Где лежит файл?")
        row.addWidget(self.btn_tickets_save)
        row.addWidget(self.btn_tickets_reload)
        row.addWidget(self.btn_tickets_open)
        row.addStretch(1)
        layout.addLayout(row)

        self.tickets_status = QLabel("—")
        self.tickets_status.setObjectName("hint")
        layout.addWidget(self.tickets_status)
        return page

    # -- вкладка «Консоль разработчика»

    def _build_console_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel(
            "Логика работает из внешних <b>.py</b>-файлов рядом с программой. "
            "Отредактируйте или вставьте код и нажмите <b>Применить и перезагрузить</b> — "
            "<code>importlib.reload()</code> подменит его в уже запущенной программе, "
            "перезапуск не нужен."
        ))

        row = QHBoxLayout()
        self.script_picker = QComboBox()
        self.script_picker.addItems(list(DEFAULT_ORDER))
        self.btn_open_script = QPushButton("Открыть файл в редакторе")
        self.btn_apply = QPushButton("Применить и перезагрузить")
        self.btn_apply.setObjectName("primary")
        self.btn_reload_all = QPushButton("Перезагрузить ВСЁ")
        self.btn_rollback = QPushButton("Откатить")
        row.addWidget(QLabel("Скрипт:"))
        row.addWidget(self.script_picker)
        row.addWidget(self.btn_open_script)
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_reload_all)
        row.addWidget(self.btn_rollback)
        row.addStretch(1)
        layout.addLayout(row)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.setPlaceholderText("# Вставьте сюда код выбранного скрипта…")
        layout.addWidget(self.editor, 1)

        self.script_status = QPlainTextEdit(readOnly=True)
        self.script_status.setFont(QFont("Consolas", 9))
        self.script_status.setMaximumHeight(140)
        layout.addWidget(self.script_status)
        return page

    # -- вкладка «Обновление с GitHub»

    def _build_ota_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        box = QGroupBox("Источник на GitHub")
        form = QFormLayout(box)
        self.in_owner = QLineEdit()
        self.in_repo = QLineEdit()
        self.in_branch = QLineEdit()
        self.in_path = QLineEdit()
        self.in_token = QLineEdit()
        self.in_token.setEchoMode(QLineEdit.Password)
        self.in_token.setPlaceholderText("не обязателен — только для приватных репозиториев")
        self.in_autosync = QCheckBox("Синхронизировать при запуске программы")
        form.addRow("Владелец", self.in_owner)
        form.addRow("Репозиторий", self.in_repo)
        form.addRow("Ветка", self.in_branch)
        form.addRow("Папка со скриптами", self.in_path)
        form.addRow("Токен", self.in_token)
        form.addRow("", self.in_autosync)
        layout.addWidget(box)

        row = QHBoxLayout()
        self.btn_sync = QPushButton("⟳  Скачать с GitHub и применить")
        self.btn_sync.setObjectName("primary")
        self.btn_list_remote = QPushButton("Показать список скриптов")
        self.btn_save_settings = QPushButton("Сохранить настройки")
        row.addWidget(self.btn_sync)
        row.addWidget(self.btn_list_remote)
        row.addWidget(self.btn_save_settings)
        row.addStretch(1)
        layout.addLayout(row)

        self.ota_output = QPlainTextEdit(readOnly=True)
        self.ota_output.setFont(QFont("Consolas", 9))
        layout.addWidget(self.ota_output, 1)
        return page

    # ----------------------------------------------------------------------- связи

    def _wire_signals(self) -> None:
        self.bridge.logged.connect(self.append_log)
        self.bridge.state_changed.connect(self.on_state_changed)
        self.bridge.cycle_done.connect(self.on_cycle_done)
        self.bridge.stats_updated.connect(self.on_stats)
        self.bridge.action.connect(lambda a: self.action_label.setText(f"последнее действие: {a}"))
        self.bridge.finished.connect(self.on_finished)

        self.btn_start.clicked.connect(self.on_start)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)

        self.btn_tickets_save.clicked.connect(self.on_tickets_save)
        self.btn_tickets_reload.clicked.connect(self.refresh_tickets_tab)
        self.btn_tickets_open.clicked.connect(
            lambda: self.append_log("info", f"таблица билетов: {paths.tickets_file()}"))

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
        self.in_yield.setValue(s.min_ticket_yield)
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
        s.min_ticket_yield = self.in_yield.value()
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
        s.ticket_target = {
            key: row["target"].value()
            for key, row in self.arcana_rows.items()
            if row["target"].value() > 0
        }
        return s

    # ------------------------------------------------------------------- поведение

    def append_log(self, level: str, message: str) -> None:
        color = LEVEL_COLORS.get(level, "#d7dae0")
        safe = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self.log_view.appendHtml(f'<span style="color:{color}">{safe}</span>')
        self.log_view.moveCursor(QTextCursor.End)

    def _initial_load(self) -> None:
        self.append_log("info", f"папка скриптов: {paths.scripts_dir()}")
        missing = self.host.missing()
        if missing:
            self.append_log("warning", f"отсутствуют скрипты: {', '.join(missing)}")
        for r in self.host.load_all():
            self.append_log(
                "success" if r.ok else "error",
                f"{'✓' if r.ok else '✗'} {r.name}.py"
                + (f"  ({r.duration * 1000:.0f} мс, {r.source_hash})" if r.ok else f"  {r.error}"),
            )
        self.refresh_script_status()
        self.refresh_tickets_tab()
        if self.settings.auto_sync_on_start:
            self.on_sync()

    def refresh_script_status(self) -> None:
        lines = []
        for name, info in self.host.status().items():
            mark = "✓" if info["loaded"] else ("•" if info["exists"] else "✗")
            lines.append(f"{mark} {name:<14} хеш={info['hash'] or '-':<12} {info['path']}")
        self.script_status.setPlainText("\n".join(lines))
        self.status.showMessage(
            "все скрипты загружены" if self.host.healthy
            else "⚠ часть скриптов не загрузилась — см. журнал"
        )

    # -- билеты

    def refresh_tickets_tab(self) -> None:
        """Перестроить таблицу арканов из загруженного скрипта и файла билетов."""
        try:
            logic = self.host.get("dota_logic")
        except Exception as exc:
            self.append_log("error", f"таблица билетов недоступна: {exc}")
            return

        self.book = logic.TicketBook.load(str(paths.tickets_file()))
        self.book.min_yield = self.settings.min_ticket_yield

        # Очистить прошлые строки (кроме заголовка).
        for row in self.arcana_rows.values():
            for widget in (row["name"], row["hero"], row["target"], row["state"]):
                widget.setParent(None)
        self.arcana_rows.clear()

        for i, key in enumerate(logic.arcana_order(), start=1):
            name = QLabel(logic.arcana_name_ru(key))
            name.setObjectName("arcana")
            hero = QLineEdit()
            hero.setPlaceholderText("имя героя из секции ×3, например: Фантом Ассасин")
            existing = self.book.heroes_for(key, 3)
            hero.setText(", ".join(existing))
            target = QSpinBox()
            target.setRange(0, 9999)
            target.setSpecialValueText("не фармить")
            target.setValue(int(self.settings.ticket_target.get(key, 0)))
            state = QLabel("✓ готов" if existing else "— не заполнен")

            self.tickets_grid.addWidget(name, i, 0)
            self.tickets_grid.addWidget(hero, i, 1)
            self.tickets_grid.addWidget(target, i, 2)
            self.tickets_grid.addWidget(state, i, 3)
            self.arcana_rows[key] = {"name": name, "hero": hero,
                                     "target": target, "state": state}

        self._update_tickets_status()

    def _update_tickets_status(self) -> None:
        if self.book is None:
            return
        ready = len(self.book.configured(3))
        total = len(self.arcana_rows) or 11
        self.tickets_status.setText(
            f"Заполнено арканов: {ready} из {total}. "
            + ("Можно запускать агента." if ready
               else "Пока не заполнен ни один аркан — агенту не на ком играть.")
        )

    def on_tickets_save(self) -> None:
        if self.book is None:
            return
        for key, row in self.arcana_rows.items():
            names = [n.strip() for n in row["hero"].text().split(",") if n.strip()]
            self.book.set_heroes(key, 3, names)
            row["state"].setText("✓ готов" if names else "— не заполнен")
        self.book.min_yield = self.in_yield.value()
        self.book.save()
        self.settings = self._collect_settings()
        self.settings.save(paths.settings_file())
        self._update_tickets_status()
        self.append_log("success", f"таблица билетов сохранена → {paths.tickets_file()}")
        self.append_log("info", "\n" + self.book.summary())

    # -- управление агентом

    def on_start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.host.healthy:
            QMessageBox.warning(
                self, "Скрипты не загружены",
                "Часть внешних скриптов не загрузилась. Исправьте их во вкладке "
                "«Консоль разработчика», прежде чем запускать агента.")
            return

        self.settings = self._collect_settings()
        self.settings.save(paths.settings_file())

        if self.book is not None and not self.book.configured(self.settings.min_ticket_yield):
            QMessageBox.warning(
                self, "Таблица билетов пуста",
                "Не указан ни один герой с отдачей ×3.\n\n"
                "Откройте вкладку «Билеты», впишите для нужных арканов героя из нижней "
                "секции панели события и нажмите «Сохранить таблицу билетов».")
            self.tabs.setCurrentIndex(1)
            return

        if not self.settings.simulate:
            answer = QMessageBox.question(
                self, "Боевой режим",
                "Режим симуляции ВЫКЛЮЧЕН — агент будет управлять реальными мышью и "
                "клавиатурой.\n\nЗапустить по-настоящему?")
            if answer != QMessageBox.Yes:
                return

        self.worker = AgentWorker(self.host, self.settings, self.bridge)
        self.worker.start()
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.append_log("success", "агент запущен"
                        + (" (СИМУЛЯЦИЯ)" if self.settings.simulate else " (БОЕВОЙ РЕЖИМ)"))

    def on_pause(self) -> None:
        if not self.worker:
            return
        if self.worker.pause_event.is_set():
            self.worker.pause_event.clear()
            self.btn_pause.setText("⏸  Пауза")
            self.append_log("info", "работа возобновлена")
        else:
            self.worker.pause_event.set()
            self.btn_pause.setText("▶  Продолжить")
            self.append_log("warning", "пауза")

    def on_stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.worker.stop_event.set()
            self.worker.pause_event.clear()
            self.append_log("warning", "запрошена остановка…")
        self.btn_stop.setEnabled(False)

    def on_finished(self, summary: dict) -> None:
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸  Пауза")
        self.btn_stop.setEnabled(False)
        self.state_label.setText("ожидание")
        self.append_log("info", f"итоги сессии: {summary}")

    def on_state_changed(self, state: str) -> None:
        self.state_label.setText(RU_STATES.get(state, state))

    def on_cycle_done(self, record) -> None:
        self.cycle_label.setText(f"циклов: {getattr(record, 'index', 0) + 1}")

    def on_stats(self, stats: dict) -> None:
        tickets = stats.get("tickets", {})
        try:
            logic = self.host.get("dota_logic")
            pretty = ", ".join(f"{logic.arcana_name_ru(k)}×{v}" for k, v in tickets.items())
        except Exception:
            pretty = ", ".join(f"{k}×{v}" for k, v in tickets.items())
        self.tickets_label.setText("билеты: " + (pretty or "—"))

    # -- консоль разработчика

    def on_open_script(self) -> None:
        name = self.script_picker.currentText()
        try:
            self.editor.setPlainText(self.host.source_of(name))
            self.append_log("info", f"{name}.py открыт в редакторе")
        except OSError as exc:
            self.append_log("error", f"не удалось прочитать {name}.py: {exc}")

    def on_apply_script(self) -> None:
        name = self.script_picker.currentText()
        source = self.editor.toPlainText()
        if not source.strip():
            self.append_log("warning", "редактор пуст — применять нечего")
            return
        try:
            result = self.host.write_script(name, source, reload=True)
        except Exception as exc:
            self.append_log("error", f"код отклонён: {exc}")
            return
        self.append_log("success" if result.ok else "error",
                        f"{name}.py → {'перезагружен' if result.ok else result.error}")
        self.refresh_script_status()
        self.refresh_tickets_tab()

    def on_reload_all(self) -> None:
        if self.worker and self.worker.is_alive():
            self.append_log("warning", "сначала остановите агента")
            return
        for r in self.host.reload_all():
            self.append_log("success" if r.ok else "error",
                            f"{'✓' if r.ok else '✗'} {r.name}.py {r.error}")
        self.refresh_script_status()
        self.refresh_tickets_tab()

    def on_rollback(self) -> None:
        name = self.script_picker.currentText()
        if self.host.rollback(name):
            self.append_log("success", f"{name}.py откачен и перезагружен")
        self.refresh_script_status()

    # -- OTA

    def on_save_settings(self) -> None:
        self.settings = self._collect_settings()
        self.settings.save(paths.settings_file())
        self.ota = OtaClient(self._ota_config())
        self.append_log("success", f"настройки сохранены → {paths.settings_file()}")

    def on_list_remote(self) -> None:
        self.on_save_settings()
        try:
            names = self.ota.list_remote_scripts()
        except Exception as exc:
            self.ota_output.appendPlainText(f"✗ {exc}")
            return
        self.ota_output.appendPlainText(
            f"скрипты в {self.ota.config.slug}@{self.ota.config.branch}"
            f"/{self.ota.config.path}:\n  " + "\n  ".join(names))

    def on_sync(self) -> None:
        if self.worker and self.worker.is_alive():
            self.append_log("warning", "сначала остановите агента")
            return
        self.on_save_settings()
        self.ota_output.appendPlainText(
            f"→ загрузка из {self.ota.config.slug}@{self.ota.config.branch}…")
        results = self.ota.sync(
            self.host,
            on_log=lambda level, msg: (self.append_log(level, msg),
                                       self.ota_output.appendPlainText(f"  {msg}")),
        )
        changed = sum(1 for r in results if r.changed)
        failed = [r.name for r in results if not r.ok]
        self.ota_output.appendPlainText(
            f"← готово: обновлено {changed}, без изменений "
            f"{len(results) - changed - len(failed)}, с ошибкой {len(failed)} "
            f"{failed if failed else ''}")
        self.refresh_script_status()
        self.refresh_tickets_tab()

    # -- завершение

    def closeEvent(self, event) -> None:  # noqa: N802 - имя из Qt
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
    log.info("Тёмный карнавал v%s запускается (сборка exe: %s)", __version__, paths.is_frozen())
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
