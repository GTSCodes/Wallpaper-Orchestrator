"""C5 unified interval, daily and seasonal automation controls."""

from dataclasses import replace
from datetime import datetime
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from wallpaper_orchestrator.automation import (
    enforce_interval_exclusivity, load_automation, normalize_interval_conflicts,
    overlapping_intervals, save_automation,
)
from wallpaper_orchestrator.automation_management import find_overlaps, inventory
from wallpaper_orchestrator.background import agent_health_text, set_background_enabled
from wallpaper_orchestrator.calendar_rules import load_calendar_rules, save_calendar_rules
from wallpaper_orchestrator.calendar_view import CalendarRuleEditor
from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.monitor_labels import monitor_labels
from wallpaper_orchestrator.scheduler import load_schedules, save_schedules


USER_ROLE = Qt.ItemDataRole.UserRole


class IntervalEditor(QDialog):
    """Update an existing timer and its exact stable display ID set."""

    def __init__(self, rule, monitors, mapping, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit interval automation")
        self.setMinimumWidth(450)
        form = QFormLayout(self)
        self.amount = QSpinBox()
        self.amount.setRange(1, 10080)
        self.unit = QComboBox()
        self.unit.addItem("Minutes", 60)
        self.unit.addItem("Hours", 3600)
        if rule.interval_seconds % 3600 == 0:
            self.unit.setCurrentIndex(1)
            self.amount.setValue(rule.interval_seconds // 3600)
        else:
            self.amount.setValue(rule.interval_seconds // 60)
        wrap = QHBoxLayout()
        wrap.addWidget(self.amount)
        wrap.addWidget(self.unit)
        form.addRow("Shuffle every", wrap)
        self.monitors = QListWidget()
        self.monitors.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        labels = monitor_labels(monitors, mapping)
        ids = {monitor.device_id for monitor in monitors} | set(rule.monitor_ids)
        for mid in sorted(ids):
            label = labels.get(mid, f"Disconnected — {mid[:30]}")
            item = QListWidgetItem(label)
            item.setData(USER_ROLE, mid)
            self.monitors.addItem(item)
            item.setSelected(mid in rule.monitor_ids)
        form.addRow("Displays", self.monitors)
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(rule.enabled)
        form.addRow("", self.enabled)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                        QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)

    def _accept(self):
        if not self.selected_ids():
            QMessageBox.warning(self, "Interval", "Choose at least one display.")
        elif self.amount.value() * self.unit.currentData() > 604800:
            QMessageBox.warning(self, "Interval", "Maximum interval is seven days.")
        else:
            self.accept()

    def selected_ids(self):
        return tuple(sorted(item.data(USER_ROLE) for item in self.monitors.selectedItems()))

    def result(self, old):
        seconds = self.amount.value() * self.unit.currentData()
        return replace(old, interval_seconds=seconds, monitor_ids=self.selected_ids(),
                       enabled=self.enabled.isChecked(), next_due_utc=time.time() + seconds,
                       last_result="Updated; waiting for next interval")


class AutomationManagerDialog(QDialog):
    """One inventory for C3 intervals, C2 daily schedules and C4/C5 seasons."""

    def __init__(self, monitors, mapping, parent=None):
        super().__init__(parent)
        self.monitors = list(monitors)
        self.mapping = dict(mapping)
        self.setWindowTitle("Manage wallpaper automations")
        self.resize(1040, 570)
        layout = QVBoxLayout(self)
        title = QLabel("Automation centre")
        title.setStyleSheet("font-size: 20px; font-weight: 500")
        layout.addWidget(title)
        self.health = QLabel()
        self.health.setWordWrap(True)
        layout.addWidget(self.health)
        background_row = QHBoxLayout()
        self.background_choice = QCheckBox(
            "Keep all enabled automations running after the app window closes (start with Windows)"
        )
        self.background_save = QPushButton("Apply background setting")
        background_row.addWidget(self.background_choice)
        background_row.addWidget(self.background_save)
        layout.addLayout(background_row)
        self.pause_all = QPushButton("Pause all automations")
        self.pause_all.setToolTip(
            "Pause every interval, daily and seasonal trigger without removing rules. "
            "Manual wallpaper changes still work."
        )
        layout.addWidget(self.pause_all)
        note = QLabel(
            "Only one enabled interval may control each display. Creating, editing or resuming "
            "a conflicting interval pauses the previous one (without deleting it). "
            "Daily schedules and seasonal filters are managed separately."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #aab8ca")
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.edit_button = QPushButton("Edit selected")
        self.toggle_button = QPushButton("Pause selected")
        self.delete_button = QPushButton("Delete selected")
        self.pause_overlaps_button = QPushButton("Pause overlapping rules…")
        for button in (self.refresh_button, self.edit_button, self.toggle_button,
                       self.delete_button, self.pause_overlaps_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("Type", "Name", "Displays", "Frequency / window", "Status", "Next / result", "Overlap")
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for i in (0, 1, 2, 3, 4, 6):
            self.table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh_button.clicked.connect(self.refresh)
        self.background_save.clicked.connect(self._configure_background)
        self.pause_all.clicked.connect(self._toggle_global_pause)
        self.edit_button.clicked.connect(self._edit)
        self.toggle_button.clicked.connect(self._toggle)
        self.delete_button.clicked.connect(self._delete)
        self.pause_overlaps_button.clicked.connect(self._pause_overlaps)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.refresh()

    def _target_names(self, ids, all_displays=False):
        if all_displays:
            return "All eligible displays"
        labels = monitor_labels(self.monitors, self.mapping)
        return ", ".join(labels.get(mid, f"Disconnected ({mid[:14]})") for mid in sorted(ids))

    def _selected(self):
        row = self.table.currentRow()
        cell = self.table.item(row, 0) if row >= 0 else None
        return cell.data(USER_ROLE) if cell else None

    def _rule(self, identity):
        if not identity:
            return None
        kind, rid = identity
        sources = {"Interval": self.intervals, "Daily": self.daily, "Seasonal": self.seasons}
        return next((r for r in sources[kind] if r.id == rid), None)

    def refresh(self):
        previous = self._selected()
        try:
            self.config = load_automation()
            exclusive = normalize_interval_conflicts(self.config)
            if exclusive != self.config:
                save_automation(exclusive)
                self.config = exclusive
            self.intervals = list(self.config.rules)
            self.daily = load_schedules()
            self.seasons = load_calendar_rules()
        except (OSError, ValueError) as error:
            self.message.setText(f"Cannot load automation: {error}")
            for button in (self.edit_button, self.toggle_button, self.delete_button,
                           self.pause_overlaps_button):
                button.setEnabled(False)
            return
        self.health.setText(f"Background agent: {agent_health_text()}")
        self.background_choice.setChecked(self.config.background_enabled)
        self.pause_all.setText(
            "Resume all automations" if self.config.paused else "Pause all automations"
        )
        entries = inventory(self.intervals, self.daily, self.seasons)
        active = {m.device_id for m in self.monitors}
        self.overlaps = find_overlaps(entries, active, seasons=self.seasons)
        warnings = {entry.id: [] for entry in entries}
        for overlap in self.overlaps:
            for item in (overlap.first, overlap.second):
                warnings[item.id].append(overlap)
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        self.table.clearSelection()
        for index, entry in enumerate(entries):
            rule = self._rule((entry.kind, entry.id))
            if entry.kind == "Interval":
                frequency = f"Every {rule.interval_seconds // 60} minutes"
                next_due = datetime.fromtimestamp(rule.next_due_utc).astimezone().strftime("%d %b %H:%M")
                result = f"{next_due}; {rule.last_result}"
            elif entry.kind == "Daily":
                frequency = f"{rule.time} on days {','.join(str(day + 1) for day in rule.weekdays)}"
                result = rule.last_result
            else:
                frequency = (", ".join(str(m) for m in rule.months) if rule.period == "months"
                             else f"{rule.start_date} – {rule.end_date}")
                if rule.shuffle_interval_seconds:
                    frequency += f"; every {rule.shuffle_interval_seconds // 60}m"
                result = f"Priority {rule.priority}; {'Start' if rule.apply_on_start else 'No start'} / {'End' if rule.apply_on_end else 'No end'}"
            overlaps = warnings.get(entry.id, [])
            warning_count = sum(o.severity == "warning" for o in overlaps)
            overlap_text = f"{warning_count} warning(s)" if warning_count else (
                f"{len(overlaps)} coordinated" if overlaps else "None")
            values = (entry.kind, entry.name,
                      self._target_names(entry.target_ids, entry.global_target),
                      frequency, "Active" if entry.enabled else "Paused", result, overlap_text)
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value + ("\n" + "\n".join(o.explanation for o in overlaps) if col == 6 else ""))
                if col == 0:
                    cell.setData(USER_ROLE, (entry.kind, entry.id))
                self.table.setItem(index, col, cell)
            if previous == (entry.kind, entry.id):
                self.table.selectRow(index)
        self.table.blockSignals(False)
        self.message.setText(
            f"{len(entries)} saved automation(s); "
            f"{sum(o.severity == 'warning' for o in self.overlaps)} overlap warning(s). "
            "Conflicting intervals pause automatically; no rules are deleted."
        )
        self._buttons()

    def _buttons(self):
        identity = self._selected()
        selected = self._rule(identity)
        for button in (self.edit_button, self.toggle_button, self.delete_button):
            button.setEnabled(selected is not None)
        self.pause_overlaps_button.setEnabled(bool(selected and any(
            o.severity == "warning" and identity[1] in (o.first.id, o.second.id)
            for o in self.overlaps
        )))
        if selected:
            self.toggle_button.setText("Pause selected" if selected.enabled else "Resume selected")

    def _save_rules(self, kind, rules):
        if kind == "Interval":
            save_automation(replace(self.config, rules=tuple(rules)))
        elif kind == "Daily":
            save_schedules(rules)
        else:
            save_calendar_rules(rules)

    def _change(self, kind, rule_id, replacement=None, *, remove=False):
        sources = {"Interval": self.intervals, "Daily": self.daily, "Seasonal": self.seasons}
        if kind == "Interval":
            # Refresh at save time: the agent may have advanced a due timer while
            # this dialog was open. Keep the interval change in ONE atomic file.
            try:
                current = load_automation()
                previous = next((r for r in current.rules if r.id == rule_id), None)
                if previous is None:
                    raise ValueError("This interval no longer exists. Refresh the list.")
                conflicting = (overlapping_intervals(
                    current, replacement.monitor_ids, exclude_id=rule_id
                ) if not remove and replacement.enabled else ())
                if conflicting and not previous.enabled:
                    names = "\n".join(
                        f"• {self._target_names(r.monitor_ids)}" for r in conflicting
                    )
                    decision = QMessageBox.question(
                        self, "Conflicting automation",
                        f"Resuming this automation will pause {len(conflicting)} other "
                        f"enabled interval(s) using the same display(s):\n{names}\n\n"
                        "Continue? Paused rules remain saved.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if decision != QMessageBox.StandardButton.Yes:
                        return False
                updated = [r for r in current.rules if r.id != rule_id] if remove else [
                    replacement if r.id == rule_id else r for r in current.rules
                ]
                config = replace(current, rules=tuple(updated))
                if not remove and replacement.enabled:
                    config = enforce_interval_exclusivity(config, rule_id)
                save_automation(config)
            except (OSError, ValueError) as error:
                QMessageBox.critical(self, "Automation save failed", str(error))
                return False
        else:
            updated = ([r for r in sources[kind] if r.id != rule_id] if remove
                       else [replacement if r.id == rule_id else r for r in sources[kind]])
            try:
                self._save_rules(kind, updated)
            except (OSError, ValueError) as error:
                QMessageBox.critical(self, "Automation save failed", str(error))
                return False
        self.refresh()
        return True

    def _edit(self):
        identity = self._selected()
        chosen = self._rule(identity)
        if not chosen:
            return
        kind, _ = identity
        if kind == "Interval":
            editor = IntervalEditor(chosen, self.monitors, self.mapping, self)
            getter = lambda: editor.result(chosen)
        elif kind == "Daily":
            from wallpaper_orchestrator.schedule_view import ScheduleEditor
            editor = ScheduleEditor(self.monitors, self.mapping, chosen, self)
            getter = lambda: editor.value(chosen)
        else:
            with WallpaperCatalog() as catalog:
                collections, tags = catalog.list_collections(), catalog.list_tags()
            editor = CalendarRuleEditor(self.monitors, self.mapping, collections, tags, chosen, self)
            getter = lambda: editor.result_rule
        if editor.exec() == QDialog.DialogCode.Accepted:
            self._change(kind, chosen.id, getter())

    def _toggle(self):
        identity = self._selected()
        chosen = self._rule(identity)
        if chosen:
            self._change(identity[0], chosen.id, replace(chosen, enabled=not chosen.enabled))

    def _delete(self):
        identity = self._selected()
        chosen = self._rule(identity)
        if chosen and QMessageBox.question(
            self, "Delete automation", f"Permanently delete '{identity[0]}: {getattr(chosen, 'name', chosen.id)}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self._change(identity[0], chosen.id, remove=True)

    def _pause_overlaps(self):
        identity = self._selected()
        if not identity:
            return
        affected = {(o.second.kind, o.second.id) if identity[1] == o.first.id
                    else (o.first.kind, o.first.id)
                    for o in self.overlaps if o.severity == "warning" and
                    identity[1] in (o.first.id, o.second.id)}
        affected = {(kind, rid) for kind, rid in affected
                    if (r := self._rule((kind, rid))) is not None and r.enabled}
        if not affected:
            self.message.setText("No other enabled conflicting rules remain.")
            return
        details = "\n".join(f"• {kind}: {self._rule((kind, rid)).id[:10]}" for kind, rid in sorted(affected))
        if QMessageBox.question(
            self, "Pause overlapping automations",
            f"Pause these {len(affected)} other automation(s)?\n{details}\n\n"
            "They remain saved and can be resumed from this dashboard.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        by_kind = {"Interval": self.intervals, "Daily": self.daily, "Seasonal": self.seasons}
        try:
            for kind, source in by_kind.items():
                ids = {rid for label, rid in affected if label == kind}
                if ids:
                    self._save_rules(kind, [replace(r, enabled=False) if r.id in ids else r
                                            for r in source])
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Could not pause overlaps", str(error))
        self.refresh()

    def _configure_background(self):
        desired = self.background_choice.isChecked()
        try:
            set_background_enabled(desired)
        except Exception as error:
            QMessageBox.critical(self, "Background automation", str(error))
        self.refresh()

    def _toggle_global_pause(self):
        try:
            current = load_automation()
            save_automation(replace(current, paused=not current.paused))
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Pause automations", str(error))
        self.refresh()
