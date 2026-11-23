"""Offline-only test fixtures: never connect to MySQL or send email.

If application dependencies aren't installed in a test runner, install lightweight
import-time substitutes so pure unittest tests still exercise real app logic.
Production code is never modified by these fixtures.
"""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import importlib.util
import sys
import types


def ensure_streamlit_importable():
    if "streamlit" in sys.modules:
        return
    if importlib.util.find_spec("streamlit") is not None:
        return
    st = types.ModuleType("streamlit")
    st.session_state = {}
    for name in (
        "error", "warning", "info", "success", "caption", "markdown", "html",
        "rerun", "button", "container", "columns", "text_input", "selectbox",
        "download_button", "write", "subheader", "checkbox", "form", "form_submit_button",
        "multiselect", "dataframe", "expander", "date_input", "radio", "code",
    ):
        setattr(st, name, Mock())
    sys.modules["streamlit"] = st


class FakeStreamlit:
    """Small widget spy for testing page logic, not Streamlit's renderer."""
    def __init__(self, *, session=None, buttons=None, inputs=None, selects=None, checks=None):
        self.session_state = {} if session is None else session
        self.buttons = {} if buttons is None else buttons
        self.inputs = {} if inputs is None else inputs
        self.selects = {} if selects is None else selects
        self.checks = {} if checks is None else checks
        self.button_calls = []
        self.containers = []
        self.column_specs = []
        self.markdowns = []
        self.errors = []
        self.warnings = []
        self.infos = []
        self.successes = []
        self.downloads = []
        self.reruns = 0
        self.styles = []
        self.grid_events = []
        self.grid_rows = []
        self.column_config = SimpleNamespace(
            NumberColumn=lambda *args, **kwargs: (args, kwargs),
            TextColumn=lambda *args, **kwargs: (args, kwargs),
        )

    def container(self, *args, **kwargs):
        self.containers.append(kwargs.get("key"))
        return nullcontext()

    def columns(self, spec, *args, **kwargs):
        self.column_specs.append((spec, kwargs))
        return [nullcontext() for _ in range(spec if isinstance(spec, int) else len(spec))]

    def button(self, label, *args, **kwargs):
        self.button_calls.append((label, kwargs))
        return bool(self.buttons.get(kwargs.get("key", label), False))

    def markdown(self, value, *args, **kwargs):
        self.markdowns.append(value)

    def html(self, value, *args, **kwargs):
        self.styles.append(value)

    def error(self, message, *args, **kwargs):
        self.errors.append(str(message))

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message))

    def info(self, message, *args, **kwargs):
        self.infos.append(str(message))

    def success(self, message, *args, **kwargs):
        self.successes.append(str(message))

    def caption(self, *args, **kwargs):
        pass

    def write(self, *args, **kwargs):
        pass

    def subheader(self, *args, **kwargs):
        pass

    def rerun(self):
        self.reruns += 1

    def text_input(self, label, *args, **kwargs):
        key = kwargs.get("key")
        return self.inputs.get(key, self.session_state.get(key, kwargs.get("value", "")))

    def selectbox(self, label, options, *args, **kwargs):
        key = kwargs.get("key")
        index = kwargs.get("index", 0)
        default = options[index] if options else None
        return self.selects.get(key, self.session_state.get(key, default))

    def expander(self, *args, **kwargs):
        return nullcontext()

    def date_input(self, label, *args, **kwargs):
        key = kwargs.get("key", label)
        return self.inputs.get(key, self.session_state.get(key, kwargs.get("value")))

    def radio(self, label, options, *args, **kwargs):
        key = kwargs.get("key", label)
        return self.selects.get(key, self.session_state.get(key, options[0]))

    def code(self, value, *args, **kwargs):
        self.markdowns.append(str(value))

    def checkbox(self, label, *args, **kwargs):
        key = kwargs.get("key", label)
        return bool(self.checks.get(key, self.session_state.get(key, kwargs.get("value", False))))

    def form(self, *args, **kwargs):
        return nullcontext()

    def form_submit_button(self, label, *args, **kwargs):
        return self.button(label, *args, **kwargs)

    def multiselect(self, label, options, *args, **kwargs):
        key = kwargs.get("key", label)
        return self.selects.get(key, self.session_state.get(key, kwargs.get("default", [])))

    def dataframe(self, data, **kwargs):
        self.grid_events.append((data, kwargs))
        return SimpleNamespace(selection=SimpleNamespace(rows=self.grid_rows))

    def download_button(self, label, *args, **kwargs):
        self.downloads.append((label, kwargs))
        return False


class ScriptedConnection:
    """Sequence-checked fake SQL connection with explicit cursor lifecycle."""
    def __init__(self, replies, *, fail_sql=None):
        self.replies = iter(replies)
        self.fail_sql = fail_sql
        self.statements = []
        self.cursors = []
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.started = 0

    def cursor(self, **kwargs):
        cur = ScriptedCursor(self, kwargs)
        self.cursors.append(cur)
        return cur

    def close(self):
        self.closed = True

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def start_transaction(self, **kwargs):
        self.started += 1


class ScriptedCursor:
    def __init__(self, connection, options):
        self.connection = connection
        self.options = options
        self.rows = []
        self.closed = False

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))
        if self.connection.fail_sql and self.connection.fail_sql in sql:
            raise RuntimeError("Synthetic database failure")
        try:
            self.rows = list(next(self.connection.replies))
        except StopIteration:
            raise AssertionError("Unexpected query without a scripted reply: " + sql[:150])

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        self.closed = True
