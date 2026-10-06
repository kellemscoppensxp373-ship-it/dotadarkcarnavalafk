"""A tiny, behaviour-preserving PySide6 stub.

The GUI shell must be verifiable on any machine — CI runners and this test suite have no
display, no libGL and no Qt platform plugin. Rather than skip ``main.py`` (the single
most important file to keep working), we install a stub that implements just enough Qt
semantics for the real ``MainWindow`` to be constructed and driven:

* ``Signal`` really connects and really emits (so the worker→GUI wiring is exercised),
* the widgets that hold *state* (editors, line edits, spin boxes, check boxes, lists)
  store and return it for real,
* everything else degrades to a permissive no-op object.

This catches the failure modes that matter — a renamed slot, a typo'd attribute, a
signal connected to a method that no longer exists — without needing a screen.
"""

from __future__ import annotations

import sys
import types


class _Signal:
    """Descriptor that hands out a per-instance bound signal."""

    def __init__(self, *types_):
        self._types = types_
        self._name = f"_sig_{id(self)}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        bound = obj.__dict__.get(self._name)
        if bound is None:
            bound = _BoundSignal()
            obj.__dict__[self._name] = bound
        return bound


class _BoundSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot=None):
        self.slots = [] if slot is None else [s for s in self.slots if s is not slot]

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _Enum:
    """Stands in for Qt's scoped enums (``Qt.Checked``, ``Qt.UserRole``, …)."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, item):
        value = _Enum(f"{self._name}.{item}")
        setattr(self, item, value)
        return value

    def __eq__(self, other):
        return isinstance(other, _Enum) and other._name == self._name

    def __hash__(self):
        return hash(self._name)

    def __or__(self, other):
        return self

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<{self._name}>"


class _WidgetMeta(type):
    """Unknown *class* attributes resolve to enum members (``QListWidget.NoSelection``)."""

    def __getattr__(cls, item):
        if item.startswith("__"):
            raise AttributeError(item)
        value = _Enum(f"{cls.__name__}.{item}")
        setattr(cls, item, value)
        return value


class _Widget(metaclass=_WidgetMeta):
    """Permissive base widget: unknown methods become recording no-ops."""

    def __init__(self, *args, **kwargs):
        self._children = []
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def _noop(*args, **kwargs):
            return None

        return _noop


class _TextEdit(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._text = ""

    def setPlainText(self, text):
        self._text = text

    def toPlainText(self):
        return self._text

    def appendPlainText(self, text):
        self._text += (("\n" if self._text else "") + text)

    def appendHtml(self, text):
        self._text += (("\n" if self._text else "") + text)

    def clear(self):
        self._text = ""


class _LineEdit(_Widget):
    Password = _Enum("EchoMode.Password")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._text = ""

    def setText(self, text):
        self._text = str(text)

    def text(self):
        return self._text


class _ComboBox(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items: list[str] = []
        self._current = ""

    def addItems(self, items):
        self._items.extend(items)
        if not self._current and self._items:
            self._current = self._items[0]

    def addItem(self, item):
        self.addItems([item])

    def setCurrentText(self, text):
        self._current = text

    def currentText(self):
        return self._current


class _SpinBox(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._value = 0

    def setValue(self, value):
        self._value = value

    def value(self):
        return self._value


class _CheckBox(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._checked = False

    def setChecked(self, state):
        self._checked = bool(state)

    def isChecked(self):
        return self._checked


class _ListWidgetItem(_Widget):
    NoSelection = _Enum("NoSelection")

    def __init__(self, text="", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._text = text
        self._data = {}
        self._check = None
        self._flags = _Enum("flags")

    def setData(self, role, value):
        self._data[role] = value

    def data(self, role):
        return self._data.get(role)

    def setCheckState(self, state):
        self._check = state

    def checkState(self):
        return self._check

    def flags(self):
        return self._flags

    def setFlags(self, flags):
        self._flags = flags

    def text(self):
        return self._text


class _ListWidget(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items: list[_ListWidgetItem] = []

    def addItem(self, item):
        self._items.append(item)

    def clear(self):
        self._items = []

    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]


class _Button(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.clicked = _BoundSignal()
        self.activated = _BoundSignal()
        self._enabled = True
        self._label = args[0] if args else ""

    def setEnabled(self, flag):
        self._enabled = bool(flag)

    def isEnabled(self):
        return self._enabled

    def setText(self, text):
        self._label = text

    def click(self):
        self.clicked.emit()


class _MessageBox(_Widget):
    Yes = _Enum("MessageBox.Yes")
    No = _Enum("MessageBox.No")
    answer = Yes

    @classmethod
    def question(cls, *args, **kwargs):
        return cls.answer

    @classmethod
    def warning(cls, *args, **kwargs):
        return cls.Yes

    @classmethod
    def information(cls, *args, **kwargs):
        return cls.Yes


class _QTimer(_Widget):
    @staticmethod
    def singleShot(_ms, callback):
        """Run deferred callbacks synchronously — tests stay deterministic."""
        return None


class _Application(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def exec(self):
        return 0


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)

    def __getattr__(item, _mod=mod):           # anything else → generic widget class
        cls = type(item, (_Widget,), {})
        setattr(_mod, item, cls)
        return cls

    mod.__getattr__ = __getattr__              # type: ignore[attr-defined]
    return mod


class _ProgressBar(_Widget):
    """Минимальная модель QProgressBar: диапазон, значение и видимость."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._min, self._max, self._value = 0, 100, 0
        self._visible = True

    def setRange(self, lo, hi):
        self._min, self._max = lo, hi

    def setValue(self, value):
        self._value = value

    def value(self):
        return self._value

    def maximum(self):
        return self._max

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False

    def isVisible(self):
        return self._visible


class _Label(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._label = args[0] if args and isinstance(args[0], str) else ""

    def setText(self, text):
        self._label = str(text)

    def text(self):
        return self._label


def install() -> None:
    """Register the stub under ``PySide6`` in ``sys.modules``."""
    qt_core = _module(
        "PySide6.QtCore",
        QObject=type("QObject", (_Widget,), {}),
        Qt=_Enum("Qt"),
        QTimer=_QTimer,
        Signal=_Signal,
        Slot=lambda *a, **k: (lambda f: f),
    )
    qt_gui = _module(
        "PySide6.QtGui",
        QAction=type("QAction", (_Widget,), {}),
        QColor=type("QColor", (_Widget,), {}),
        QFont=type("QFont", (_Widget,), {}),
        QKeySequence=type("QKeySequence", (_Widget,), {}),
        QShortcut=_Button,
        QTextCursor=_Enum("QTextCursor"),
    )
    qt_widgets = _module(
        "PySide6.QtWidgets",
        QApplication=_Application,
        QCheckBox=_CheckBox,
        QComboBox=_ComboBox,
        QDoubleSpinBox=_SpinBox,
        QLineEdit=_LineEdit,
        QListWidget=_ListWidget,
        QListWidgetItem=_ListWidgetItem,
        QLabel=_Label,
        QMainWindow=type("QMainWindow", (_Widget,), {}),
        QMessageBox=_MessageBox,
        QPlainTextEdit=_TextEdit,
        QProgressBar=_ProgressBar,
        QPushButton=_Button,
        QSpinBox=_SpinBox,
    )
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qt_core          # type: ignore[attr-defined]
    pyside.QtGui = qt_gui            # type: ignore[attr-defined]
    pyside.QtWidgets = qt_widgets    # type: ignore[attr-defined]

    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qt_core
    sys.modules["PySide6.QtGui"] = qt_gui
    sys.modules["PySide6.QtWidgets"] = qt_widgets
