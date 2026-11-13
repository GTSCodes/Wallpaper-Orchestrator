"""C5 intelligent selection settings, kept out of the compact Displays panel."""

from dataclasses import replace
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.monitor_labels import monitor_labels
from wallpaper_orchestrator.intelligence import (
    ImageRule, MonitorGroup, SelectionPolicy, SmartCollection,
    load_selection_policy, load_smart_collections,
    save_selection_policy, save_smart_collections,
)


ROLE = Qt.ItemDataRole.UserRole


def _ids(widget: QListWidget) -> tuple[str, ...]:
    return tuple(widget.item(i).data(ROLE) for i in range(widget.count())
                 if widget.item(i).isSelected())


def _list(items, chosen=(), *, height=105) -> QListWidget:
    widget = QListWidget()
    widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
    widget.setMaximumHeight(height)
    chosen = set(chosen)
    existing = set()
    for label, identity in items:
        row = QListWidgetItem(label)
        row.setData(ROLE, identity)
        widget.addItem(row)
        row.setSelected(identity in chosen)
        existing.add(identity)
    for identity in chosen - existing:
        row = QListWidgetItem(f"Missing ({identity[:12]}…)")
        row.setData(ROLE, identity)
        widget.addItem(row)
        row.setSelected(True)
    return widget


class FilterEditor(QWidget):
    def __init__(self, collections, tags, rule=None, parent=None):
        super().__init__(parent)
        rule = rule or ImageRule()
        form = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItem("Require — only matching images", "require")
        self.mode.addItem("Prefer — matching images first", "prefer")
        self.mode.setCurrentIndex(0 if rule.mode == "require" else 1)
        form.addRow("Behaviour", self.mode)
        tag_items = [(tag.name, tag.id) for tag in tags]
        collection_items = [(item.name, item.id) for item in collections]
        self.include_tags = _list(tag_items, rule.include_tags)
        self.exclude_tags = _list(tag_items, rule.exclude_tags)
        self.include_collections = _list(collection_items, rule.include_collections)
        self.exclude_collections = _list(collection_items, rule.exclude_collections)
        form.addRow("Include tags (any)", self.include_tags)
        form.addRow("Exclude tags (any)", self.exclude_tags)
        form.addRow("Include collections (any)", self.include_collections)
        form.addRow("Exclude collections (any)", self.exclude_collections)
        note = QLabel("These filters never expand a display's assigned sources. "
                      "Require excludes nonmatches; Prefer only changes ordering.")
        note.setWordWrap(True)
        form.addRow("", note)

    def value(self) -> ImageRule:
        return ImageRule(
            self.mode.currentData(), _ids(self.include_tags), _ids(self.exclude_tags),
            _ids(self.include_collections), _ids(self.exclude_collections),
        )


class CapEditor(QWidget):
    """One compact row per stable collection/tag, -1 means unlimited."""

    def __init__(self, collections, tags, collection_limits=None, tag_limits=None):
        super().__init__()
        self.collection_rows = {}
        self.tag_rows = {}
        grid = QGridLayout(self)
        grid.addWidget(QLabel("Collection limits"), 0, 0)
        grid.addWidget(QLabel("Tag limits"), 0, 2)
        for column, entries, saved, output in (
            (0, collections, collection_limits or {}, self.collection_rows),
            (2, tags, tag_limits or {}, self.tag_rows),
        ):
            rows = [(entry.name, entry.id) for entry in entries]
            known = {identity for _, identity in rows}
            rows.extend((f"Missing ({identity[:12]}…)", identity)
                        for identity in saved if identity not in known)
            for index, (label, identity) in enumerate(rows, 1):
                spinner = QSpinBox()
                spinner.setRange(-1, 99)
                spinner.setSpecialValueText("Unlimited")
                spinner.setValue(saved.get(identity, -1))
                grid.addWidget(QLabel(label), index, column)
                grid.addWidget(spinner, index, column + 1)
                output[identity] = spinner
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

    def values(self) -> tuple[dict[str, int], dict[str, int]]:
        return (
            {identity: widget.value() for identity, widget in self.collection_rows.items()
             if widget.value() >= 0},
            {identity: widget.value() for identity, widget in self.tag_rows.items()
             if widget.value() >= 0},
        )


class GroupEditor(QDialog):
    def __init__(self, monitors, mapping, collections, tags, existing=None, parent=None):
        super().__init__(parent)
        self.existing = existing
        self.setWindowTitle("Monitor group")
        self.resize(770, 660)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        contents = QWidget()
        form = QFormLayout(contents)
        self.name = QLineEdit(existing.name if existing else "My display group")
        form.addRow("Group name", self.name)
        labels = monitor_labels(monitors, mapping)
        self.monitors = _list(
            [(labels[m.device_id], m.device_id) for m in monitors],
            existing.monitor_ids if existing else (), height=115,
        )
        form.addRow("Displays", self.monitors)
        self.rule = FilterEditor(collections, tags, existing.rule if existing else None)
        form.addRow("Shared filters", self.rule)
        self.caps = CapEditor(collections, tags,
            existing.collection_limits if existing else {},
            existing.tag_limits if existing else {})
        form.addRow("Group-only limits", self.caps)
        scroll.setWidget(contents)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.result_group = None

    def _accept(self):
        try:
            collection_caps, tag_caps = self.caps.values()
            self.result_group = MonitorGroup(
                self.existing.id if self.existing else uuid4().hex,
                self.name.text().strip(), _ids(self.monitors),
                self.rule.value(), collection_caps, tag_caps,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Monitor Group", str(error))
            return
        self.accept()


class SmartEditor(QDialog):
    def __init__(self, tags, existing=None, name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Smart collection")
        self.setMinimumWidth(470)
        self.existing = existing
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(name or "Winter Wallpapers")
        self.name.setEnabled(existing is None)
        form.addRow("Collection name", self.name)
        entries = [(tag.name, tag.id) for tag in tags]
        self.include = _list(entries, existing.include_tags if existing else ())
        self.exclude = _list(entries, existing.exclude_tags if existing else ())
        self.all_tags = QCheckBox("Require all included tags (instead of any)")
        self.all_tags.setChecked(existing.match_all if existing else False)
        form.addRow("Include tags", self.include)
        form.addRow("Exclude tags", self.exclude)
        form.addRow("", self.all_tags)
        layout.addLayout(form)
        description = QLabel(
            "A smart collection automatically includes every indexed image "
            "matching these tags, regardless of its physical folder."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        if not self.name.text().strip() or not _ids(self.include):
            QMessageBox.warning(self, "Smart Collection", "Choose a name and at least one tag.")
            return
        if set(_ids(self.include)) & set(_ids(self.exclude)):
            QMessageBox.warning(self, "Smart Collection", "A tag cannot be included and excluded.")
            return
        self.accept()


class IntelligenceDialog(QDialog):
    def __init__(self, monitors, mapping, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Intelligent wallpaper selection")
        self.resize(860, 740)
        self.monitors = list(monitors)
        self.mapping = dict(mapping)
        self.policy = load_selection_policy()
        self.monitor_rules = dict(self.policy.monitor_rules)
        self.groups = list(self.policy.groups)
        with WallpaperCatalog() as catalog:
            self.collections = catalog.list_collections()
            self.tags = catalog.list_tags()
        self.smart = load_smart_collections()

        outer = QVBoxLayout(self)
        introduction = QLabel("Control recent-image history, strict limits, "
                              "monitor preferences, groups and smart collections.")
        introduction.setWordWrap(True)
        outer.addWidget(introduction)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        general = QWidget()
        general_layout = QVBoxLayout(general)
        general_form = QFormLayout()
        self.recent = QSpinBox()
        self.recent.setRange(0, 100)
        self.recent.setValue(self.policy.recent_window)
        general_form.addRow("Recent-history window (N)", self.recent)
        self.recent_scope = QComboBox()
        self.recent_scope.addItem("Per display: last N wallpapers on that display", "monitor")
        self.recent_scope.addItem("Whole setup: images used in the last N shuffle events", "all")
        self.recent_scope.setCurrentIndex(0 if self.policy.recent_scope == "monitor" else 1)
        general_form.addRow("History scope", self.recent_scope)
        self.recent_mode = QComboBox()
        self.recent_mode.addItem("Prefer new images; fall back if necessary", "prefer")
        self.recent_mode.addItem("Require new images; report an error if exhausted", "require")
        self.recent_mode.setCurrentIndex(0 if self.policy.recent_mode == "prefer" else 1)
        general_form.addRow("History behaviour", self.recent_mode)
        self.duplicates = QCheckBox("Prevent the same image appearing on multiple displays")
        self.duplicates.setChecked(self.policy.avoid_duplicates)
        general_form.addRow("", self.duplicates)
        general_layout.addLayout(general_form)
        cap_scroll = QScrollArea()
        cap_scroll.setWidgetResizable(True)
        self.caps = CapEditor(self.collections, self.tags,
                              self.policy.collection_limits, self.policy.tag_limits)
        cap_scroll.setWidget(self.caps)
        general_layout.addWidget(cap_scroll, 1)
        self.tabs.addTab(general, "Global limits")

        monitor_page = QWidget()
        monitor_layout = QVBoxLayout(monitor_page)
        self.monitor_choice = QComboBox()
        # Display names such as "landscape" are not unique.  Reuse the same
        # identifiable labels shown in Automation Centre, but store the full
        # device ID as the combo's user data so saved rules remain stable.
        labels = monitor_labels(self.monitors, self.mapping)
        for monitor in self.monitors:
            label = labels[monitor.device_id]
            self.monitor_choice.addItem(label, monitor.device_id)
            self.monitor_choice.setItemData(
                self.monitor_choice.count() - 1,
                label,
                Qt.ItemDataRole.ToolTipRole,
            )
        monitor_layout.addWidget(self.monitor_choice)
        self.monitor_rule_slot = QVBoxLayout()
        monitor_layout.addLayout(self.monitor_rule_slot)
        save_monitor = QPushButton("Save this display's rule")
        save_monitor.clicked.connect(self._save_monitor)
        monitor_layout.addWidget(save_monitor)
        self.monitor_choice.currentIndexChanged.connect(self._show_monitor)
        self._shown_mid = None
        self._show_monitor()
        self.tabs.addTab(monitor_page, "Per-display rules")

        group_page = QWidget()
        group_layout = QVBoxLayout(group_page)
        self.group_list = QListWidget()
        group_layout.addWidget(self.group_list)
        group_buttons = QHBoxLayout()
        for title, action in (("+ Group", self._add_group), ("Edit", self._edit_group),
                              ("Remove", self._remove_group)):
            button = QPushButton(title)
            button.clicked.connect(action)
            group_buttons.addWidget(button)
        group_buttons.addStretch()
        group_layout.addLayout(group_buttons)
        self.tabs.addTab(group_page, "Monitor groups")
        self._render_groups()

        smart_page = QWidget()
        smart_layout = QVBoxLayout(smart_page)
        self.smart_list = QListWidget()
        smart_layout.addWidget(self.smart_list)
        smart_buttons = QHBoxLayout()
        for title, action in (("+ Smart collection", self._add_smart),
                              ("Edit", self._edit_smart), ("Remove filter", self._remove_smart)):
            button = QPushButton(title)
            button.clicked.connect(action)
            smart_buttons.addWidget(button)
        smart_layout.addLayout(smart_buttons)
        note = QLabel("Removing a smart filter preserves the collection and original image files.")
        note.setWordWrap(True)
        smart_layout.addWidget(note)
        self.tabs.addTab(smart_page, "Smart collections")
        self._render_smart()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _show_monitor(self):
        if hasattr(self, "monitor_editor"):
            try:
                if self._shown_mid:
                    self.monitor_rules[self._shown_mid] = self.monitor_editor.value()
            except ValueError:
                # The final Save validates invalid filters, so keep the last
                # valid rule instead of silently storing a malformed one.
                pass
            self.monitor_rule_slot.removeWidget(self.monitor_editor)
            self.monitor_editor.deleteLater()
        mid = self.monitor_choice.currentData()
        self._shown_mid = mid
        self.monitor_editor = FilterEditor(self.collections, self.tags,
                                           self.monitor_rules.get(mid))
        self.monitor_rule_slot.addWidget(self.monitor_editor)

    def _save_monitor(self):
        mid = self.monitor_choice.currentData()
        if mid:
            try:
                self.monitor_rules[mid] = self.monitor_editor.value()
                QMessageBox.information(self, "Display Rule", "Stored in this dialog. Click Save to apply all rules.")
            except ValueError as error:
                QMessageBox.warning(self, "Display Rule", str(error))

    def _selected_group(self):
        row = self.group_list.currentRow()
        return row if 0 <= row < len(self.groups) else None

    def _render_groups(self):
        self.group_list.clear()
        for group in self.groups:
            self.group_list.addItem(f"{group.name} — {len(group.monitor_ids)} display(s), {group.rule.mode}")

    def _group_editor(self, existing=None):
        editor = GroupEditor(self.monitors, self.mapping, self.collections, self.tags, existing, self)
        return editor.result_group if editor.exec() == QDialog.DialogCode.Accepted else None

    def _add_group(self):
        group = self._group_editor()
        if group:
            self.groups.append(group)
            self._render_groups()

    def _edit_group(self):
        row = self._selected_group()
        if row is not None:
            group = self._group_editor(self.groups[row])
            if group:
                self.groups[row] = group
                self._render_groups()

    def _remove_group(self):
        row = self._selected_group()
        if row is not None:
            del self.groups[row]
            self._render_groups()

    def _selected_smart(self):
        item = self.smart_list.currentItem()
        return item.data(ROLE) if item else None

    def _render_smart(self):
        self.smart_list.clear()
        names = {collection.id: collection.name for collection in self.collections}
        for cid, spec in sorted(self.smart.items(), key=lambda item: names.get(item[0], item[0])):
            row = QListWidgetItem(f"{names.get(cid, 'Missing collection')} — "
                                  f"{len(spec.include_tags)} included tag(s)")
            row.setData(ROLE, cid)
            self.smart_list.addItem(row)

    def _add_smart(self):
        editor = SmartEditor(self.tags, parent=self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            with WallpaperCatalog() as catalog:
                collection = catalog.create_collection(editor.name.text().strip())
            self.collections.append(collection)
            self.smart[collection.id] = SmartCollection(
                collection.id, _ids(editor.include), _ids(editor.exclude),
                editor.all_tags.isChecked(),
            )
            save_smart_collections(self.smart)
            self._render_smart()
            QMessageBox.information(self, "Smart Collection", "Collection created; it is now available in the Library.")
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Smart Collection", str(error))

    def _edit_smart(self):
        cid = self._selected_smart()
        if cid is None:
            return
        name = next((c.name for c in self.collections if c.id == cid), "")
        editor = SmartEditor(self.tags, self.smart[cid], name, self)
        if editor.exec() == QDialog.DialogCode.Accepted:
            changed = dict(self.smart)
            changed[cid] = SmartCollection(cid, _ids(editor.include),
                                           _ids(editor.exclude), editor.all_tags.isChecked())
            try:
                save_smart_collections(changed)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "Smart Collection", str(error))
                return
            self.smart = changed
            self._render_smart()

    def _remove_smart(self):
        cid = self._selected_smart()
        if cid is None:
            return
        if QMessageBox.question(self, "Remove Smart Filter",
                                "Turn this into an ordinary collection? Existing files will not be moved.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        changed = dict(self.smart)
        del changed[cid]
        try:
            save_smart_collections(changed)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Smart Collection", str(error))
            return
        self.smart = changed
        self._render_smart()

    def _accept(self):
        try:
            mid = self.monitor_choice.currentData()
            if mid:
                self.monitor_rules[mid] = self.monitor_editor.value()
            collection_caps, tag_caps = self.caps.values()
            policy = SelectionPolicy(
                recent_window=self.recent.value(), recent_mode=self.recent_mode.currentData(),
                recent_scope=self.recent_scope.currentData(),
                avoid_duplicates=self.duplicates.isChecked(),
                collection_limits=collection_caps, tag_limits=tag_caps,
                monitor_rules=self.monitor_rules, groups=tuple(self.groups),
            )
            save_selection_policy(policy)
        except (ValueError, OSError) as error:
            QMessageBox.critical(self, "Selection Rule Error", str(error))
            return
        self.accept()
