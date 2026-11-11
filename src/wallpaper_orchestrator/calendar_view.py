"""C4 seasonal rule management: advanced dialogs, no Displays layout changes."""

from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from wallpaper_orchestrator.calendar_rules import (
    ALL_WEEKDAYS, CalendarRule, create_calendar_rule, load_calendar_rules,
    matches_calendar, save_calendar_rules,
)
from wallpaper_orchestrator.catalog import WallpaperCatalog


USER_ROLE = Qt.ItemDataRole.UserRole
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
               "Sep", "Oct", "Nov", "Dec")


def _selected_ids(widget: QListWidget) -> tuple[str, ...]:
    return tuple(widget.item(index).data(USER_ROLE)
                 for index in range(widget.count())
                 if widget.item(index).isSelected())


def _select_ids(widget: QListWidget, ids) -> None:
    chosen = set(ids)
    for index in range(widget.count()):
        widget.item(index).setSelected(widget.item(index).data(USER_ROLE) in chosen)


def _reference_list(items, selected_ids=(), *, label="Missing entry") -> QListWidget:
    """Preserve references to renamed/deleted/disconnected items when editing."""
    widget = QListWidget()
    widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
    widget.setMaximumHeight(102)
    present = set()
    for name, identity in items:
        row = QListWidgetItem(name)
        row.setData(USER_ROLE, identity)
        widget.addItem(row)
        present.add(identity)
    for identity in selected_ids:
        if identity not in present:
            row = QListWidgetItem(f"{label} ({identity[:12]}…)")
            row.setData(USER_ROLE, identity)
            widget.addItem(row)
    _select_ids(widget, selected_ids)
    return widget


class CalendarRuleEditor(QDialog):
    """Edit month/date, time/day, monitor and tag/collection filters."""

    def __init__(self, monitors, mapping, collections, tags,
                 existing: CalendarRule | None = None, parent=None):
        super().__init__(parent)
        self.existing = existing
        self.result_rule = None
        self.setWindowTitle("Edit seasonal rule" if existing else "New seasonal rule")
        self.resize(630, 690)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        form = QFormLayout(content)
        form.setSpacing(10)

        self.name = QLineEdit(existing.name if existing else "December wallpapers")
        form.addRow("Rule name", self.name)
        self.priority = QSpinBox()
        self.priority.setRange(0, 100)
        self.priority.setValue(existing.priority if existing else 50)
        self.priority.setToolTip("Higher priority wins when multiple rules are active.")
        form.addRow("Priority (0–100)", self.priority)
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(existing.enabled if existing else True)
        form.addRow("", self.enabled)

        self.all_monitors = QCheckBox("All eligible displays")
        self.all_monitors.setChecked(not existing or not existing.target_ids)
        form.addRow("Applies to", self.all_monitors)
        labels = {device_id: role for role, device_id in mapping.items()}
        choices = [
            (f"{labels.get(m.device_id, m.orientation)} "
             f"({m.width} × {m.height})", m.device_id)
            for m in monitors
        ]
        self.monitor_list = _reference_list(
            choices, existing.target_ids if existing else (), label="Disconnected monitor"
        )
        self.monitor_list.setEnabled(not self.all_monitors.isChecked())
        self.all_monitors.toggled.connect(
            lambda checked: self.monitor_list.setEnabled(not checked)
        )
        form.addRow("Specific displays", self.monitor_list)

        self.period = QComboBox()
        self.period.addItem("Selected months", "months")
        self.period.addItem("Date range (yearly)", "range")
        self.period.setCurrentIndex(0 if not existing or existing.period == "months" else 1)
        form.addRow("Active period", self.period)

        self.months_widget = QWidget()
        month_grid = QGridLayout(self.months_widget)
        month_grid.setContentsMargins(0, 0, 0, 0)
        self.month_checks = []
        active_months = existing.months if existing else (12,)
        for month, name in enumerate(MONTH_NAMES, 1):
            box = QCheckBox(name)
            box.setChecked(month in active_months)
            self.month_checks.append(box)
            month_grid.addWidget(box, (month - 1) // 4, (month - 1) % 4)
        form.addRow("Months", self.months_widget)

        self.range_widget = QWidget()
        range_layout = QHBoxLayout(self.range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        self.start_date = QLineEdit(existing.start_date if existing and existing.start_date else "12-01")
        self.end_date = QLineEdit(existing.end_date if existing and existing.end_date else "01-06")
        self.start_date.setPlaceholderText("MM-DD")
        self.end_date.setPlaceholderText("MM-DD")
        self.start_date.setMaximumWidth(90)
        self.end_date.setMaximumWidth(90)
        range_layout.addWidget(QLabel("From"))
        range_layout.addWidget(self.start_date)
        range_layout.addWidget(QLabel("through"))
        range_layout.addWidget(self.end_date)
        range_layout.addStretch()
        form.addRow("Annual range", self.range_widget)
        self.period.currentIndexChanged.connect(self._update_period)
        self._update_period()

        weekdays_widget = QWidget()
        days = QHBoxLayout(weekdays_widget)
        days.setContentsMargins(0, 0, 0, 0)
        self.days = []
        for day, label in enumerate(DAY_NAMES):
            box = QCheckBox(label)
            box.setChecked(day in (existing.weekdays if existing else ALL_WEEKDAYS))
            self.days.append(box)
            days.addWidget(box)
        form.addRow("Weekdays", weekdays_widget)

        self.all_day = QCheckBox("All day")
        self.all_day.setChecked(existing.all_day if existing else True)
        form.addRow("Time window", self.all_day)
        time_widget = QWidget()
        times = QHBoxLayout(time_widget)
        times.setContentsMargins(0, 0, 0, 0)
        self.start_time = QTimeEdit()
        self.end_time = QTimeEdit()
        self.start_time.setDisplayFormat("HH:mm")
        self.end_time.setDisplayFormat("HH:mm")
        first = existing.start_time if existing else "09:00"
        last = existing.end_time if existing else "21:00"
        self.start_time.setTime(QTime(*map(int, first.split(":"))))
        self.end_time.setTime(QTime(*map(int, last.split(":"))))
        times.addWidget(QLabel("From"))
        times.addWidget(self.start_time)
        times.addWidget(QLabel("until"))
        times.addWidget(self.end_time)
        times.addStretch()
        form.addRow("Local times", time_widget)
        self.all_day.toggled.connect(self._update_time)
        self._update_time()

        self.collections = _reference_list(
            [(entry.name, entry.id) for entry in collections],
            existing.collection_ids if existing else (), label="Deleted collection",
        )
        form.addRow("In collections (any)", self.collections)
        self.include_tags = _reference_list(
            [(tag.name, tag.id) for tag in tags],
            existing.include_tag_ids if existing else (), label="Deleted tag",
        )
        form.addRow("Include tags (any)", self.include_tags)
        self.exclude_tags = _reference_list(
            [(tag.name, tag.id) for tag in tags],
            existing.exclude_tag_ids if existing else (), label="Deleted tag",
        )
        form.addRow("Exclude tags (any)", self.exclude_tags)

        self.behavior = QComboBox()
        self.behavior.addItem("Prefer matches; use normal wallpapers if none", "prefer")
        self.behavior.addItem("Require matches; report an error if none", "require")
        self.behavior.setCurrentIndex(0 if not existing or existing.behavior == "prefer" else 1)
        form.addRow("No matching images", self.behavior)

        note = QLabel(
            "Rules only filter wallpapers already eligible from each monitor's "
            "current folders/collections and orientation. They do not move files, "
            "change subscriptions or trigger a wallpaper change by themselves. "
            "They take effect on the next manual, interval or daily update. "
            "At equal priority a display-specific rule wins."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #aab8ca;")
        form.addRow("", note)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _update_period(self):
        is_months = self.period.currentData() == "months"
        self.months_widget.setVisible(is_months)
        self.range_widget.setVisible(not is_months)

    def _update_time(self):
        enabled = not self.all_day.isChecked()
        self.start_time.setEnabled(enabled)
        self.end_time.setEnabled(enabled)

    def value(self) -> CalendarRule:
        if not self.all_monitors.isChecked() and not _selected_ids(self.monitor_list):
            raise ValueError("Choose at least one monitor, or select all displays.")
        period = self.period.currentData()
        rule = create_calendar_rule(
            self.name.text().strip(), priority=self.priority.value(),
            enabled=self.enabled.isChecked(),
            target_ids=() if self.all_monitors.isChecked() else _selected_ids(self.monitor_list),
            period=period,
            months=tuple(i for i, box in enumerate(self.month_checks, 1) if box.isChecked())
            if period == "months" else (),
            start_date=None if period == "months" else self.start_date.text().strip(),
            end_date=None if period == "months" else self.end_date.text().strip(),
            weekdays=tuple(i for i, box in enumerate(self.days) if box.isChecked()),
            all_day=self.all_day.isChecked(),
            start_time=self.start_time.time().toString("HH:mm"),
            end_time=self.end_time.time().toString("HH:mm"),
            collection_ids=_selected_ids(self.collections),
            include_tag_ids=_selected_ids(self.include_tags),
            exclude_tag_ids=_selected_ids(self.exclude_tags),
            behavior=self.behavior.currentData(),
        )
        return replace(rule, id=self.existing.id) if self.existing else rule

    def _accept(self):
        try:
            self.result_rule = self.value()
        except ValueError as error:
            QMessageBox.warning(self, "Seasonal Rule", str(error))
            return
        self.accept()


class CalendarRulesDialog(QDialog):
    """Compact advanced rule manager, opened from the Schedules tab."""

    def __init__(self, monitors, mapping, parent=None):
        super().__init__(parent)
        self.monitors = list(monitors)
        self.mapping = dict(mapping)
        self.setWindowTitle("Seasonal and calendar rules")
        self.resize(910, 475)
        self.error = None
        try:
            self.rules = load_calendar_rules()
        except (OSError, ValueError) as error:
            self.rules = []
            self.error = str(error)
        outer = QVBoxLayout(self)
        title = QLabel("Seasonal wallpaper rules")
        title.setStyleSheet("font-size: 19px; font-weight: 500;")
        outer.addWidget(title)
        explanation = QLabel(
            "Use calendar dates, weekdays, local times and tags to filter what "
            "each display may show. Your existing Automate intervals and daily "
            "schedules determine when changes happen."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #aab8ca;")
        outer.addWidget(explanation)
        actions = QHBoxLayout()
        self.new_button = QPushButton("+ New rule")
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        actions.addWidget(self.new_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        outer.addLayout(actions)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Period", "Window", "Displays", "Filters", "Priority", "Now"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table, 1)
        self.status = QLabel(self.error or "Highest-priority active rule wins for each display.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #aab8ca;")
        outer.addWidget(self.status)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        outer.addWidget(close)

        self.new_button.clicked.connect(self._new)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._render()

    def _selected_rule(self) -> CalendarRule | None:
        item = self.table.item(self.table.currentRow(), 0)
        selected_id = item.data(USER_ROLE) if item else None
        return next((rule for rule in self.rules if rule.id == selected_id), None)

    def _render(self):
        chosen = self._selected_rule()
        with WallpaperCatalog() as catalog:
            tags = {tag.id: tag.name for tag in catalog.list_tags()}
            collections = {item.id: item.name for item in catalog.list_collections()}
        labels = {device_id: role for role, device_id in self.mapping.items()}
        now = datetime.now().astimezone()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rules))
        for index, rule in enumerate(sorted(self.rules, key=lambda r: (-r.priority, r.name.casefold()))):
            if rule.period == "months":
                period = ", ".join(MONTH_NAMES[month - 1] for month in rule.months)
            else:
                period = f"{rule.start_date} – {rule.end_date}"
            weekdays = "Every day" if rule.weekdays == ALL_WEEKDAYS else ", ".join(
                DAY_NAMES[day] for day in rule.weekdays
            )
            window = weekdays + ("" if rule.all_day else f" {rule.start_time}–{rule.end_time}")
            target = "All" if not rule.target_ids else ", ".join(
                labels.get(device_id, device_id[:8]) for device_id in rule.target_ids
            )
            parts = []
            if rule.collection_ids:
                parts.append("Collections: " + ", ".join(collections.get(cid, "Missing") for cid in rule.collection_ids))
            if rule.include_tag_ids:
                parts.append("Tags: " + ", ".join(tags.get(tid, "Missing") for tid in rule.include_tag_ids))
            if rule.exclude_tag_ids:
                parts.append("Not: " + ", ".join(tags.get(tid, "Missing") for tid in rule.exclude_tag_ids))
            active = rule.enabled and any(
                matches_calendar(rule, now, m.device_id) for m in self.monitors
            )
            cells = (rule.name, period, window, target,
                     "; ".join(parts) + f" ({rule.behavior})",
                     str(rule.priority), "Active" if active else "Inactive")
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(USER_ROLE, rule.id)
                self.table.setItem(index, column, item)
            if chosen and chosen.id == rule.id:
                self.table.selectRow(index)
        self.table.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self):
        selected = self._selected_rule() is not None
        self.new_button.setEnabled(self.error is None)
        self.edit_button.setEnabled(selected and self.error is None)
        self.delete_button.setEnabled(selected and self.error is None)

    def _references(self):
        with WallpaperCatalog() as catalog:
            return catalog.list_collections(), catalog.list_tags()

    def _save(self, rules):
        try:
            save_calendar_rules(rules)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Calendar Save Error", str(error))
            return False
        self.rules = rules
        self._render()
        self.status.setText("Saved. Rule filters apply on the next wallpaper update.")
        return True

    def _new(self):
        collections, tags = self._references()
        editor = CalendarRuleEditor(self.monitors, self.mapping, collections, tags, parent=self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self._save([*self.rules, editor.result_rule])

    def _edit(self):
        chosen = self._selected_rule()
        if chosen is None:
            return
        collections, tags = self._references()
        editor = CalendarRuleEditor(self.monitors, self.mapping, collections, tags, chosen, self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self._save([editor.result_rule if rule.id == chosen.id else rule
                        for rule in self.rules])

    def _delete(self):
        chosen = self._selected_rule()
        if chosen is None:
            return
        if QMessageBox.question(
            self, "Delete Seasonal Rule", f"Delete '{chosen.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._save([rule for rule in self.rules if rule.id != chosen.id])
