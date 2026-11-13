"""Focused C6 Library widgets; keep the main Library panel relatively small."""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTableWidget, QVBoxLayout,
)

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.indexer import SUPPORTED_EXTENSIONS
from wallpaper_orchestrator.intelligence import (
    SmartCollection, load_smart_collections, save_smart_collections,
)
from wallpaper_orchestrator.intelligence_view import SmartEditor
from wallpaper_orchestrator.library_tools import (
    copy_images_into_folder, find_exact_duplicates, import_images_to_collection,
)


ROLE = Qt.ItemDataRole.UserRole


class _FileDropMixin:
    """Receive OS file drops; the owner confirms copying before any mutation."""

    def _setup_drop(self):
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        # Icon-mode QListWidget can handle its viewport's drag events before
        # our widget overrides. Intercept external files at the viewport.
        if watched is self.viewport():
            kind = event.type()
            if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove,
                        QEvent.Type.Drop):
                paths = self._incoming(event)
                if paths:
                    if kind == QEvent.Type.Drop:
                        self.files_dropped.emit(paths)
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _incoming(event):
        if not event.mimeData().hasUrls():
            return ()
        return tuple(Path(url.toLocalFile()) for url in event.mimeData().urls()
                     if url.isLocalFile() and
                     Path(url.toLocalFile()).suffix.lower() in SUPPORTED_EXTENSIONS)

    def dragEnterEvent(self, event):
        if self._incoming(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._incoming(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._incoming(event)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class ImageDropTable(_FileDropMixin, QTableWidget):
    files_dropped = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_drop()


class ImageDropGrid(_FileDropMixin, QListWidget):
    files_dropped = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDragEnabled(False)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._setup_drop()


def _tag_list(tags, selected):
    widget = QListWidget()
    widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
    chosen = set(selected)
    for tag in tags:
        item = QListWidgetItem(tag.name)
        item.setData(ROLE, tag.id)
        widget.addItem(item)
        item.setSelected(tag.id in chosen)
    return widget


def _selected_ids(widget):
    return {item.data(ROLE) for item in widget.selectedItems()}


class TagFilterDialog(QDialog):
    def __init__(self, tags, include=(), exclude=(), match_all=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter by tags")
        self.resize(460, 510)
        form = QFormLayout(self)
        self.include = _tag_list(tags, include)
        self.exclude = _tag_list(tags, exclude)
        self.all = QCheckBox("Require all selected included tags")
        self.all.setChecked(match_all)
        form.addRow("Include", self.include)
        form.addRow("Exclude", self.exclude)
        form.addRow("", self.all)
        note = QLabel("Include and exclude filters apply to the current Library view only. "
                      "They never remove image tags or change automation rules.")
        note.setWordWrap(True)
        form.addRow("", note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def selection(self):
        return (_selected_ids(self.include), _selected_ids(self.exclude),
                self.all.isChecked())


class FolderDefaultsDialog(QDialog):
    def __init__(self, name, tags, selected=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Default tags — {name}")
        self.resize(420, 420)
        layout = QVBoxLayout(self)
        note = QLabel(
            "New images in this linked folder automatically receive the checked tags. "
            "Apply also tags existing indexed images. Removing a default does not "
            "erase tags already assigned to an image."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tags = _tag_list(tags, selected)
        layout.addWidget(self.tags)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_ids(self):
        return _selected_ids(self.tags)


class SmartCollectionsDialog(QDialog):
    """Focused C6 entry point for C5's existing tag-based smart collections."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Smart collections")
        self.resize(580, 450)
        layout = QVBoxLayout(self)
        description = QLabel("A smart collection contains indexed images that match its tags, "
                             "regardless of where the files are stored. Original files never move.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.items = QListWidget()
        layout.addWidget(self.items)
        buttons = QHBoxLayout()
        for label, method in (("+ New", self._add), ("Edit", self._edit),
                              ("Remove filter", self._remove)):
            button = QPushButton(label)
            button.clicked.connect(method)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._reload()

    def _reload(self):
        with WallpaperCatalog() as catalog:
            self.collections = catalog.list_collections()
            self.tags = catalog.list_tags()
        self.smart = load_smart_collections()
        names = {collection.id: collection.name for collection in self.collections}
        self.items.clear()
        for cid, rule in sorted(self.smart.items(), key=lambda pair: names.get(pair[0], "")):
            item = QListWidgetItem(f"{names.get(cid, 'Missing collection')}  ·  "
                                   f"{len(rule.include_tags)} included tag(s)")
            item.setData(ROLE, cid)
            self.items.addItem(item)

    def _chosen(self):
        item = self.items.currentItem()
        return item.data(ROLE) if item else None

    def _add(self):
        editor = SmartEditor(self.tags, parent=self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            with WallpaperCatalog() as catalog:
                collection = catalog.create_collection(editor.name.text().strip())
            specs = load_smart_collections()
            specs[collection.id] = SmartCollection(
                collection.id, tuple(_selected_ids(editor.include)),
                tuple(_selected_ids(editor.exclude)), editor.all_tags.isChecked(),
            )
            save_smart_collections(specs)
            self._reload()
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Smart collection", str(error))

    def _edit(self):
        cid = self._chosen()
        if cid is None:
            return
        names = {collection.id: collection.name for collection in self.collections}
        editor = SmartEditor(self.tags, self.smart[cid], names.get(cid, ""), self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            specs = load_smart_collections()
            specs[cid] = SmartCollection(
                cid, tuple(_selected_ids(editor.include)),
                tuple(_selected_ids(editor.exclude)), editor.all_tags.isChecked(),
            )
            save_smart_collections(specs)
            self._reload()
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Smart collection", str(error))

    def _remove(self):
        cid = self._chosen()
        if cid is None:
            return
        if QMessageBox.question(
            self, "Remove smart filter", "Keep the collection, but remove its automatic tag filter? "
            "No wallpaper files will be moved or deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            specs = load_smart_collections()
            specs.pop(cid, None)
            save_smart_collections(specs)
            self._reload()
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Smart collection", str(error))


class DuplicateScanWorker(QObject):
    """Hash potentially large images outside the GUI thread."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, paths):
        super().__init__()
        self.paths = tuple(paths)

    @Slot()
    def run(self):
        try:
            self.completed.emit(find_exact_duplicates(self.paths))
        except Exception as error:
            self.failed.emit(str(error))


class ImportWorker(QObject):
    """Copy dropped files outside the GUI thread; source files remain intact."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, paths, destination):
        super().__init__()
        self.paths = tuple(paths)
        self.destination = destination

    @Slot()
    def run(self):
        try:
            self.completed.emit(copy_images_into_folder(self.paths, self.destination))
        except Exception as error:
            self.failed.emit(str(error))


class ManualImportWorker(QObject):
    """Index manual collection members using a separate SQLite connection."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, paths, collection_id):
        super().__init__()
        self.paths = tuple(paths)
        self.collection_id = collection_id

    @Slot()
    def run(self):
        try:
            with WallpaperCatalog() as catalog:
                report = import_images_to_collection(catalog, self.collection_id, self.paths)
            self.completed.emit(report)
        except Exception as error:
            self.failed.emit(str(error))
