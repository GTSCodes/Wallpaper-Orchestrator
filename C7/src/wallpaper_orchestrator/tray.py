"""C7 Windows tray companion; the background agent remains a separate process."""

from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from wallpaper_orchestrator.automation import load_automation, save_automation
from wallpaper_orchestrator.background import agent_alive, read_status
from wallpaper_orchestrator.tray_state import automation_state_text, next_shuffle_text


def _tray_icon():
    """Draw a recognisable pair of monitors without external icon assets."""
    canvas = QPixmap(64, 64)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#dceaff"), 3))
    painter.setBrush(QColor("#2578c4"))
    painter.drawRoundedRect(6, 13, 35, 32, 4, 4)
    painter.setBrush(QColor("#122b46"))
    painter.drawRoundedRect(29, 25, 29, 25, 4, 4)
    painter.setPen(QPen(QColor("#dceaff"), 2))
    painter.drawLine(21, 45, 21, 51)
    painter.drawLine(12, 52, 30, 52)
    painter.end()
    return QIcon(canvas)


class WallpaperTray(QObject):
    """Explicit Exit quits the GUI only; Pause controls persisted schedules."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self._last_reported_failure = None
        if not self.available:
            self.tray = None
            return

        self.tray = QSystemTrayIcon(_tray_icon(), self)
        self.tray.setToolTip("Wallpaper Orchestrator")
        self.menu = QMenu(window)
        self.heading = self.menu.addAction("Wallpaper Orchestrator")
        self.heading.setEnabled(False)
        self.status = self.menu.addAction("Checking automation…")
        self.status.setEnabled(False)
        self.due = self.menu.addAction("Next interval: checking…")
        self.due.setEnabled(False)
        self.menu.addSeparator()
        self.shuffle = self.menu.addAction("Shuffle all displays")
        self.pause = self.menu.addAction("Pause automation")
        self.menu.addSeparator()
        self.open = self.menu.addAction("Open Wallpaper Orchestrator")
        self.settings = self.menu.addAction("Manage automations / Settings")
        self.menu.addSeparator()
        self.exit = self.menu.addAction("Exit GUI (keep background automation)")

        self.shuffle.triggered.connect(self.shuffle_all)
        self.pause.triggered.connect(self.toggle_pause)
        self.open.triggered.connect(self.show_window)
        self.settings.triggered.connect(self.show_settings)
        self.exit.triggered.connect(self.exit_gui)
        self.menu.aboutToShow.connect(self.refresh)
        self.tray.activated.connect(self._activated)
        self.tray.setContextMenu(self.menu)
        self._timer = QTimer(self)
        self._timer.setInterval(15000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()
        self.tray.show()

    def refresh(self):
        if not self.available:
            return
        try:
            config = load_automation()
            alive = agent_alive() if config.background_enabled else False
            self.status.setText(automation_state_text(
                config, agent_running=alive
            ))
            self.due.setText(next_shuffle_text(config))
            self.pause.setText("Resume automation" if config.paused else "Pause automation")
            if alive and not config.paused:
                message = str(read_status().get("message") or "")
                failed = any(word in message.lower() for word in
                             ("automation failed", "could not apply", "agent will retry"))
                if failed and message != self._last_reported_failure:
                    self.tray.showMessage("Background wallpaper problem", message,
                                          QSystemTrayIcon.MessageIcon.Warning, 6000)
                    self._last_reported_failure = message
                elif not failed:
                    self._last_reported_failure = None
        except (OSError, ValueError) as error:
            self.status.setText(f"Automation settings error: {error}")
            self.pause.setEnabled(False)
            return
        self.pause.setEnabled(True)

    def show_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def show_settings(self):
        self.show_window()
        self.window.tabs.setCurrentIndex(2)
        self.window.schedules_panel._open_automation_centre()
        self.refresh()

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    def toggle_pause(self):
        try:
            current = load_automation()
            save_automation(replace(current, paused=not current.paused))
            self.refresh()
            self.tray.showMessage(
                "Wallpaper Orchestrator",
                "Automations paused." if not current.paused else "Automations resumed.",
                QSystemTrayIcon.MessageIcon.Information, 2500,
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self.window, "Automation settings", str(error))

    def shuffle_all(self):
        try:
            message = self.window.shuffle_all_displays()
            self.tray.showMessage("Wallpaper Orchestrator", message,
                                  QSystemTrayIcon.MessageIcon.Information, 2500)
        except Exception as error:
            self.tray.showMessage("Wallpaper could not be applied", str(error),
                                  QSystemTrayIcon.MessageIcon.Warning, 6000)

    def exit_gui(self):
        self.window._tray_exit_requested = True
        self.show_window()
        if self.window.close():
            self.tray.hide()
            QApplication.instance().quit()
        else:
            self.window._tray_exit_requested = False
