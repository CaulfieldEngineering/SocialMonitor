"""Main application — bridges PyQt6 event loop with asyncio."""

from __future__ import annotations

import asyncio
import logging
import sys

from PyQt6.QtWidgets import QApplication

from social_monitor.config import load_config, AppConfig
from social_monitor.database import Database
from social_monitor.notifier import Notifier
from social_monitor.poller import Poller
from social_monitor.scorer import Scorer
from social_monitor.ui.main_window import MainWindow
from social_monitor.ui.signals import SignalBridge
from social_monitor.ui.tray import TrayIcon

logger = logging.getLogger(__name__)


class SocialMonitorApp:
    """Top-level application controller."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.db = Database()
        self.notifier = Notifier(sound=config.general.notification_sound)
        self.signals = SignalBridge()
        self._qt_app: QApplication | None = None
        self._tray: TrayIcon | None = None
        self._main_window: MainWindow | None = None
        self._poller: Poller | None = None
        self._poller_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._scorer: Scorer | None = None

    def setup(self, qt_app: QApplication) -> None:
        self._qt_app = qt_app

        # Create main window (pass self so toolbar buttons work)
        self._main_window = MainWindow(app_controller=self)
        self.signals.post_scored.connect(self._main_window.add_post)
        self.signals.ai_status.connect(self._main_window.set_ai_status)
        self.signals.source_status.connect(self._main_window.set_source_status)

        # Create tray
        self._tray = TrayIcon(self)
        self._tray.show()
        self._tray.set_status("Starting...")

        # Always show main window on launch
        self._main_window.show()

    async def start_async(self) -> None:
        """Initialize async components (database, poller)."""
        await self.db.connect()

        # Load recent history into the feed
        history = await self.db.get_recent_matches(limit=100, min_score=0.0)
        if history:
            self._main_window.load_history(history)
            logger.info("Loaded %d historical posts into feed", len(history))

        # Create AI scorer if configured
        self._scorer = None
        if self.config.ai.provider != "none" and self.config.ai.api_key:
            self._scorer = Scorer(ai_config=self.config.ai)
            ai_text = self._scorer.status_text()
            logger.info("AI scoring enabled: %s", ai_text)
            self._main_window.set_ai_status(f"{ai_text} | Threshold: {self.config.ai.threshold:.0%}")
        else:
            self._main_window.set_ai_status("AI: Disabled (keyword matching only)")

        # Create and start the poller
        self._poller = Poller(
            config=self.config,
            db=self.db,
            notifier=self.notifier,
            scorer=self._scorer,
            signal_bridge=self.signals,
        )
        self._poller_task = asyncio.create_task(self._poller.run())
        self._tray.set_status("Monitoring")
        logger.info("SocialMonitor started")

    async def stop_async(self) -> None:
        if self._poller:
            self._poller.stop()
            await self._poller.teardown()
        if self._poller_task:
            self._poller_task.cancel()
        await self.db.close()
        logger.info("SocialMonitor stopped")

    # -- Reload (hot-restart poller after settings change) --

    def reload_settings(self) -> None:
        """Stop current poller, rebuild scorer, start fresh poller. No app restart needed."""
        if self._loop:
            asyncio.ensure_future(self._reload_async(), loop=self._loop)

    async def _reload_async(self) -> None:
        logger.info("Reloading settings...")

        # Stop old poller
        if self._poller:
            self._poller.stop()
            await self._poller.teardown()
        if self._poller_task:
            self._poller_task.cancel()
            self._poller_task = None

        # Update notifier
        self.notifier._sound = self.config.general.notification_sound

        # Rebuild scorer
        self._scorer = None
        if self.config.ai.provider != "none" and self.config.ai.api_key:
            self._scorer = Scorer(ai_config=self.config.ai)
            ai_text = self._scorer.status_text()
            self._main_window.set_ai_status(f"{ai_text} | Threshold: {self.config.ai.threshold:.0%}")
        else:
            self._main_window.set_ai_status("AI: Disabled (keyword matching only)")

        # Start fresh poller
        self._poller = Poller(
            config=self.config,
            db=self.db,
            notifier=self.notifier,
            scorer=self._scorer,
            signal_bridge=self.signals,
        )
        self._poller_task = asyncio.create_task(self._poller.run())
        self._tray.set_status("Monitoring")
        logger.info("Settings reloaded — poller restarted with %d source(s)", len(self.config.sources))

    # -- Actions called by tray menu --

    def show_log_viewer(self) -> None:
        """Show/raise the main window."""
        if self._main_window:
            self._main_window.show()
            self._main_window.raise_()
            self._main_window.activateWindow()

    def show_settings(self) -> None:
        """Show main window and switch to the Settings tab."""
        if self._main_window:
            self._main_window.show()
            self._main_window.raise_()
            self._main_window._tabs.setCurrentIndex(1)  # Settings tab

    def pause_monitoring(self) -> None:
        if self._poller:
            self._poller.pause()
        logger.info("Monitoring paused")

    def resume_monitoring(self) -> None:
        if self._poller:
            self._poller.resume()
        logger.info("Monitoring resumed")

    def check_now(self) -> None:
        if self._poller:
            self._poller.check_now()
        logger.info("Manual check triggered")

    def quit(self) -> None:
        logger.info("Quit requested")
        # Stop poller first to prevent tasks running during shutdown
        if self._poller:
            self._poller.stop()
        if self._poller_task:
            self._poller_task.cancel()
        if self._qt_app:
            self._qt_app.quit()


def _ensure_single_instance():
    """Prevent multiple instances using a Windows mutex."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, "SocialMonitor_SingleInstance_Mutex")
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        return True
    except Exception:
        return True  # If mutex fails, allow running anyway


def run_app() -> int:
    """Application entry point."""
    if not _ensure_single_instance():
        # Another instance is already running
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            temp_app = QApplication(sys.argv)
            QMessageBox.information(None, "SocialMonitor",
                "SocialMonitor is already running.\nCheck your system tray.")
        except Exception:
            pass
        return 0

    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.general.log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setApplicationName("SocialMonitor")
    qt_app.setStyleSheet("""
        QScrollBar:vertical {
            background: #f0f0f0;
            width: 10px;
            border-radius: 5px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #c0c0c0;
            min-height: 30px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background: #a0a0a0;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar:horizontal {
            background: #f0f0f0;
            height: 10px;
            border-radius: 5px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #c0c0c0;
            min-width: 30px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #a0a0a0;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }
        QMainWindow, QDialog, QTabWidget::pane, QStackedWidget {
            background: white;
        }
        QGroupBox {
            background: white;
            border: 1px solid #ddd;
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 16px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            padding: 0 6px;
        }
        QListWidget {
            background: white;
            border: 1px solid #ddd;
        }
        QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {
            background: white;
            border: 1px solid #ccc;
            border-radius: 3px;
            padding: 4px;
        }
        QStatusBar {
            background: #f8f8f8;
        }
    """)

    app = SocialMonitorApp(config)
    app.setup(qt_app)

    try:
        import qasync

        loop = qasync.QEventLoop(qt_app)
        asyncio.set_event_loop(loop)
        app._loop = loop

        async def _main():
            await app.start_async()
            # Keep running until the Qt app quits
            stop_event = asyncio.Event()
            qt_app.aboutToQuit.connect(stop_event.set)
            await stop_event.wait()
            try:
                await app.stop_async()
            except Exception:
                logger.debug("Shutdown cleanup interrupted (normal on exit)")

        with loop:
            try:
                loop.run_until_complete(_main())
            except RuntimeError as e:
                if "Event loop stopped" in str(e):
                    logger.debug("Event loop stopped during shutdown (normal)")
                else:
                    raise
    except ImportError:
        logger.warning("qasync not installed — running Qt loop only")
        return qt_app.exec()

    return 0
