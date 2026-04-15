"""Log viewer dialog — shows recent matched posts with scores."""

from __future__ import annotations

import logging
import webbrowser

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class LogViewer(QDialog):
    """Dialog showing recent matched posts from the database."""

    def __init__(self, matches: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("SocialMonitor — Recent Matches")
        self.setMinimumSize(800, 500)

        layout = QVBoxLayout(self)

        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel(f"Showing {len(matches)} recent match(es)"))
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._on_refresh)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Score", "Source", "Title", "Author", "Explanation", "Time"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

        # Populate
        self._matches = matches
        self._populate(matches)

        # Footer
        footer = QHBoxLayout()
        footer.addWidget(QLabel("Double-click a row to open the post in your browser."))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    def _populate(self, matches: list[dict]) -> None:
        self._matches = matches
        self._table.setRowCount(len(matches))

        for row, m in enumerate(matches):
            # Score with color coding
            score = m.get("score", 0) or 0
            score_item = QTableWidgetItem(f"{score:.0%}")
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if score >= 0.8:
                score_item.setBackground(QColor(76, 175, 80, 80))  # Green
            elif score >= 0.6:
                score_item.setBackground(QColor(255, 193, 7, 80))  # Amber
            else:
                score_item.setBackground(QColor(158, 158, 158, 80))  # Grey
            self._table.setItem(row, 0, score_item)

            # Source
            source = (m.get("source", "") or "").replace("_", " ").title()
            self._table.setItem(row, 1, QTableWidgetItem(source))

            # Title
            self._table.setItem(row, 2, QTableWidgetItem(m.get("title", "")))

            # Author
            self._table.setItem(row, 3, QTableWidgetItem(m.get("author", "")))

            # Explanation
            self._table.setItem(
                row, 4, QTableWidgetItem(m.get("explanation", ""))
            )

            # Time
            self._table.setItem(
                row, 5, QTableWidgetItem(m.get("created_at", ""))
            )

    def _on_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._matches):
            url = self._matches[row].get("url", "")
            if url:
                webbrowser.open(url)

    def _on_refresh(self) -> None:
        # This would need a reference to the database to re-query.
        # For now it's a placeholder — will be wired via a callback.
        logger.info("Refresh requested (requires app integration)")
