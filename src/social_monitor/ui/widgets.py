"""Reusable UI components for the settings dialog."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class KeywordListEditor(QWidget):
    """A widget for editing a list of keywords with add/remove buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        layout.addWidget(self._list)

        # Input row
        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a keyword and press Add...")
        self._input.returnPressed.connect(self._add_keyword)
        input_row.addWidget(self._input)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_keyword)
        input_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected)
        input_row.addWidget(remove_btn)

        layout.addLayout(input_row)

    def _add_keyword(self) -> None:
        text = self._input.text().strip()
        if text:
            self._list.addItem(text)
            self._input.clear()

    def _remove_selected(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))

    def get_keywords(self) -> list[str]:
        return [
            self._list.item(i).text() for i in range(self._list.count())
        ]

    def set_keywords(self, keywords: list[str]) -> None:
        self._list.clear()
        for kw in keywords:
            self._list.addItem(kw)


class StringListEditor(QWidget):
    """Generic list editor for URLs, tags, subreddits, etc."""

    def __init__(self, placeholder: str = "Add item...", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        layout.addWidget(self._list)

        input_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText(placeholder)
        self._input.returnPressed.connect(self._add_item)
        input_row.addWidget(self._input)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_item)
        input_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected)
        input_row.addWidget(remove_btn)

        layout.addLayout(input_row)

    def _add_item(self) -> None:
        text = self._input.text().strip()
        if text:
            self._list.addItem(text)
            self._input.clear()

    def _remove_selected(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))

    def get_items(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def set_items(self, items: list[str]) -> None:
        self._list.clear()
        for item in items:
            self._list.addItem(item)
