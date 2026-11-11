"""C2 scheduling UI; selection and persistence are separate from the engine."""

from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from wallpaper_orchestrator.scheduler import (
    ALL_WEEKDAYS, ScheduleRule, due_rules, load_schedules, new_rule,
    record_manual_result, save_schedules, stamp_attempts,
)
from wallpaper_orchestrator.calendar_view import CalendarRulesDialog


USER_ROLE = Qt.ItemDataRole.UserRole
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class ScheduleEditor(QDialog):
    """Edit a local-time recurrence and its stable monitor subscription."""

    def __init__(self, monitors, mapping, existing: ScheduleRule | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit schedule" if existing else "New wallpaper schedule")
        self.setMinimumWidth(470)
        form = QFormLayout(self)
        self.name = QLineEdit(existing.name if existing else "Daily wallpaper refresh")
        form.addRow("Name", self.name)

        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        hour, minute = map(int, (existing.time if existing else "09:00").split(":"))
        self.time.setTime(QTime(hour, minute))
        form.addRow("Local time", self.time)

        self.target = QComboBox()
        self.target.addItem("All eligible displays", "__all__")
        roles = {device_id: role for role, device_id in mapping.items()}
        for monitor in monitors:
            label = roles.get(monitor.device_id, monitor.orientation)
            self.target.addItem(
                f"{label} ({monitor.width} × {monitor.height})", monitor.device_id
            )
        if existing and existing.target == "monitor":
            index = self.target.findData(existing.device_id)
            if index < 0:
                self.target.addItem(
                    f"Disconnected display ({existing.device_id})", existing.device_id
                )
                index = self.target.count() - 1
            self.target.setCurrentIndex(index)
        form.addRow("Target", self.target)

        days = QWidget()
        days_row = QHBoxLayout(days)
        days_row.setContentsMargins(0, 0, 0, 0)
        self.day_checks = []
        for index, label in enumerate(DAY_NAMES):
            checkbox = QCheckBox(label)
            checkbox.setChecked(index in (existing.weekdays if existing else ALL_WEEKDAYS))
            self.day_checks.append(checkbox)
            days_row.addWidget(checkbox)
        form.addRow("Days", days)

        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(existing.enabled if existing else True)
        form.addRow("", self.enabled)
        note = QLabel(
            "Missed runs catch up once on the same day when the app resumes. "
            "A newly created or edited past-time rule starts at its next occurrence."
        )
        note.setWordWrap(True)
        note.setMaximumWidth(430)
        form.addRow("", note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate_and_accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Schedule", "Please give the schedule a name.")
            return
        if not any(checkbox.isChecked() for checkbox in self.day_checks):
            QMessageBox.warning(self, "Schedule", "Select at least one weekday.")
            return
        self.accept()

    def value(self, existing: ScheduleRule | None = None) -> ScheduleRule:
        selection = self.target.currentData()
        rule = new_rule(
            self.name.text().strip(), self.time.time().toString("HH:mm"),
            target="all" if selection == "__all__" else "monitor",
            device_id=None if selection == "__all__" else selection,
            weekdays=tuple(i for i, item in enumerate(self.day_checks) if item.isChecked()),
            enabled=self.enabled.isChecked(), now=datetime.now().astimezone(),
        )
        return replace(rule, id=existing.id) if existing else rule


class SchedulesPanel(QWidget):
    """Manage reusable daily/weekly schedules; emit actions to the main app."""

    run_now_requested = Signal(object)  # A list of schedule rules.

    def __init__(self, monitors, mapping, parent=None):
        super().__init__(parent)
        self.monitors = list(monitors)
        self.mapping = dict(mapping)
        self._load_error = None
        try:
            self.rules = load_schedules()
        except ValueError as error:
            self.rules = []
            self._load_error = str(error)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        title = QLabel("Wallpaper Schedules")
        title.setObjectName("scheduleTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Create timed wallpaper updates here, or use Seasonal rules… to "
            "choose images by month, date range, weekday, time and tags. "
            "Fixed-image monitors are never shuffled. Enable background "
            "mode from Displays → Automate to continue when closed."
        )
        subtitle.setObjectName("scheduleMuted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        actions = QHBoxLayout()
        self.add_button = QPushButton("+ New schedule")
        self.edit_button = QPushButton("Edit")
        self.remove_button = QPushButton("Delete")
        self.run_button = QPushButton("Run now")
        self.calendar_button = QPushButton("Seasonal rules…")
        actions.addWidget(self.add_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        actions.addWidget(self.calendar_button)
        actions.addWidget(self.run_button)
        layout.addLayout(actions)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("schedulesTable")
        self.table.setHorizontalHeaderLabels(
            ["Name", "Time", "Days", "Target", "Enabled", "Last result"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for column in range(5):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table, 1)

        self.status = QLabel(
            self._load_error or
            "Schedules run while open, or through the optional background agent."
        )
        self.status.setObjectName("scheduleMuted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setStyleSheet("""
            QLabel#scheduleTitle { font-size: 21px; font-weight: 500; }
            QLabel#scheduleMuted { color: #aab8ca; }
            QTableWidget#schedulesTable {
                background: #171b23; border: 1px solid #38404d;
                border-radius: 5px; color: #e8e9ec;
                selection-background-color: #344e6b;
            }
            QHeaderView::section { background: #252c37; color: #cbd5e1;
                border: 0; border-right: 1px solid #38404d; padding: 7px; }
        """)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.remove_button.clicked.connect(self._remove)
        self.run_button.clicked.connect(self._run_now)
        self.calendar_button.clicked.connect(self._open_calendar_rules)
        self.table.itemSelectionChanged.connect(self._buttons)
        self._refresh()

    def update_monitors(self, monitors, mapping):
        self.monitors = list(monitors)
        self.mapping = dict(mapping)
        self._refresh()

    def reload_results(self):
        """Refresh status changes written by the independent agent process."""
        if self._load_error is not None:
            return
        try:
            self.rules = load_schedules()
            self._refresh()
        except (OSError, ValueError) as error:
            self.status.setText(f"Could not refresh schedule status: {error}")

    def _selected_rule(self) -> ScheduleRule | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        identity = item.data(USER_ROLE) if item else None
        return next((rule for rule in self.rules if rule.id == identity), None)

    def _target_label(self, rule: ScheduleRule) -> str:
        if rule.target == "all":
            return "All eligible displays"
        roles = {device_id: name for name, device_id in self.mapping.items()}
        return roles.get(rule.device_id, f"Monitor {rule.device_id[:8]}")

    def _refresh(self):
        selected = self._selected_rule()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rules))
        for row, rule in enumerate(self.rules):
            values = (
                rule.name, rule.time,
                "Every day" if rule.weekdays == ALL_WEEKDAYS
                else ", ".join(DAY_NAMES[index] for index in rule.weekdays),
                self._target_label(rule), "Yes" if rule.enabled else "No",
                rule.last_result,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setData(USER_ROLE, rule.id)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
            if selected and selected.id == rule.id:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self._buttons()

    def _buttons(self):
        has_rule = self._selected_rule() is not None
        self.add_button.setEnabled(self._load_error is None)
        self.edit_button.setEnabled(has_rule and self._load_error is None)
        self.remove_button.setEnabled(has_rule and self._load_error is None)
        self.run_button.setEnabled(has_rule and self._load_error is None)

    def _save(self, rules: list[ScheduleRule]) -> bool:
        try:
            save_schedules(rules)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Schedule Save Error", str(error))
            return False
        self.rules = rules
        self._refresh()
        return True

    def _add(self):
        editor = ScheduleEditor(self.monitors, self.mapping, parent=self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            if self._save([*self.rules, editor.value()]):
                self.status.setText("Schedule created.")

    def _edit(self):
        chosen = self._selected_rule()
        if chosen is None:
            return
        editor = ScheduleEditor(self.monitors, self.mapping, chosen, self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            updated = editor.value(chosen)
            if self._save([updated if rule.id == chosen.id else rule
                           for rule in self.rules]):
                self.status.setText("Schedule updated. Next run is at its next occurrence.")

    def _remove(self):
        chosen = self._selected_rule()
        if chosen is None:
            return
        if QMessageBox.question(
            self, "Delete schedule", f"Delete '{chosen.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if self._save([rule for rule in self.rules if rule.id != chosen.id]):
            self.status.setText("Schedule deleted. Existing wallpapers unchanged.")

    def _run_now(self):
        chosen = self._selected_rule()
        if chosen is not None:
            self.run_now_requested.emit([chosen])

    def _open_calendar_rules(self):
        """Keep the Schedules tab compact while exposing advanced filters."""
        CalendarRulesDialog(self.monitors, self.mapping, self).exec()

    def get_due(self, now: datetime) -> list[ScheduleRule]:
        return due_rules(self.rules, now) if self._load_error is None else []

    def mark_attempts(self, ids: set[str], now: datetime, message: str) -> bool:
        if not ids:
            return True
        return self._save(stamp_attempts(self.rules, ids, now, message))

    def record_manual_result(self, ids: set[str], message: str) -> bool:
        """A manual test must not consume the next automatic occurrence."""
        self.reload_results()
        return self._save(record_manual_result(self.rules, ids, message))

    def report_result(self, message: str):
        self.status.setText(message)
