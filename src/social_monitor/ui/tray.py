"""System tray icon and context menu."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter, QBrush
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

if TYPE_CHECKING:
    from social_monitor.app import SocialMonitorApp

logger = logging.getLogger(__name__)


def _make_icon(color: str = "#4CAF50", size: int = 64) -> QIcon:
    """Generate a simple colored circle icon (placeholder until a real icon is added)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(QColor(color).darker(120))
    painter.drawEllipse(2, 2, size - 4, size - 4)
    # Draw "SM" text
    from PyQt6.QtGui import QFont

    font = QFont("Arial", size // 3, QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), 0x0084, "SM")  # AlignCenter
    painter.end()
    return QIcon(pixmap)


class TrayIcon(QSystemTrayIcon):
    """System tray icon with context menu for SocialMonitor."""

    def __init__(self, app: SocialMonitorApp):
        super().__init__(_make_icon(), parent=None)
        self._app = app
        self._paused = False
        self._unread_count = 0
        self._setup_menu()
        self.setToolTip("SocialMonitor — Idle")
        self.activated.connect(self._on_activated)

    def _setup_menu(self) -> None:
        menu = QMenu()

        self._status_action = QAction("Status: Idle")
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        menu.addSeparator()

        view_action = QAction("View Recent Matches", menu)
        view_action.triggered.connect(self._on_view_matches)
        menu.addAction(view_action)

        settings_action = QAction("Settings...", menu)
        settings_action.triggered.connect(self._on_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        self._pause_action = QAction("Pause Monitoring", menu)
        self._pause_action.triggered.connect(self._on_toggle_pause)
        menu.addAction(self._pause_action)

        check_now_action = QAction("Check Now", menu)
        check_now_action.triggered.connect(self._on_check_now)
        menu.addAction(check_now_action)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._on_quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    # -- State updates --

    def set_status(self, text: str) -> None:
        self._status_action.setText(f"Status: {text}")
        self.setToolTip(f"SocialMonitor — {text}")

    def set_alert(self, count: int) -> None:
        """Update icon to alert state with unread count."""
        self._unread_count = count
        if count > 0:
            self.setIcon(_make_icon("#FF5722"))
            self.setToolTip(f"SocialMonitor — {count} new match{'es' if count != 1 else ''}")
        else:
            self.setIcon(_make_icon("#4CAF50"))

    # -- Menu handlers --

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._on_view_matches()

    def _on_view_matches(self) -> None:
        self._app.show_log_viewer()

    def _on_settings(self) -> None:
        self._app.show_settings()

    def _on_toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._pause_action.setText("Resume Monitoring")
            self.set_status("Paused")
            self.setIcon(_make_icon("#9E9E9E"))
            self._app.pause_monitoring()
        else:
            self._pause_action.setText("Pause Monitoring")
            self.set_status("Monitoring")
            self.setIcon(_make_icon("#4CAF50"))
            self._app.resume_monitoring()

    def _on_check_now(self) -> None:
        self._app.check_now()

    def _on_quit(self) -> None:
        self._app.quit()
