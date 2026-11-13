"""Compact interval dialog launched from selected monitor tiles."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)


class AutomateDialog(QDialog):
    def __init__(self, count, order_summary, existing, background_enabled,
                 agent_health, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Automate selected displays")
        self.setMinimumWidth(455)
        self.action = None
        layout = QVBoxLayout(self)
        caption = QLabel(
            f"{count} selected display(s) will shuffle automatically using "
            "their current collections or folders."
        )
        caption.setWordWrap(True)
        layout.addWidget(caption)
        form = QFormLayout()
        interval_row = QWidget()
        interval_layout = QHBoxLayout(interval_row)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        self.number = QSpinBox()
        self.unit = QComboBox()
        self.unit.addItem("Minutes", 60)
        self.unit.addItem("Hours", 3600)
        seconds = existing.interval_seconds if existing else 30 * 60
        if seconds % 3600 == 0:
            self.unit.setCurrentIndex(1)
            self._limit()
            self.number.setValue(seconds // 3600)
        else:
            self._limit()
            self.number.setValue(seconds // 60)
        interval_layout.addWidget(self.number, 1)
        interval_layout.addWidget(self.unit)
        form.addRow("Shuffle every", interval_row)
        self.unit.currentIndexChanged.connect(self._limit)
        self._limit()

        order = QLabel(order_summary)
        order.setWordWrap(True)
        form.addRow("Wallpaper order", order)
        self.run_now = QCheckBox("Change the selected wallpapers immediately")
        self.run_now.setChecked(existing is None)
        form.addRow("", self.run_now)
        self.background = QCheckBox("Run even when the application window is closed")
        self.background.setChecked(background_enabled)
        self.background.setToolTip(
            "Global background setting: unchecking stops all interval and "
            "daily schedules while the app is closed."
        )
        form.addRow("", self.background)
        layout.addLayout(form)

        notice = QLabel(
            "Background mode starts a small hidden process now and at Windows "
            "sign-in (for your user account only). Wallpaper order is read "
            "fresh each time, so changing Random / Next / Previous on Displays "
            "will also change this automation. The background checkbox applies "
            "to all automations, not only the selected monitors."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #aab8ca;")
        layout.addWidget(notice)
        health = QLabel(f"Background agent: {agent_health}")
        health.setWordWrap(True)
        health.setStyleSheet("color: #aab8ca;")
        layout.addWidget(health)
        if existing is not None:
            last_result = QLabel(f"Last automatic result: {existing.last_result}")
            last_result.setWordWrap(True)
            last_result.setStyleSheet("color: #aab8ca;")
            layout.addWidget(last_result)
        buttons = QHBoxLayout()
        self.remove_button = QPushButton("Stop this interval")
        self.remove_button.setEnabled(existing is not None)
        self.remove_button.clicked.connect(self._remove)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("Save automation")
        apply.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

    def _limit(self):
        self.number.setRange(1, 168 if self.unit.currentData() == 3600 else 10080)

    def interval_seconds(self) -> int:
        return self.number.value() * self.unit.currentData()

    def _save(self):
        self.action = "save"
        self.accept()

    def _remove(self):
        self.action = "remove"
        self.accept()
