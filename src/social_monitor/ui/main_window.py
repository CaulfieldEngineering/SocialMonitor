"""Main application window — Feed tab + Settings tab with sidebar nav."""

from __future__ import annotations

import logging
import webbrowser
from datetime import datetime
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from social_monitor.config import AppConfig, SourceInstanceConfig, save_config
from social_monitor.sources import SOURCE_REGISTRY
from social_monitor.sources.base import AccessMethod, ConfigField
from social_monitor.ui.widgets import KeywordListEditor, StringListEditor

if TYPE_CHECKING:
    from social_monitor.app import SocialMonitorApp
    from social_monitor.ui.signals import JsonScoredPost

logger = logging.getLogger(__name__)
MAX_FEED_ROWS = 500


# ═══════════════════════════════════════════════════════════════════
# ConfigField helpers
# ═══════════════════════════════════════════════════════════════════

def _create_field_widget(field: ConfigField, value: Any) -> QWidget:
    if field.field_type == "str":
        w = QLineEdit(str(value) if value else ""); w.setPlaceholderText(field.placeholder); return w
    elif field.field_type == "password":
        w = QLineEdit(str(value) if value else ""); w.setEchoMode(QLineEdit.EchoMode.Password)
        w.setPlaceholderText(field.placeholder); return w
    elif field.field_type == "int":
        w = QSpinBox(); w.setRange(0, 999999); w.setValue(int(value) if value else 0); return w
    elif field.field_type == "str_list":
        w = StringListEditor(field.placeholder); w.set_items(value if isinstance(value, list) else []); return w
    elif field.field_type == "int_list":
        w = StringListEditor(field.placeholder); w.set_items([str(x) for x in value] if isinstance(value, list) else []); return w
    elif field.field_type == "text":
        w = QPlainTextEdit(str(value) if value else ""); w.setPlaceholderText(field.placeholder); w.setMaximumHeight(100); return w
    return QLineEdit(str(value) if value else "")

def _read_field_widget(field: ConfigField, widget: QWidget) -> Any:
    if field.field_type in ("str", "password"): return widget.text()
    elif field.field_type == "int": return widget.value()
    elif field.field_type == "str_list": return widget.get_items()
    elif field.field_type == "int_list": return [int(x) for x in widget.get_items() if x.isdigit()]
    elif field.field_type == "text": return widget.toPlainText()
    return widget.text()

def _add_field_row(form: QFormLayout, field: ConfigField, widget: QWidget) -> None:
    if field.help_text:
        c = QVBoxLayout(); c.setContentsMargins(0,0,0,0); c.addWidget(widget)
        h = QLabel(field.help_text); h.setStyleSheet("color:gray;font-size:11px;"); h.setWordWrap(True)
        c.addWidget(h); w = QWidget(); w.setLayout(c); form.addRow(field.label + ":", w)
    else:
        form.addRow(field.label + ":", widget)


# ═══════════════════════════════════════════════════════════════════
# Method panel + Source config form
# ═══════════════════════════════════════════════════════════════════

class MethodPanel(QWidget):
    def __init__(self, method: AccessMethod, settings: dict, parent=None):
        super().__init__(parent)
        self._fw: list[tuple[ConfigField, QWidget]] = []
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        desc = QLabel(method.description)
        desc.setStyleSheet("color:#555;font-style:italic;margin-bottom:4px;"); desc.setWordWrap(True)
        layout.addWidget(desc)
        if method.fields:
            form = QFormLayout()
            for f in method.fields:
                w = _create_field_widget(f, settings.get(f.key, f.default))
                self._fw.append((f, w)); _add_field_row(form, f, w)
            layout.addLayout(form)
    def collect(self) -> dict[str, Any]:
        return {f.key: _read_field_widget(f, w) for f, w in self._fw}


class _SourceInnerForm(QWidget):
    """Inner form for a specific source type — rebuilt when type changes."""

    def __init__(self, source_type: str, src_cfg: SourceInstanceConfig, parent=None):
        super().__init__(parent)
        self._type = source_type
        self._common_fw: list[tuple[ConfigField, QWidget]] = []
        self._mc = None
        self._mp: list[MethodPanel] = []
        self._methods: list[AccessMethod] = []

        source_cls = SOURCE_REGISTRY.get(source_type)
        if not source_cls:
            QVBoxLayout(self).addWidget(QLabel(f"Unknown type: {source_type}"))
            return

        self._methods = source_cls.supported_methods()
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)

        # Common fields for this type
        if source_cls.common_fields():
            form = QFormLayout()
            for f in source_cls.common_fields():
                w = _create_field_widget(f, src_cfg.settings.get(f.key, f.default))
                self._common_fw.append((f, w)); _add_field_row(form, f, w)
            layout.addLayout(form)

        # Access method
        if len(self._methods) > 1:
            layout.addWidget(QLabel(""))
            lbl = QLabel("Access Method"); lbl.setStyleSheet("font-weight:bold;"); layout.addWidget(lbl)
            self._mc = QComboBox()
            for m in self._methods: self._mc.addItem(m.label, m.key)
            layout.addWidget(self._mc)
            self._ms = QStackedWidget()
            for m in self._methods:
                p = MethodPanel(m, src_cfg.settings); self._mp.append(p); self._ms.addWidget(p)
            layout.addWidget(self._ms)
            self._mc.currentIndexChanged.connect(self._ms.setCurrentIndex)
            cur = src_cfg.method or source_cls.default_method()
            for i, m in enumerate(self._methods):
                if m.key == cur: self._mc.setCurrentIndex(i); break
        elif self._methods:
            self._mp = [MethodPanel(self._methods[0], src_cfg.settings)]
            if self._methods[0].fields:
                layout.addWidget(self._mp[0])

        # Keywords
        layout.addWidget(QLabel(""))
        kl = QLabel("Keywords (override global)"); kl.setStyleSheet("font-weight:bold;"); layout.addWidget(kl)
        self._kw = KeywordListEditor(); self._kw.set_keywords(src_cfg.keywords)
        layout.addWidget(self._kw)

    def collect_settings(self) -> dict[str, Any]:
        s: dict[str, Any] = {}
        for f, w in self._common_fw: s[f.key] = _read_field_widget(f, w)
        pi = self._mc.currentIndex() if self._mc else (0 if self._mp else -1)
        if 0 <= pi < len(self._mp): s.update(self._mp[pi].collect())
        return s

    def collect_method(self) -> str:
        if self._mc: return self._mc.currentData()
        return self._methods[0].key if self._methods else ""

    def collect_keywords(self) -> list[str]:
        return self._kw.get_keywords()


class SourceConfigForm(QWidget):
    """Source config with a changeable Source Type dropdown that rebuilds the form."""

    def __init__(self, src_cfg: SourceInstanceConfig, on_save=None, parent=None):
        super().__init__(parent)
        self._src_cfg = src_cfg
        self._on_save = on_save

        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,0)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget()
        self._inner_layout = QVBoxLayout(inner)

        # -- Top fields (shared across all types) --
        top = QFormLayout()
        self._name_edit = QLineEdit(src_cfg.name); top.addRow("Name:", self._name_edit)
        self._enabled = QCheckBox("Enabled"); self._enabled.setChecked(src_cfg.enabled); top.addRow(self._enabled)

        # Source type dropdown
        from social_monitor.poller import _import_all_sources
        _import_all_sources()
        self._type_combo = QComboBox()
        for type_key, cls in SOURCE_REGISTRY.items():
            self._type_combo.addItem(cls.display_name, type_key)
        # Set current
        idx = self._type_combo.findData(src_cfg.type)
        if idx >= 0: self._type_combo.setCurrentIndex(idx)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        top.addRow("Source Type:", self._type_combo)

        self._inner_layout.addLayout(top)

        # -- Type-specific inner form (rebuilt on type change) --
        self._inner_form: _SourceInnerForm | None = None
        self._inner_container = QVBoxLayout()
        self._inner_layout.addLayout(self._inner_container)
        self._inner_layout.addStretch()

        self._rebuild_inner_form(src_cfg.type, src_cfg)

        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # Button row
        btn_row = QHBoxLayout()
        test_btn = QPushButton("Test Connection")
        test_btn.setToolTip("Try fetching posts with the current settings")
        test_btn.clicked.connect(self._do_test)
        btn_row.addWidget(test_btn)

        save_btn = QPushButton("Save Source")
        save_btn.setStyleSheet("font-weight:bold; padding:6px;")
        save_btn.clicked.connect(self._do_save)
        btn_row.addWidget(save_btn)
        outer.addLayout(btn_row)

        # Test result label
        self._test_result = QLabel("")
        self._test_result.setWordWrap(True)
        outer.addWidget(self._test_result)

    def _rebuild_inner_form(self, type_key: str, src_cfg: SourceInstanceConfig) -> None:
        """Remove old inner form and build a new one for the given type."""
        if self._inner_form:
            self._inner_container.removeWidget(self._inner_form)
            self._inner_form.deleteLater()
        self._inner_form = _SourceInnerForm(type_key, src_cfg)
        self._inner_container.addWidget(self._inner_form)

    def _on_type_changed(self, index: int) -> None:
        new_type = self._type_combo.currentData()
        # Preserve name/enabled/interval, but reset type-specific settings
        stub = SourceInstanceConfig(
            name=self._name_edit.text(),
            type=new_type,
            enabled=self._enabled.isChecked(),
            interval=120,
        )
        self._rebuild_inner_form(new_type, stub)

    def _do_save(self) -> None:
        if self._on_save:
            self._on_save()

    def _do_test(self) -> None:
        """Test the source by trying to fetch posts with current settings."""
        import asyncio
        cfg = self.collect()
        type_key = cfg.type
        source_cls = SOURCE_REGISTRY.get(type_key)
        if not source_cls:
            self._test_result.setStyleSheet("color:red;")
            self._test_result.setText(f"Unknown source type: {type_key}")
            return

        self._test_result.setStyleSheet("color:#555;")
        self._test_result.setText("Testing...")

        async def _run_test():
            source = source_cls()
            plugin_config = {
                "method": cfg.method,
                "keywords": cfg.keywords,
                "settings": cfg.settings,
            }
            try:
                errors = source.validate_config(plugin_config)
                if errors:
                    return False, f"Config errors: {'; '.join(errors)}"
                await source.setup(plugin_config)
                posts = await source.fetch_new()
                await source.teardown()
                if posts:
                    titles = [p.title[:60] for p in posts[:3]]
                    return True, f"OK — fetched {len(posts)} post(s).\n" + "\n".join(f"  - {t}" for t in titles)
                else:
                    return True, "Connected OK but no new posts found (this may be normal)."
            except Exception as e:
                return False, f"Error: {e}"

        def _on_done(future):
            try:
                ok, msg = future.result()
                self._test_result.setStyleSheet("color:green;" if ok else "color:red;")
                self._test_result.setText(msg)
            except Exception as e:
                self._test_result.setStyleSheet("color:red;")
                self._test_result.setText(f"Test failed: {e}")

        loop = asyncio.get_event_loop()
        task = asyncio.ensure_future(_run_test(), loop=loop)
        task.add_done_callback(_on_done)

    def collect(self) -> SourceInstanceConfig:
        type_key = self._type_combo.currentData()
        s = self._inner_form.collect_settings() if self._inner_form else {}
        mk = self._inner_form.collect_method() if self._inner_form else ""
        kw = self._inner_form.collect_keywords() if self._inner_form else []
        return SourceInstanceConfig(
            name=self._name_edit.text(), type=type_key, method=mk,
            enabled=self._enabled.isChecked(), interval=120,
            keywords=kw, settings=s)


# ═══════════════════════════════════════════════════════════════════
# Feed components
# ═══════════════════════════════════════════════════════════════════

class SortableItem(QTableWidgetItem):
    """Table item that sorts by a numeric value stored in UserRole+3."""
    def __lt__(self, other):
        my_val = self.data(Qt.ItemDataRole.UserRole + 3)
        other_val = other.data(Qt.ItemDataRole.UserRole + 3) if other else None
        if my_val is not None and other_val is not None:
            return my_val < other_val
        return super().__lt__(other)


class FeedTable(QTableWidget):
    COLUMNS = ["", "Time", "Source", "Score", "Title", "Author", "Trigger"]

    UNREAD_FONT = QFont("", -1, QFont.Weight.Bold)
    READ_FONT = QFont("", -1, QFont.Weight.Normal)
    UNREAD_BG = QColor(230, 240, 255)
    READ_BG = QColor(255, 255, 255)

    # Custom data role to store post reference and read state per-row
    POST_ROLE = Qt.ItemDataRole.UserRole + 1
    READ_ROLE = Qt.ItemDataRole.UserRole + 2
    SORT_ROLE = Qt.ItemDataRole.UserRole + 3  # For proper sorting

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLUMNS), parent)
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(False)
        self.setSortingEnabled(True)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(0, 12)
        for i, mode in enumerate([QHeaderView.ResizeMode.ResizeToContents]*3 +
                                  [QHeaderView.ResizeMode.Stretch,
                                   QHeaderView.ResizeMode.ResizeToContents,
                                   QHeaderView.ResizeMode.Stretch], start=1):
            h.setSectionResizeMode(i, mode)
        # Default sort: Time descending
        self.sortItems(1, Qt.SortOrder.DescendingOrder)
        self._post_count = 0

    def add_post(self, post: JsonScoredPost) -> None:
        # Temporarily disable sorting while inserting
        self.setSortingEnabled(False)
        row = self.rowCount()
        self.insertRow(row)

        # Col 0: unread dot
        dot = QTableWidgetItem("\u25cf")
        dot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        dot.setForeground(QColor("#2196F3"))
        dot.setData(self.POST_ROLE, self._post_count)  # Store index
        dot.setData(self.READ_ROLE, False)
        self.setItem(row, 0, dot)

        # Col 1: Time — convert to local time, store epoch for sorting
        ts = post.timestamp
        if isinstance(ts, datetime):
            local_ts = ts.astimezone() if ts.tzinfo else ts
            ti = SortableItem(local_ts.strftime("%m/%d %I:%M %p"))
            ti.setData(self.SORT_ROLE, ts.timestamp())
        else:
            ti = SortableItem(str(ts))
        self.setItem(row, 1, ti)

        # Col 2: Source
        si = QTableWidgetItem(getattr(post, 'source_name', post.source.replace("_"," ").title()))
        self.setItem(row, 2, si)

        # Col 3: Score — store float for sorting
        sci = SortableItem(f"{post.score:.0%}")
        sci.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        sci.setData(self.SORT_ROLE, post.score)
        if post.score >= 0.8: sci.setBackground(QColor(76,175,80,100)); sci.setForeground(QColor(27,94,32))
        elif post.score >= 0.6: sci.setBackground(QColor(255,193,7,100)); sci.setForeground(QColor(245,127,23))
        elif post.score >= 0.3: sci.setBackground(QColor(255,152,0,60))
        else: sci.setForeground(QColor("#999"))
        self.setItem(row, 3, sci)

        # Col 4: Title
        self.setItem(row, 4, QTableWidgetItem(post.title[:120]))

        # Col 5: Author
        self.setItem(row, 5, QTableWidgetItem(post.author))

        # Col 6: Trigger
        tri = QTableWidgetItem(post.trigger_info)
        tri.setForeground(QColor("#666"))
        self.setItem(row, 6, tri)

        # Store post reference on the first column item
        self.item(row, 0).setData(self.POST_ROLE, post)

        # Set tooltips on all cells so truncated text is visible on hover
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item and item.text():
                item.setToolTip(item.text())

        # Apply unread styling
        self._apply_row_style(row, read=False)
        self._post_count += 1

        # Re-enable sorting (will re-sort)
        self.setSortingEnabled(True)

        # Trim
        while self.rowCount() > MAX_FEED_ROWS:
            self.removeRow(self.rowCount() - 1)

    def mark_read(self, row: int) -> None:
        dot = self.item(row, 0)
        if dot and not dot.data(self.READ_ROLE):
            dot.setData(self.READ_ROLE, True)
            self._apply_row_style(row, read=True)

    def mark_all_read(self) -> None:
        for i in range(self.rowCount()):
            dot = self.item(i, 0)
            if dot and not dot.data(self.READ_ROLE):
                dot.setData(self.READ_ROLE, True)
                self._apply_row_style(i, read=True)

    def get_unread_count(self) -> int:
        count = 0
        for i in range(self.rowCount()):
            dot = self.item(i, 0)
            if dot and not dot.data(self.READ_ROLE):
                count += 1
        return count

    def _apply_row_style(self, row: int, read: bool) -> None:
        font = self.READ_FONT if read else self.UNREAD_FONT
        bg = self.READ_BG if read else self.UNREAD_BG
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                if col != 3:
                    item.setBackground(bg)
                item.setFont(font)
        dot = self.item(row, 0)
        if dot:
            if read:
                dot.setText("")
            else:
                dot.setText("\u25cf")
                dot.setForeground(QColor("#2196F3"))

    def get_selected_post(self) -> JsonScoredPost | None:
        r = self.currentRow()
        if r < 0:
            return None
        dot = self.item(r, 0)
        if dot:
            post = dot.data(self.POST_ROLE)
            if isinstance(post, object) and hasattr(post, 'title'):
                return post
        return None


class PostDetailPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self); layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(6)

        # Title row
        self._title = QLabel("Select a post to view details")
        self._title.setFont(QFont("", 13, QFont.Weight.Bold)); self._title.setWordWrap(True)
        layout.addWidget(self._title)

        # Meta row: source | author | time
        self._meta = QLabel("")
        self._meta.setStyleSheet("color: #666; font-size: 12px;"); self._meta.setWordWrap(True)
        layout.addWidget(self._meta)

        # AI Analysis box — prominent, colored by score
        self._ai_box = QLabel("")
        self._ai_box.setWordWrap(True)
        self._ai_box.setMinimumHeight(50)
        self._ai_box.setStyleSheet(
            "background: #f0f4f8; border: 1px solid #ccc; border-radius: 6px; "
            "padding: 10px; font-size: 13px;"
        )
        layout.addWidget(self._ai_box)

        # Post body in a bordered frame
        from PyQt6.QtWidgets import QTextBrowser, QFrame
        body_frame = QFrame()
        body_frame.setFrameShape(QFrame.Shape.StyledPanel)
        body_frame.setStyleSheet(
            "QFrame { background: white; border: 1px solid #ddd; border-radius: 4px; }"
        )
        body_layout = QVBoxLayout(body_frame)
        body_layout.setContentsMargins(8, 8, 8, 8)

        body_label = QLabel("Post Content")
        body_label.setStyleSheet("font-weight: bold; color: #555; border: none; font-size: 11px;")
        body_layout.addWidget(body_label)

        self._body = QTextBrowser()
        self._body.setOpenExternalLinks(True)
        self._body.setStyleSheet("border: none; font-size: 13px;")
        body_layout.addWidget(self._body)

        layout.addWidget(body_frame, stretch=1)

        # Open button
        self._open_btn = QPushButton("Open in Browser")
        self._open_btn.setStyleSheet("padding: 6px; font-weight: bold;")
        self._open_btn.clicked.connect(self._open_url); self._open_btn.setEnabled(False)
        layout.addWidget(self._open_btn)
        self._url = ""

    def show_post(self, post: JsonScoredPost) -> None:
        self._title.setText(post.title)

        # Meta
        sl = getattr(post, 'source_name', post.source.replace("_", " ").title())
        ts = post.timestamp
        if isinstance(ts, datetime):
            local_ts = ts.astimezone() if ts.tzinfo else ts
            tstr = local_ts.strftime("%m/%d/%Y %I:%M:%S %p %Z")
        else:
            tstr = str(ts)
        self._meta.setText(f"{sl}  |  {post.author}  |  {tstr}")

        # AI Analysis box — style by score
        score = post.score
        score_pct = f"{score:.0%}"

        if score >= 0.8:
            bg = "#e8f5e9"; border = "#4CAF50"; icon = "HIGH MATCH"
        elif score >= 0.6:
            bg = "#fff8e1"; border = "#FF9800"; icon = "MATCH"
        elif score >= 0.3:
            bg = "#fff3e0"; border = "#FFB74D"; icon = "LOW"
        else:
            bg = "#f5f5f5"; border = "#ccc"; icon = "NO MATCH"

        self._ai_box.setStyleSheet(
            f"background: {bg}; border: 2px solid {border}; border-radius: 6px; "
            f"padding: 10px; font-size: 13px;"
        )

        explanation = post.explanation or "No AI explanation"
        ai_lines = [
            f"<b>{icon}</b> — Score: <b>{score_pct}</b>",
            f"<br/>{explanation}",
        ]
        self._ai_box.setText("".join(ai_lines))

        # Body
        body = post.body or "(No body text available)"
        if "<" in body and ">" in body:
            self._body.setHtml(body)
        else:
            self._body.setPlainText(body)

        self._url = post.url; self._open_btn.setEnabled(bool(post.url))

    def _open_url(self) -> None:
        if self._url: webbrowser.open(self._url)


# ═══════════════════════════════════════════════════════════════════
# Settings tab — sidebar navigation
# ═══════════════════════════════════════════════════════════════════

class SettingsTab(QWidget):
    """Settings with sidebar: General, Sources, AI Scoring, Keywords."""

    def __init__(self, config: AppConfig, app_controller: SocialMonitorApp | None = None, parent=None):
        super().__init__(parent)
        self.config = config
        self._app = app_controller

        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,0)
        body = QHBoxLayout()

        # Sidebar
        self._nav = QListWidget()
        self._nav.setMaximumWidth(160)
        self._nav.setStyleSheet("QListWidget { font-size: 13px; } QListWidget::item { padding: 8px; }")
        for label in ["General", "Sources", "AI Scoring", "Keywords"]:
            self._nav.addItem(label)
        body.addWidget(self._nav)

        # Stacked pages
        self._pages = QStackedWidget()
        body.addWidget(self._pages, stretch=1)

        self._nav.currentRowChanged.connect(self._pages.setCurrentIndex)

        # Build pages
        self._build_general_page()
        self._build_sources_page()
        self._build_ai_page()
        self._build_keywords_page()

        self._nav.setCurrentRow(0)
        outer.addLayout(body)

        # Status message label
        self._status = QLabel("")
        self._status.setStyleSheet("color: green; font-weight: bold; padding: 4px;")
        outer.addWidget(self._status)

    def _status_msg(self, text: str) -> None:
        self._status.setText(text)
        QTimer.singleShot(4000, lambda: self._status.setText(""))

    # -- General --
    def _build_general_page(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page)
        layout.addWidget(QLabel("<h3>General</h3>"))
        form = QFormLayout()
        self._min = QCheckBox("Start minimized to tray"); self._min.setChecked(self.config.general.start_minimized)
        form.addRow(self._min)
        self._login = QCheckBox("Start on Windows login"); self._login.setChecked(self.config.general.start_on_login)
        form.addRow(self._login)
        self._snd = QCheckBox("Notification sound"); self._snd.setChecked(self.config.general.notification_sound)
        form.addRow(self._snd)

        # Poll interval — use a simple line edit to avoid the broken QSpinBox arrows
        poll_row = QHBoxLayout()
        self._poll_interval = QLineEdit(str(self.config.general.poll_interval))
        self._poll_interval.setMaximumWidth(80)
        poll_row.addWidget(self._poll_interval)
        poll_row.addWidget(QLabel("seconds (applies to all sources)"))
        poll_row.addStretch()
        form.addRow("Poll interval:", poll_row)

        self._ll = QComboBox(); self._ll.addItems(["DEBUG","INFO","WARNING","ERROR"])
        self._ll.setCurrentText(self.config.general.log_level); form.addRow("Log level:", self._ll)
        layout.addLayout(form)

        # Debug tools
        layout.addWidget(QLabel(""))
        layout.addWidget(QLabel("<b>Database</b>"))
        clear_db_btn = QPushButton("Clear Database (reset all seen posts)")
        clear_db_btn.setStyleSheet("color: red;")
        clear_db_btn.clicked.connect(self._clear_database)
        layout.addWidget(clear_db_btn)

        layout.addStretch()
        save_btn = QPushButton("Save"); save_btn.setStyleSheet("font-weight:bold; padding:6px;")
        save_btn.clicked.connect(self.save); layout.addWidget(save_btn)
        self._pages.addWidget(page)

    def _clear_database(self) -> None:
        if QMessageBox.question(self, "Clear Database",
            "Delete ALL seen posts from the database and restart sources?\n\n"
            "This resets everything so the next poll will re-fetch all posts."
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._app:
            import asyncio
            async def _do_clear():
                count = await self._app.db.clear_all()
                return count
            def _on_cleared(f):
                self._status_msg(f"Cleared {f.result()} posts. Restarting sources...")
                # Clear the feed display
                if self._app and self._app._main_window:
                    self._app._main_window._on_clear_feed()
                # Reload poller to reset in-memory seen caches
                self._app.reload_settings()
            loop = asyncio.get_event_loop()
            future = asyncio.ensure_future(_do_clear(), loop=loop)
            future.add_done_callback(_on_cleared)
        else:
            self._status_msg("No app reference — cannot clear.")

    # -- Sources --
    def _build_sources_page(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page)
        layout.addWidget(QLabel("<h3>Sources</h3>"))

        top = QHBoxLayout()
        # Source list (left)
        left = QVBoxLayout()
        self._source_list = QListWidget()
        self._source_list.currentRowChanged.connect(self._on_source_selected)
        left.addWidget(self._source_list)
        br = QHBoxLayout()
        ab = QPushButton("+ Add"); ab.clicked.connect(self._add_source); br.addWidget(ab)
        bb = QPushButton("Bulk Add"); bb.clicked.connect(self._bulk_add_sources); br.addWidget(bb)
        rb = QPushButton("- Remove"); rb.clicked.connect(self._remove_source); br.addWidget(rb)
        left.addLayout(br)
        top.addLayout(left, stretch=1)

        # Source config (right) — stacked forms
        self._source_stack = QStackedWidget()
        top.addWidget(self._source_stack, stretch=2)

        layout.addLayout(top)

        # Populate
        self._source_forms: list[SourceConfigForm] = []
        for sc in self.config.sources:
            self._add_source_ui(sc)
        if not self.config.sources:
            ph = QLabel("No sources configured.\nClick '+ Add' to get started.")
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._source_stack.addWidget(ph)

        # Select first source
        if self._source_forms:
            self._source_list.setCurrentRow(0)

        self._pages.addWidget(page)

    def _on_source_selected(self, row: int) -> None:
        if 0 <= row < len(self._source_forms):
            self._source_stack.setCurrentWidget(self._source_forms[row])

    def _add_source_ui(self, sc: SourceInstanceConfig) -> None:
        f = SourceConfigForm(sc, on_save=self.save)
        self._source_forms.append(f)
        self._source_stack.addWidget(f)
        d = sc.name or f"{sc.type} (unnamed)"
        if not sc.enabled: d += " [disabled]"
        self._source_list.addItem(QListWidgetItem(d))

    def _add_source(self) -> None:
        from social_monitor.poller import _import_all_sources
        _import_all_sources()
        tl = {f"{c.display_name} — {c.description}": n for n, c in SOURCE_REGISTRY.items()}
        label, ok = QInputDialog.getItem(self, "Add Source", "Select source type:", list(tl.keys()), editable=False)
        if not ok: return
        st = tl[label]; sc = SOURCE_REGISTRY[st]
        name, ok = QInputDialog.getText(self, "Name", f"Name this {sc.display_name}:", text=sc.display_name)
        if not ok or not name: return
        cfg = SourceInstanceConfig(name=name, type=st, method=sc.default_method(), enabled=True, interval=sc.default_interval)
        # Remove placeholder if present
        if not self._source_forms:
            while self._source_stack.count(): self._source_stack.removeWidget(self._source_stack.widget(0))
        self._add_source_ui(cfg)
        self._source_list.setCurrentRow(self._source_list.count()-1)
        self.save()

    def _bulk_add_sources(self) -> None:
        """Add multiple subreddit sources at once — one entry per name."""
        from social_monitor.poller import _import_all_sources
        _import_all_sources()

        text, ok = QInputDialog.getMultiLineText(
            self, "Bulk Add Subreddits",
            "Enter subreddit names (one per line, without r/):\n\n"
            "Example:\n  AudioEngineering\n  LiveSound\n  WeAreTheMusicMakers",
            "",
        )
        if not ok or not text.strip():
            return

        names = [n.strip().strip("/").removeprefix("r/") for n in text.strip().split("\n") if n.strip()]
        if not names:
            return

        # Remove placeholder if this is the first source
        if not self._source_forms:
            while self._source_stack.count():
                self._source_stack.removeWidget(self._source_stack.widget(0))

        source_cls = SOURCE_REGISTRY.get("subreddit")
        for name in names:
            cfg = SourceInstanceConfig(
                name=f"r/{name}",
                type="subreddit",
                method=source_cls.default_method() if source_cls else "rss",
                enabled=True,
                interval=120,
                settings={"subreddit": name},
            )
            self._add_source_ui(cfg)

        self._source_list.setCurrentRow(self._source_list.count() - 1)
        self.save()
        self._status_msg(f"Added {len(names)} subreddit(s) and saved.")

    def _remove_source(self) -> None:
        r = self._source_list.currentRow()
        if r < 0: return
        name = self._source_list.item(r).text()
        if QMessageBox.question(self, "Remove", f"Remove '{name}'?") != QMessageBox.StandardButton.Yes: return
        self._source_list.takeItem(r)
        f = self._source_forms.pop(r)
        self._source_stack.removeWidget(f); f.deleteLater()
        self.save()
        self._status_msg(f"Removed '{name}' and saved.")

    # -- AI Scoring --
    def _build_ai_page(self) -> None:
        from social_monitor.config import DEFAULT_AI_PROMPT
        from social_monitor.scorer import OPENROUTER_MODELS

        page = QWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); layout = QVBoxLayout(inner)

        layout.addWidget(QLabel("<h3>AI Scoring</h3>"))
        layout.addWidget(QLabel(
            "When enabled, new posts are sent to an AI model for semantic relevance scoring.\n"
            "When disabled ('none'), posts are scored by keyword matching only."
        ))

        c = self.config.ai
        form = QFormLayout()

        # Provider
        self._ai_provider = QComboBox()
        self._ai_provider.addItems(["none", "claude", "openai", "openrouter"])
        self._ai_provider.setCurrentText(c.provider)
        form.addRow("Provider:", self._ai_provider)

        # API Key
        self._ai_key = QLineEdit(c.api_key); self._ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_key.setPlaceholderText("API key"); form.addRow("API Key:", self._ai_key)

        # Model — combo with presets + editable for custom
        self._ai_model = QComboBox(); self._ai_model.setEditable(True)
        # Add presets
        model_presets = [
            ("claude-sonnet-4-6", "Claude Sonnet 4.6 (direct Anthropic API)"),
            ("gpt-4o-mini", "GPT-4o Mini (direct OpenAI API)"),
            ("gpt-4o", "GPT-4o (direct OpenAI API)"),
        ]
        for model_id, label in model_presets:
            self._ai_model.addItem(f"{label}  ({model_id})", model_id)
        self._ai_model.insertSeparator(len(model_presets))
        # OpenRouter models
        for model_id, label in OPENROUTER_MODELS:
            self._ai_model.addItem(f"{label}  ({model_id})", model_id)

        # Set current model
        found = False
        for i in range(self._ai_model.count()):
            if self._ai_model.itemData(i) == c.model:
                self._ai_model.setCurrentIndex(i); found = True; break
        if not found:
            self._ai_model.setEditText(c.model)
        form.addRow("Model:", self._ai_model)

        # Threshold
        tr = QHBoxLayout()
        self._ai_thresh = QSlider(Qt.Orientation.Horizontal); self._ai_thresh.setRange(0,100)
        self._ai_thresh.setValue(int(c.threshold*100))
        self._thresh_lbl = QLabel(f"{c.threshold:.2f}")
        self._ai_thresh.valueChanged.connect(lambda v: self._thresh_lbl.setText(f"{v/100:.2f}"))
        tr.addWidget(self._ai_thresh); tr.addWidget(self._thresh_lbl)
        form.addRow("Notification threshold:", tr)
        form.addRow(QLabel("Posts scoring above this threshold trigger a desktop notification."))

        # Interests
        self._ai_interests = QPlainTextEdit(c.interests)
        self._ai_interests.setPlaceholderText(
            "Describe your interests and expertise.\n"
            "Example: Audio software development, VST/AU plugin development,\n"
            "music production tools, DSP programming, JUCE framework")
        self._ai_interests.setMaximumHeight(100); form.addRow("Interests:", self._ai_interests)

        layout.addLayout(form)

        # Filter toggles
        layout.addWidget(QLabel(""))
        layout.addWidget(QLabel("<b>Scoring Filters</b>"))
        layout.addWidget(QLabel("These get injected into the AI prompt as additional scoring rules."))
        self._ai_prefer_q = QCheckBox("Boost questions and help requests")
        self._ai_prefer_q.setChecked(c.prefer_questions); layout.addWidget(self._ai_prefer_q)
        self._ai_prefer_unans = QCheckBox("Boost unanswered posts (early engagement)")
        self._ai_prefer_unans.setChecked(c.prefer_unanswered); layout.addWidget(self._ai_prefer_unans)
        self._ai_exclude_promo = QCheckBox("Penalize self-promotion and spam")
        self._ai_exclude_promo.setChecked(c.exclude_self_promo); layout.addWidget(self._ai_exclude_promo)

        # Editable prompt
        layout.addWidget(QLabel(""))
        layout.addWidget(QLabel("<b>AI Prompt</b>"))
        layout.addWidget(QLabel(
            "The system prompt sent to the AI. Uses {interests}, {keywords}, and {filters} placeholders.\n"
            "Leave empty to use the default prompt. Edit to customize scoring behavior."))
        self._ai_prompt = QPlainTextEdit(c.prompt)
        self._ai_prompt.setPlaceholderText("(Using default prompt — edit to customize)")
        self._ai_prompt.setMinimumHeight(150)
        layout.addWidget(self._ai_prompt)

        reset_btn = QPushButton("Reset to Default Prompt")
        reset_btn.clicked.connect(lambda: self._ai_prompt.setPlainText(DEFAULT_AI_PROMPT))
        layout.addWidget(reset_btn)

        save_btn = QPushButton("Save"); save_btn.setStyleSheet("font-weight:bold; padding:6px;")
        save_btn.clicked.connect(self.save); layout.addWidget(save_btn)
        layout.addStretch()
        scroll.setWidget(inner)
        page_layout = QVBoxLayout(page); page_layout.setContentsMargins(0,0,0,0)
        page_layout.addWidget(scroll)
        self._pages.addWidget(page)

    # -- Keywords --
    def _build_keywords_page(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page)
        layout.addWidget(QLabel("<h3>Keywords</h3>"))
        layout.addWidget(QLabel("Global keywords apply to all sources unless overridden per-source."))

        layout.addWidget(QLabel("<b>Global Keywords</b>"))
        self._gk = KeywordListEditor(); self._gk.set_keywords(self.config.global_keywords)
        layout.addWidget(self._gk)

        layout.addWidget(QLabel("<b>Negative Keywords</b> (posts containing these are excluded)"))
        self._nk = KeywordListEditor(); self._nk.set_keywords(self.config.negative_keywords)
        layout.addWidget(self._nk)
        layout.addStretch()
        save_btn = QPushButton("Save"); save_btn.setStyleSheet("font-weight:bold; padding:6px;")
        save_btn.clicked.connect(self.save); layout.addWidget(save_btn)
        self._pages.addWidget(page)

    # -- Save --
    def save(self) -> None:
        self.config.sources = [f.collect() for f in self._source_forms]
        self.config.ai.provider = self._ai_provider.currentText()
        self.config.ai.api_key = self._ai_key.text()
        # Model: use itemData if a preset was selected, otherwise use the text
        idx = self._ai_model.currentIndex()
        model_data = self._ai_model.itemData(idx)
        self.config.ai.model = model_data if model_data else self._ai_model.currentText()
        self.config.ai.threshold = self._ai_thresh.value() / 100.0
        self.config.ai.interests = self._ai_interests.toPlainText()
        self.config.ai.prompt = self._ai_prompt.toPlainText()
        self.config.ai.prefer_questions = self._ai_prefer_q.isChecked()
        self.config.ai.prefer_unanswered = self._ai_prefer_unans.isChecked()
        self.config.ai.exclude_self_promo = self._ai_exclude_promo.isChecked()
        self.config.global_keywords = self._gk.get_keywords()
        self.config.negative_keywords = self._nk.get_keywords()
        self.config.general.start_minimized = self._min.isChecked()
        self.config.general.start_on_login = self._login.isChecked()
        self.config.general.notification_sound = self._snd.isChecked()
        try:
            self.config.general.poll_interval = max(30, int(self._poll_interval.text()))
        except ValueError:
            pass
        self.config.general.log_level = self._ll.currentText()
        save_config(self.config)
        logger.info("Settings saved")

        # Hot-reload: restart poller with new config
        if self._app:
            self._app.reload_settings()

        # Update source list display names
        for i, form in enumerate(self._source_forms):
            cfg = form.collect()
            d = cfg.name or f"{cfg.type} (unnamed)"
            if not cfg.enabled: d += " [disabled]"
            self._source_list.item(i).setText(d)

        self._status_msg("Settings saved and applied.")


# ═══════════════════════════════════════════════════════════════════
# Main Window
# ═══════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    """Main window with Feed and Settings tabs, minimize to tray."""

    def __init__(self, app_controller: SocialMonitorApp | None = None, parent=None):
        super().__init__(parent)
        self._app = app_controller
        self.setWindowTitle("SocialMonitor")
        self.setMinimumSize(1000, 600)
        self.resize(1200, 700)

        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        # -- Feed tab --
        feed_widget = QWidget()
        feed_layout = QVBoxLayout(feed_widget); feed_layout.setContentsMargins(4, 4, 4, 0)

        toolbar = QHBoxLayout()
        chk = QPushButton("Check Now"); chk.clicked.connect(self._on_check_now); toolbar.addWidget(chk)
        self._pause_btn = QPushButton("Pause"); self._pause_btn.setCheckable(True)
        self._pause_btn.clicked.connect(self._on_toggle_pause); toolbar.addWidget(self._pause_btn)
        mar = QPushButton("Mark All Read"); mar.clicked.connect(self._on_mark_all_read); toolbar.addWidget(mar)
        clr = QPushButton("Clear Feed"); clr.clicked.connect(self._on_clear_feed); toolbar.addWidget(clr)
        toolbar.addStretch()

        # Time filter
        toolbar.addWidget(QLabel("Show:"))
        self._time_filter = QComboBox()
        self._time_filter.addItems(["All", "Today", "This Week", "Unread Only", "Matches Only"])
        self._time_filter.currentTextChanged.connect(self._apply_time_filter)
        toolbar.addWidget(self._time_filter)

        feed_layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._feed = FeedTable()
        self._feed.currentCellChanged.connect(self._on_row_changed)
        splitter.addWidget(self._feed)
        self._detail = PostDetailPanel()
        splitter.addWidget(self._detail)
        splitter.setSizes([600, 400])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        feed_layout.addWidget(splitter)
        self._tabs.addTab(feed_widget, "Feed")

        # -- Settings tab --
        if app_controller:
            self._settings_tab = SettingsTab(app_controller.config, app_controller=app_controller)
            self._tabs.addTab(self._settings_tab, "Settings")

        # -- Status bar --
        sb = QStatusBar(); self.setStatusBar(sb)
        self._ai_lbl = QLabel(); self._src_lbl = QLabel(); self._cnt_lbl = QLabel("Posts: 0")
        sb.addWidget(self._ai_lbl, stretch=2); sb.addWidget(self._src_lbl, stretch=2)
        sb.addPermanentWidget(self._cnt_lbl)
        self._total = 0
        self._last_refresh: datetime | None = None

    # -- Public API --

    def add_post(self, post: JsonScoredPost) -> None:
        self._feed.add_post(post); self._total += 1
        self._update_count_label()

    def load_history(self, matches: list[dict]) -> None:
        from social_monitor.ui.signals import JsonScoredPost as JSP
        from social_monitor.models import Post, ScoredPost
        from datetime import timezone
        for m in reversed(matches):
            ts = m.get("timestamp")
            if isinstance(ts, str):
                try: ts = datetime.fromisoformat(ts)
                except: ts = datetime.now(timezone.utc)
            source_name = m.get("source_name", "") or m.get("source", "").replace("_", " ").title()
            post = Post(source=m.get("source",""), post_id=m.get("post_id",""),
                title=m.get("title",""), body=m.get("body_preview","") or "",
                author=m.get("author",""), url=m.get("url",""),
                timestamp=ts or datetime.now(timezone.utc),
                metadata={"source_name": source_name})
            sp = ScoredPost(post=post, score=m.get("score") or 0, explanation=m.get("explanation",""))
            trigger = m.get("explanation","")
            if m.get("notified"): trigger += " | NOTIFIED"
            self._feed.add_post(JSP(sp, trigger))
            self._total += 1
        self._update_count_label()

    def set_ai_status(self, status: str) -> None: self._ai_lbl.setText(status)

    def set_source_status(self, name: str, count: int) -> None:
        if count > 0:
            self._src_lbl.setText(f"{name}: +{count} new")
        else:
            self._src_lbl.setText(name)
        self._last_refresh = datetime.now()
        refresh_text = f"Last refreshed: {self._last_refresh.strftime('%I:%M:%S %p')}"
        QTimer.singleShot(3000, lambda t=refresh_text: self._src_lbl.setText(t))

    # -- Handlers --

    def _on_check_now(self) -> None:
        if self._app: self._app.check_now()

    def _on_toggle_pause(self, checked: bool) -> None:
        if self._app:
            if checked: self._app.pause_monitoring(); self._pause_btn.setText("Resume")
            else: self._app.resume_monitoring(); self._pause_btn.setText("Pause")

    def _on_mark_all_read(self) -> None:
        self._feed.mark_all_read()
        self._update_count_label()

    def _on_clear_feed(self) -> None:
        self._feed.setRowCount(0); self._feed._post_count = 0
        self._total = 0; self._cnt_lbl.setText("Posts: 0")

    def _on_row_changed(self, row, col, pr, pc) -> None:
        p = self._feed.get_selected_post()
        if p:
            self._detail.show_post(p)
            self._feed.mark_read(row)
            self._update_count_label()

    def _apply_time_filter(self, filter_text: str) -> None:
        """Show/hide rows based on the selected time filter."""
        from datetime import timezone
        now = datetime.now(timezone.utc)

        for row in range(self._feed.rowCount()):
            dot = self._feed.item(row, 0)
            if not dot:
                continue
            post = dot.data(self._feed.POST_ROLE)
            show = True

            if filter_text == "Today":
                if post and isinstance(getattr(post, 'timestamp', None), datetime):
                    ts = post.timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    show = (now - ts).total_seconds() < 86400
            elif filter_text == "This Week":
                if post and isinstance(getattr(post, 'timestamp', None), datetime):
                    ts = post.timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    show = (now - ts).total_seconds() < 7 * 86400
            elif filter_text == "Unread Only":
                show = not dot.data(self._feed.READ_ROLE)
            elif filter_text == "Matches Only":
                if post and hasattr(post, 'score'):
                    threshold = self._app.config.ai.threshold if self._app else 0.6
                    show = post.score >= threshold

            self._feed.setRowHidden(row, not show)

    def _update_count_label(self) -> None:
        unread = self._feed.get_unread_count()
        if unread > 0:
            self._cnt_lbl.setText(f"Posts: {self._total} ({unread} unread)")
        else:
            self._cnt_lbl.setText(f"Posts: {self._total}")

    def closeEvent(self, event) -> None:
        event.ignore(); self.hide()
