"""Tabbed settings dialog with dynamic source management and method selection."""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from social_monitor.config import AppConfig, SourceInstanceConfig, save_config
from social_monitor.sources import SOURCE_REGISTRY
from social_monitor.sources.base import AccessMethod, ConfigField
from social_monitor.ui.widgets import KeywordListEditor, StringListEditor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: create a widget for a ConfigField
# ---------------------------------------------------------------------------

def _create_field_widget(field: ConfigField, value: Any) -> QWidget:
    if field.field_type == "str":
        w = QLineEdit(str(value) if value else "")
        w.setPlaceholderText(field.placeholder)
        return w
    elif field.field_type == "password":
        w = QLineEdit(str(value) if value else "")
        w.setEchoMode(QLineEdit.EchoMode.Password)
        w.setPlaceholderText(field.placeholder)
        return w
    elif field.field_type == "int":
        w = QSpinBox()
        w.setRange(0, 999999)
        w.setValue(int(value) if value else 0)
        return w
    elif field.field_type == "str_list":
        w = StringListEditor(field.placeholder)
        w.set_items(value if isinstance(value, list) else [])
        return w
    elif field.field_type == "int_list":
        w = StringListEditor(field.placeholder)
        w.set_items([str(x) for x in value] if isinstance(value, list) else [])
        return w
    elif field.field_type == "text":
        w = QPlainTextEdit(str(value) if value else "")
        w.setPlaceholderText(field.placeholder)
        w.setMaximumHeight(100)
        return w
    else:
        w = QLineEdit(str(value) if value else "")
        return w


def _read_field_widget(field: ConfigField, widget: QWidget) -> Any:
    if field.field_type in ("str", "password"):
        return widget.text()
    elif field.field_type == "int":
        return widget.value()
    elif field.field_type == "str_list":
        return widget.get_items()
    elif field.field_type == "int_list":
        return [int(x) for x in widget.get_items() if x.isdigit()]
    elif field.field_type == "text":
        return widget.toPlainText()
    else:
        return widget.text()


def _add_field_to_form(
    form: QFormLayout, field: ConfigField, widget: QWidget
) -> None:
    """Add a field + optional help text to a form layout."""
    if field.help_text:
        container = QVBoxLayout()
        container.setContentsMargins(0, 0, 0, 0)
        container.addWidget(widget)
        help_label = QLabel(field.help_text)
        help_label.setStyleSheet("color: gray; font-size: 11px;")
        help_label.setWordWrap(True)
        container.addWidget(help_label)
        wrapper = QWidget()
        wrapper.setLayout(container)
        form.addRow(field.label + ":", wrapper)
    else:
        form.addRow(field.label + ":", widget)


# ---------------------------------------------------------------------------
# Method panel — one per AccessMethod, stacked and swapped by dropdown
# ---------------------------------------------------------------------------

class MethodPanel(QWidget):
    """Form panel for one access method's config fields."""

    def __init__(self, method: AccessMethod, settings: dict, parent=None):
        super().__init__(parent)
        self.method = method
        self._field_widgets: list[tuple[ConfigField, QWidget]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Method description
        desc = QLabel(method.description)
        desc.setStyleSheet("color: #555; font-style: italic; margin-bottom: 6px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        if method.fields:
            form = QFormLayout()
            for field in method.fields:
                value = settings.get(field.key, field.default)
                widget = _create_field_widget(field, value)
                self._field_widgets.append((field, widget))
                _add_field_to_form(form, field, widget)
            layout.addLayout(form)
        else:
            layout.addWidget(QLabel("No additional configuration needed for this method."))

    def collect_settings(self) -> dict[str, Any]:
        result = {}
        for field, widget in self._field_widgets:
            result[field.key] = _read_field_widget(field, widget)
        return result


# ---------------------------------------------------------------------------
# Source config form — full form for a single source instance
# ---------------------------------------------------------------------------

class SourceConfigForm(QWidget):
    """Dynamically generated form for a single source instance."""

    def __init__(self, src_cfg: SourceInstanceConfig, parent=None):
        super().__init__(parent)
        self._src_cfg = src_cfg
        self._common_widgets: list[tuple[ConfigField, QWidget]] = []

        from social_monitor.sources import SOURCE_REGISTRY
        source_cls = SOURCE_REGISTRY.get(src_cfg.type)
        if source_cls is None:
            QVBoxLayout(self).addWidget(QLabel(f"Unknown source type: {src_cfg.type}"))
            self._methods = []
            return

        self._methods = source_cls.supported_methods()
        common_fields = source_cls.common_fields()

        layout = QVBoxLayout(self)

        # -- Common fields: name, enabled, interval --
        top_form = QFormLayout()

        self._name_edit = QLineEdit(src_cfg.name)
        top_form.addRow("Name:", self._name_edit)

        self._enabled = QCheckBox("Enabled")
        self._enabled.setChecked(src_cfg.enabled)
        top_form.addRow(self._enabled)

        self._interval = QSpinBox()
        self._interval.setRange(10, 7200)
        self._interval.setValue(src_cfg.interval)
        self._interval.setSuffix(" seconds")
        top_form.addRow("Poll interval:", self._interval)

        # Source-specific common fields (shared across all methods)
        for field in common_fields:
            value = src_cfg.settings.get(field.key, field.default)
            widget = _create_field_widget(field, value)
            self._common_widgets.append((field, widget))
            _add_field_to_form(top_form, field, widget)

        layout.addLayout(top_form)

        # -- Method selector --
        if len(self._methods) > 1:
            method_group = QGroupBox("Access Method")
            method_layout = QVBoxLayout(method_group)

            self._method_combo = QComboBox()
            for m in self._methods:
                self._method_combo.addItem(m.label, m.key)
            method_layout.addWidget(self._method_combo)

            # Stacked panels — one per method
            self._method_stack = QStackedWidget()
            self._method_panels: list[MethodPanel] = []
            for m in self._methods:
                panel = MethodPanel(m, src_cfg.settings)
                self._method_panels.append(panel)
                self._method_stack.addWidget(panel)
            method_layout.addWidget(self._method_stack)

            # Wire dropdown to stack
            self._method_combo.currentIndexChanged.connect(self._method_stack.setCurrentIndex)

            # Set current method
            current_method = src_cfg.method or source_cls.default_method()
            for i, m in enumerate(self._methods):
                if m.key == current_method:
                    self._method_combo.setCurrentIndex(i)
                    break

            layout.addWidget(method_group)
        elif len(self._methods) == 1:
            # Single method — no dropdown needed, just show its fields
            self._method_combo = None
            self._method_panels = [MethodPanel(self._methods[0], src_cfg.settings)]
            if self._methods[0].fields:
                group = QGroupBox(self._methods[0].label)
                group_layout = QVBoxLayout(group)
                group_layout.addWidget(self._method_panels[0])
                layout.addWidget(group)
        else:
            self._method_combo = None
            self._method_panels = []

        # -- Per-source keyword override --
        kw_group = QGroupBox("Keywords (override global)")
        kw_layout = QVBoxLayout(kw_group)
        self._keywords = KeywordListEditor()
        self._keywords.set_keywords(src_cfg.keywords)
        kw_layout.addWidget(self._keywords)
        layout.addWidget(kw_group)

        layout.addStretch()

    def collect(self) -> SourceInstanceConfig:
        """Read all widget values back into a SourceInstanceConfig."""
        # Determine selected method
        if self._method_combo:
            method_key = self._method_combo.currentData()
            panel_idx = self._method_combo.currentIndex()
        elif self._methods:
            method_key = self._methods[0].key
            panel_idx = 0
        else:
            method_key = ""
            panel_idx = -1

        # Collect settings from common fields + active method panel
        settings: dict[str, Any] = {}
        for field, widget in self._common_widgets:
            settings[field.key] = _read_field_widget(field, widget)

        if 0 <= panel_idx < len(self._method_panels):
            settings.update(self._method_panels[panel_idx].collect_settings())

        return SourceInstanceConfig(
            name=self._name_edit.text(),
            type=self._src_cfg.type,
            method=method_key,
            enabled=self._enabled.isChecked(),
            interval=self._interval.value(),
            keywords=self._keywords.get_keywords(),
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Main settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("SocialMonitor Settings")
        self.setMinimumSize(750, 600)

        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._build_general_tab()
        self._build_sources_tab()
        self._build_ai_tab()
        self._build_keywords_tab()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── General ──────────────────────────────────────────────────

    def _build_general_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        self._start_minimized = QCheckBox("Start minimized to tray")
        self._start_minimized.setChecked(self.config.general.start_minimized)
        form.addRow(self._start_minimized)

        self._start_on_login = QCheckBox("Start on Windows login")
        self._start_on_login.setChecked(self.config.general.start_on_login)
        form.addRow(self._start_on_login)

        self._notification_sound = QCheckBox("Play notification sound")
        self._notification_sound.setChecked(self.config.general.notification_sound)
        form.addRow(self._notification_sound)

        self._log_level = QComboBox()
        self._log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level.setCurrentText(self.config.general.log_level)
        form.addRow("Log level:", self._log_level)

        self._tabs.addTab(tab, "General")

    # ── Sources (dynamic) ────────────────────────────────────────

    def _build_sources_tab(self) -> None:
        tab = QWidget()
        layout = QHBoxLayout(tab)

        # Left panel: source list + add/remove buttons
        left = QVBoxLayout()

        self._source_list = QListWidget()
        self._source_list.currentRowChanged.connect(self._on_source_selected)
        left.addWidget(self._source_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Source")
        add_btn.clicked.connect(self._on_add_source)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("- Remove")
        remove_btn.clicked.connect(self._on_remove_source)
        btn_row.addWidget(remove_btn)

        left.addLayout(btn_row)
        layout.addLayout(left, stretch=1)

        # Right panel: scrollable stacked config forms
        self._source_stack = QStackedWidget()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._source_stack)
        layout.addWidget(scroll, stretch=2)

        # Populate from existing config
        self._source_forms: list[SourceConfigForm] = []
        for src_cfg in self.config.sources:
            self._add_source_to_ui(src_cfg)

        if not self.config.sources:
            placeholder = QLabel("No sources configured.\nClick '+ Add Source' to get started.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._source_stack.addWidget(placeholder)

        self._tabs.addTab(tab, "Sources")

    def _add_source_to_ui(self, src_cfg: SourceInstanceConfig) -> None:
        form = SourceConfigForm(src_cfg)
        self._source_forms.append(form)
        self._source_stack.addWidget(form)

        display = src_cfg.name or f"{src_cfg.type} (unnamed)"
        if not src_cfg.enabled:
            display += " [disabled]"
        self._source_list.addItem(QListWidgetItem(display))

    def _on_source_selected(self, row: int) -> None:
        if 0 <= row < len(self._source_forms):
            self._source_stack.setCurrentWidget(self._source_forms[row])

    def _on_add_source(self) -> None:
        from social_monitor.poller import _import_all_sources
        _import_all_sources()

        type_labels = {}
        for type_name, cls in SOURCE_REGISTRY.items():
            type_labels[f"{cls.display_name} — {cls.description}"] = type_name

        label, ok = QInputDialog.getItem(
            self, "Add Source", "Select source type:",
            list(type_labels.keys()), editable=False,
        )
        if not ok or not label:
            return

        source_type = type_labels[label]
        source_cls = SOURCE_REGISTRY[source_type]

        name, ok = QInputDialog.getText(
            self, "Source Name",
            f"Give this {source_cls.display_name} source a name:",
            text=source_cls.display_name,
        )
        if not ok or not name:
            return

        new_cfg = SourceInstanceConfig(
            name=name,
            type=source_type,
            method=source_cls.default_method(),
            enabled=True,
            interval=source_cls.default_interval,
        )
        self._add_source_to_ui(new_cfg)
        self._source_list.setCurrentRow(self._source_list.count() - 1)

    def _on_remove_source(self) -> None:
        row = self._source_list.currentRow()
        if row < 0:
            return
        name = self._source_list.item(row).text()
        reply = QMessageBox.question(
            self, "Remove Source", f"Remove '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._source_list.takeItem(row)
        form = self._source_forms.pop(row)
        self._source_stack.removeWidget(form)
        form.deleteLater()

    # ── AI ────────────────────────────────────────────────────────

    def _build_ai_tab(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)
        cfg = self.config.ai

        self._ai_provider = QComboBox()
        self._ai_provider.addItems(["none", "claude", "openai", "openrouter"])
        self._ai_provider.setCurrentText(cfg.provider)
        form.addRow("Provider:", self._ai_provider)

        self._ai_api_key = QLineEdit(cfg.api_key)
        self._ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_api_key.setPlaceholderText("API key for Claude or OpenAI")
        form.addRow("API Key:", self._ai_api_key)

        self._ai_model = QLineEdit(cfg.model)
        self._ai_model.setPlaceholderText("e.g., claude-haiku-4-5-20251001 or gpt-4o-mini")
        form.addRow("Model:", self._ai_model)

        threshold_row = QHBoxLayout()
        self._ai_threshold = QSlider(Qt.Orientation.Horizontal)
        self._ai_threshold.setRange(0, 100)
        self._ai_threshold.setValue(int(cfg.threshold * 100))
        self._threshold_label = QLabel(f"{cfg.threshold:.2f}")
        self._ai_threshold.valueChanged.connect(
            lambda v: self._threshold_label.setText(f"{v / 100:.2f}")
        )
        threshold_row.addWidget(self._ai_threshold)
        threshold_row.addWidget(self._threshold_label)
        form.addRow("Threshold:", threshold_row)

        self._ai_interests = QPlainTextEdit(cfg.interests)
        self._ai_interests.setPlaceholderText(
            "Describe your interests and expertise. The AI uses this\n"
            "to judge relevance beyond keyword matching.\n\n"
            "Example: Audio software development, VST/AU plugin development,\n"
            "music production tools, DSP programming, JUCE framework"
        )
        self._ai_interests.setMaximumHeight(150)
        form.addRow("Interests:", self._ai_interests)

        form.addRow(QLabel(
            "When provider is 'none', posts are scored by keyword matching only.\n"
            "AI scoring uses the cheapest models (~$0.001 per batch of 20 posts)."
        ))

        self._tabs.addTab(tab, "AI Scoring")

    # ── Keywords ──────────────────────────────────────────────────

    def _build_keywords_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(QLabel(
            "Global keywords apply to all sources unless overridden per-source."
        ))

        group1 = QGroupBox("Global Keywords")
        g1_layout = QVBoxLayout(group1)
        self._global_keywords = KeywordListEditor()
        self._global_keywords.set_keywords(self.config.global_keywords)
        g1_layout.addWidget(self._global_keywords)
        layout.addWidget(group1)

        group2 = QGroupBox("Negative Keywords (exclude posts containing these)")
        g2_layout = QVBoxLayout(group2)
        self._negative_keywords = KeywordListEditor()
        self._negative_keywords.set_keywords(self.config.negative_keywords)
        g2_layout.addWidget(self._negative_keywords)
        layout.addWidget(group2)

        self._tabs.addTab(tab, "Keywords")

    # ── Save ──────────────────────────────────────────────────────

    def _save(self) -> None:
        self.config.general.start_minimized = self._start_minimized.isChecked()
        self.config.general.start_on_login = self._start_on_login.isChecked()
        self.config.general.notification_sound = self._notification_sound.isChecked()
        self.config.general.log_level = self._log_level.currentText()

        self.config.sources = [form.collect() for form in self._source_forms]

        self.config.ai.provider = self._ai_provider.currentText()
        self.config.ai.api_key = self._ai_api_key.text()
        self.config.ai.model = self._ai_model.text()
        self.config.ai.threshold = self._ai_threshold.value() / 100.0
        self.config.ai.interests = self._ai_interests.toPlainText()

        self.config.global_keywords = self._global_keywords.get_keywords()
        self.config.negative_keywords = self._negative_keywords.get_keywords()

        save_config(self.config)
        logger.info("Settings saved")
        self.accept()
