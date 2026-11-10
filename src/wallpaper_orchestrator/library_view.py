"""Library management UI for Wallpaper Orchestrator (Milestones B3/B4).

This widget does not detect monitors or change wallpapers. Every operation
uses the B1/B2 catalogue API, so the original image files remain untouched.
B4 watches linked folders for automatic background indexing.
"""

import threading
import time
import warnings
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (
    QObject, QSize, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.indexer import scan_all, scan_folder
from wallpaper_orchestrator.watcher import FolderChangeDetector


USER_ROLE = Qt.ItemDataRole.UserRole
MAX_PREVIEW_PIXELS = 80_000_000


class ScanWorker(QObject):
    """Run folder discovery in a worker thread with its *own* SQLite handle."""

    completed = Signal(object, object)
    failed = Signal(str)

    def __init__(self, collection_id: str | None, folder_ids: tuple[str, ...] | None = None):
        super().__init__()
        self.collection_id = collection_id
        self.folder_ids = folder_ids

    @Slot()
    def run(self) -> None:
        try:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always", Image.DecompressionBombWarning)
                with WallpaperCatalog() as catalog:
                    if self.folder_ids is None:
                        reports = scan_all(catalog, collection_id=self.collection_id)
                    else:
                        reports = []
                        for folder_id in self.folder_ids:
                            try:
                                catalog.get_folder_link(folder_id)
                            except KeyError:
                                continue  # A link can be removed while queued.
                            reports.append(scan_folder(catalog, folder_id))
            warning_text = [str(warning.message) for warning in captured]
            self.completed.emit(reports, warning_text)
        except Exception as error:
            self.failed.emit(str(error))


class AutoScanSignals(QObject):
    """Transport filesystem notifications to Qt's UI thread safely."""

    changed = Signal(object)
    failed = Signal(str)


class LibraryPanel(QWidget):
    """Manage collections, folders, indexed images and their descriptive tags."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_records = []
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._scan_automatic = False
        self._scan_auto_ids: tuple[str, ...] = ()
        self._pending_auto_ids: set[str] = set()
        self._auto_retry_after = 0.0
        self._auto_failures = 0
        self._settings = QSettings("WallpaperOrchestrator", "WallpaperOrchestrator")
        self._watch_stop = threading.Event()
        self._watch_wakeup = threading.Event()
        self._watch_active = threading.Event()
        self._watch_signals = AutoScanSignals(self)
        self._watch_signals.changed.connect(self._watch_changed)
        self._watch_signals.failed.connect(self._watch_failed)
        self._auto_scan_timer = QTimer(self)
        self._auto_scan_timer.setSingleShot(True)
        self._auto_scan_timer.timeout.connect(self._perform_automatic_scan)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(9)

        title = QLabel("Wallpaper Library")
        title.setObjectName("libraryTitle")
        outer.addWidget(title)
        subtitle = QLabel(
            "Organise existing wallpaper folders without moving the original files."
        )
        subtitle.setObjectName("libraryMuted")
        outer.addWidget(subtitle)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)

        left = QWidget()
        left.setMinimumWidth(245)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 5, 0)
        left_layout.setSpacing(7)
        left_layout.addWidget(self._caption("COLLECTIONS"))
        self.collections = QListWidget()
        self.collections.setObjectName("libraryList")
        self.collections.currentItemChanged.connect(self._collection_changed)
        left_layout.addWidget(self.collections, 2)
        collection_buttons = QHBoxLayout()
        self.add_collection_button = QPushButton("+ New")
        self.rename_collection_button = QPushButton("Rename")
        self.delete_collection_button = QPushButton("Delete")
        collection_buttons.addWidget(self.add_collection_button)
        collection_buttons.addWidget(self.rename_collection_button)
        collection_buttons.addWidget(self.delete_collection_button)
        left_layout.addLayout(collection_buttons)

        left_layout.addWidget(self._caption("LINKED FOLDERS"))
        self.folders = QListWidget()
        self.folders.setObjectName("libraryList")
        self.folders.currentItemChanged.connect(self._sync_buttons)
        self.folders.setToolTip("One collection can reference several folders")
        left_layout.addWidget(self.folders, 2)
        folder_buttons = QHBoxLayout()
        self.add_folder_button = QPushButton("+ Folder")
        self.remove_folder_button = QPushButton("Unlink")
        folder_buttons.addWidget(self.add_folder_button)
        folder_buttons.addWidget(self.remove_folder_button)
        left_layout.addLayout(folder_buttons)
        location_buttons = QHBoxLayout()
        self.open_folder_button = QPushButton("Open folder")
        self.copy_folder_button = QPushButton("Copy path")
        location_buttons.addWidget(self.open_folder_button)
        location_buttons.addWidget(self.copy_folder_button)
        left_layout.addLayout(location_buttons)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(7, 0, 0, 0)
        right_layout.setSpacing(8)
        top_row = QHBoxLayout()
        self.collection_summary = QLabel("Select a collection")
        self.collection_summary.setObjectName("librarySectionTitle")
        top_row.addWidget(self.collection_summary, 1)
        self.scan_collection_button = QPushButton("Scan collection")
        self.scan_all_button = QPushButton("Scan all")
        top_row.addWidget(self.scan_collection_button)
        top_row.addWidget(self.scan_all_button)
        self.auto_scan_checkbox = QCheckBox("Auto-scan")
        self.auto_scan_checkbox.setToolTip(
            "Watch linked folders for new, moved, edited or removed images. "
            "Index them in the background without applying a wallpaper."
        )
        self.auto_scan_checkbox.setChecked(
            self._settings.value("library_auto_scan_enabled", True, type=bool)
        )
        top_row.addWidget(self.auto_scan_checkbox)
        right_layout.addLayout(top_row)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filenames...")
        self.search.textChanged.connect(self._filter_images)
        self.orientation_filter = QComboBox()
        for label, value in (
            ("All orientations", "all"), ("Portrait", "portrait"),
            ("Landscape", "landscape"), ("Square", "square"),
        ):
            self.orientation_filter.addItem(label, value)
        self.orientation_filter.currentIndexChanged.connect(self._filter_images)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.orientation_filter)
        right_layout.addLayout(search_row)

        self.images = QTableWidget(0, 4)
        self.images.setObjectName("libraryImages")
        self.images.setHorizontalHeaderLabels(
            ["Image", "Dimensions", "Orientation", "Status"]
        )
        self.images.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.images.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.images.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.images.setAlternatingRowColors(True)
        self.images.setShowGrid(False)
        self.images.verticalHeader().setVisible(False)
        self.images.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for index in (1, 2, 3):
            self.images.horizontalHeader().setSectionResizeMode(
                index, QHeaderView.ResizeMode.ResizeToContents
            )
        self.images.itemSelectionChanged.connect(self._image_changed)
        right_layout.addWidget(self.images, 3)

        details = QHBoxLayout()
        details.setSpacing(12)
        self.preview = QLabel("Select a wallpaper to preview")
        self.preview.setObjectName("libraryPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(250, 165)
        self.preview.setWordWrap(True)
        details.addWidget(self.preview)

        detail_side = QVBoxLayout()
        self.image_info = QLabel("Image details and tags will appear here.")
        self.image_info.setObjectName("libraryMuted")
        self.image_info.setWordWrap(True)
        detail_side.addWidget(self.image_info)
        detail_side.addWidget(self._caption("IMAGE TAGS"))
        self.image_tags = QListWidget()
        self.image_tags.setObjectName("libraryTags")
        self.image_tags.setMaximumHeight(72)
        detail_side.addWidget(self.image_tags)
        tag_actions = QHBoxLayout()
        self.tag_choice = QComboBox()
        self.tag_choice.setMinimumWidth(95)
        self.add_tag_button = QPushButton("Assign")
        self.remove_tag_button = QPushButton("Remove")
        tag_actions.addWidget(self.tag_choice, 1)
        tag_actions.addWidget(self.add_tag_button)
        tag_actions.addWidget(self.remove_tag_button)
        detail_side.addLayout(tag_actions)

        tag_management = QHBoxLayout()
        self.new_tag_button = QPushButton("New tag")
        self.rename_tag_button = QPushButton("Rename tag")
        self.delete_tag_button = QPushButton("Delete tag")
        tag_management.addWidget(self.new_tag_button)
        tag_management.addWidget(self.rename_tag_button)
        tag_management.addWidget(self.delete_tag_button)
        detail_side.addLayout(tag_management)
        details.addLayout(detail_side, 1)
        right_layout.addLayout(details, 1)
        splitter.addWidget(right)
        splitter.setSizes([300, 740])

        self.status = QLabel("Library ready. Linked-folder monitoring is available.")
        self.status.setObjectName("libraryMuted")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.setStyleSheet("""
            QLabel#libraryTitle { font-size: 21px; font-weight: 500; }
            QLabel#librarySectionTitle { font-size: 15px; font-weight: 500; }
            QLabel#libraryMuted { color: #9ca5b1; }
            QLabel#libraryCaption { color: #aab8ca; font-size: 11px;
                                     font-weight: 600; }
            QListWidget#libraryList, QListWidget#libraryTags,
            QTableWidget#libraryImages {
                background: #171b23; border: 1px solid #38404d;
                border-radius: 5px; color: #e8e9ec;
                alternate-background-color: #1b2029;
                selection-background-color: #344e6b;
            }
            QHeaderView::section { background: #252c37; color: #cbd5e1;
                border: 0; border-right: 1px solid #38404d; padding: 6px; }
            QLabel#libraryPreview { background: #171b23; border: 1px solid #38404d;
                                     border-radius: 6px; color: #8793a2; }
            QSplitter::handle { background: #2a323f; width: 3px; }
        """)

        self.add_collection_button.clicked.connect(self._add_collection)
        self.rename_collection_button.clicked.connect(self._rename_collection)
        self.delete_collection_button.clicked.connect(self._delete_collection)
        self.add_folder_button.clicked.connect(self._add_folder)
        self.remove_folder_button.clicked.connect(self._remove_folder)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.copy_folder_button.clicked.connect(self._copy_folder_path)
        self.scan_collection_button.clicked.connect(
            lambda: self._start_scan(self.selected_collection_id())
        )
        self.scan_all_button.clicked.connect(lambda: self._start_scan(None))
        self.auto_scan_checkbox.toggled.connect(self._auto_scan_toggled)
        self.add_tag_button.clicked.connect(self._assign_tag)
        self.remove_tag_button.clicked.connect(self._remove_image_tag)
        self.new_tag_button.clicked.connect(self._new_tag)
        self.rename_tag_button.clicked.connect(self._rename_tag)
        self.delete_tag_button.clicked.connect(self._delete_tag)
        self.image_tags.currentItemChanged.connect(self._sync_buttons)
        self.tag_choice.currentIndexChanged.connect(self._sync_buttons)

        self.refresh_collections()
        if self.auto_scan_checkbox.isChecked():
            self._watch_active.set()
        self._watch_thread = threading.Thread(
            target=self._watch_loop, name="WallpaperAutoIndex", daemon=True
        )
        self._watch_thread.start()

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("libraryCaption")
        return label

    def selected_collection_id(self) -> str | None:
        item = self.collections.currentItem()
        return item.data(USER_ROLE) if item else None

    def selected_folder_id(self) -> str | None:
        item = self.folders.currentItem()
        return item.data(USER_ROLE) if item else None

    def selected_image_id(self) -> str | None:
        selected = self.images.selectionModel().selectedRows()
        if not selected:
            return None
        cell = self.images.item(selected[0].row(), 0)
        return cell.data(USER_ROLE) if cell else None

    def _error(self, message: str) -> None:
        QMessageBox.critical(self, "Library Error", message)

    def refresh_collections(self, preferred_id: str | None = None) -> None:
        selected = preferred_id or self.selected_collection_id()
        try:
            with WallpaperCatalog() as catalog:
                collections = catalog.list_collections()
                tags = catalog.list_tags()
        except Exception as error:
            self._error(str(error))
            return
        self.collections.blockSignals(True)
        self.collections.clear()
        row_to_select = 0
        for row, collection in enumerate(collections):
            item = QListWidgetItem(collection.name)
            item.setData(USER_ROLE, collection.id)
            self.collections.addItem(item)
            if collection.id == selected:
                row_to_select = row
        if collections:
            self.collections.setCurrentRow(row_to_select)
        self.collections.blockSignals(False)
        current_tag = self.tag_choice.currentData()
        self.tag_choice.blockSignals(True)
        self.tag_choice.clear()
        for tag in tags:
            self.tag_choice.addItem(tag.name, tag.id)
        if current_tag:
            index = self.tag_choice.findData(current_tag)
            if index >= 0:
                self.tag_choice.setCurrentIndex(index)
        self.tag_choice.blockSignals(False)
        self._collection_changed()

    def _collection_changed(self, *_args) -> None:
        collection_id = self.selected_collection_id()
        previous_image_id = self.selected_image_id()
        self.folders.clear()
        self._image_records = []
        self.images.blockSignals(True)
        self.images.setRowCount(0)
        self.images.blockSignals(False)
        self.collection_summary.setText("Select a collection")
        if collection_id:
            try:
                with WallpaperCatalog() as catalog:
                    links = catalog.list_folder_links(collection_id)
                    self._image_records = catalog.list_collection_images(collection_id)
                for folder in links:
                    item = QListWidgetItem(str(folder.path))
                    item.setData(USER_ROLE, folder.id)
                    item.setToolTip(str(folder.path))
                    if not folder.path.is_dir():
                        item.setText(f"⚠  {folder.path}")
                    self.folders.addItem(item)
                self.collection_summary.setText(
                    f"{len(self._image_records)} indexed image(s)"
                )
                self._populate_images()
                for row, image in enumerate(self._image_records):
                    if image.id == previous_image_id and not self.images.isRowHidden(row):
                        self.images.selectRow(row)
                        break
            except Exception as error:
                self._error(str(error))
        self._image_changed()
        self._sync_buttons()

    def _populate_images(self) -> None:
        self.images.blockSignals(True)
        self.images.setUpdatesEnabled(False)
        try:
            self.images.setRowCount(len(self._image_records))
            for row, image in enumerate(self._image_records):
                cell = QTableWidgetItem(image.path.name)
                cell.setData(USER_ROLE, image.id)
                cell.setToolTip(str(image.path))
                self.images.setItem(row, 0, cell)
                self.images.setItem(
                    row, 1, QTableWidgetItem(f"{image.width} × {image.height}")
                )
                self.images.setItem(
                    row, 2, QTableWidgetItem(image.orientation.title())
                )
                self.images.setItem(
                    row, 3, QTableWidgetItem(
                        "Available" if image.available else "Unavailable"
                    )
                )
        finally:
            self.images.setUpdatesEnabled(True)
            self.images.blockSignals(False)
        self._filter_images()

    def _filter_images(self, *_args) -> None:
        query = self.search.text().casefold().strip()
        orientation = self.orientation_filter.currentData()
        for row, image in enumerate(self._image_records):
            show = (query in image.path.name.casefold()
                    and (orientation == "all" or image.orientation == orientation))
            self.images.setRowHidden(row, not show)
        if not self.selected_image_id():
            self._image_changed()

    def _image_changed(self) -> None:
        image_id = self.selected_image_id()
        self.image_tags.clear()
        self.preview.clear()
        image = next((record for record in self._image_records
                      if record.id == image_id), None)
        if image is None:
            self.preview.setText("Select a wallpaper to preview")
            self.image_info.setText("Image details and tags will appear here.")
            self._sync_buttons()
            return
        self.image_info.setText(
            f"{image.path.name}\n{image.width} × {image.height} · "
            f"{image.orientation.title()} · {image.size_bytes / 1048576:.1f} MB"
        )
        try:
            with WallpaperCatalog() as catalog:
                for tag in catalog.list_image_tags(image.id):
                    item = QListWidgetItem(tag.name)
                    item.setData(USER_ROLE, tag.id)
                    self.image_tags.addItem(item)
        except Exception as error:
            self.status.setText(str(error))
        if not image.available or not image.path.is_file():
            self.preview.setText("Image unavailable")
        elif image.width * image.height > MAX_PREVIEW_PIXELS:
            self.preview.setText("Preview disabled for very large image")
        else:
            reader = QImageReader(str(image.path))
            reader.setAutoTransform(True)
            scale = min(246 / image.width, 160 / image.height, 1.0)
            reader.setScaledSize(QSize(
                max(1, int(image.width * scale)),
                max(1, int(image.height * scale)),
            ))
            picture = reader.read()
            if picture.isNull():
                self.preview.setText("Preview unavailable")
            else:
                pixmap = QPixmap.fromImage(picture)
                self.preview.setPixmap(pixmap.scaled(
                    self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        self._sync_buttons()

    def _sync_buttons(self, *_args) -> None:
        has_collection = self.selected_collection_id() is not None
        has_folder = self.selected_folder_id() is not None
        has_image = self.selected_image_id() is not None
        has_tag = self.tag_choice.currentData() is not None
        busy = self.scan_running()
        for button in (self.rename_collection_button, self.delete_collection_button,
                       self.add_folder_button):
            button.setEnabled(has_collection and not busy)
        self.add_collection_button.setEnabled(not busy)
        self.remove_folder_button.setEnabled(has_folder and not busy)
        self.copy_folder_button.setEnabled(has_folder)
        self.open_folder_button.setEnabled(has_folder)
        self.scan_collection_button.setEnabled(has_collection and not busy)
        self.scan_all_button.setEnabled(not busy)
        self.add_tag_button.setEnabled(has_image and has_tag and not busy)
        self.remove_tag_button.setEnabled(
            has_image and self.image_tags.currentItem() is not None and not busy
        )
        self.new_tag_button.setEnabled(not busy)
        self.rename_tag_button.setEnabled(has_tag and not busy)
        self.delete_tag_button.setEnabled(has_tag and not busy)

    def _add_collection(self) -> None:
        name, accepted = QInputDialog.getText(self, "New Collection", "Name:")
        if not accepted or not name.strip():
            return
        try:
            with WallpaperCatalog() as catalog:
                collection = catalog.create_collection(name)
            self.refresh_collections(collection.id)
            self.status.setText(f"Created collection '{collection.name}'.")
        except (ValueError, OSError) as error:
            self._error(str(error))

    def _rename_collection(self) -> None:
        selected = self.collections.currentItem()
        if selected is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename Collection", "New name:", text=selected.text()
        )
        if not accepted or not name.strip():
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.rename_collection(selected.data(USER_ROLE), name)
            self.refresh_collections(selected.data(USER_ROLE))
        except (ValueError, KeyError, OSError) as error:
            self._error(str(error))

    def _delete_collection(self) -> None:
        selected = self.collections.currentItem()
        if selected is None:
            return
        answer = QMessageBox.question(
            self, "Delete Collection",
            f"Delete the collection '{selected.text()}'?\n\n"
            "Its original wallpaper files will not be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                with WallpaperCatalog() as catalog:
                    catalog.delete_collection(selected.data(USER_ROLE))
                self.refresh_collections()
                self.status.setText("Collection removed; original files untouched.")
            except Exception as error:
                self._error(str(error))

    def _add_folder(self) -> None:
        collection_id = self.selected_collection_id()
        if not collection_id:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Link Existing Wallpaper Folder", str(Path.home())
        )
        if not folder:
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.link_folder(collection_id, folder)
            self._collection_changed()
            self._watch_wakeup.set()
            self.status.setText(
                f"Linked {folder}. "
                + ("Auto-scan will index its images shortly."
                   if self.auto_scan_checkbox.isChecked()
                   else "Use Scan collection to index its images.")
            )
        except (ValueError, OSError) as error:
            self._error(str(error))

    def _remove_folder(self) -> None:
        folder_id = self.selected_folder_id()
        if not folder_id:
            return
        if QMessageBox.question(
            self, "Unlink Folder",
            "Remove this folder's association with the collection?\n"
            "No files will be moved or deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.unlink_folder(folder_id)
            self._collection_changed()
            self._watch_wakeup.set()
            self.status.setText("Folder unlinked. Original files untouched.")
        except Exception as error:
            self._error(str(error))

    def _selected_folder_path(self) -> Path | None:
        folder_id = self.selected_folder_id()
        if not folder_id:
            return None
        with WallpaperCatalog() as catalog:
            return catalog.get_folder_link(folder_id).path

    def _open_folder(self) -> None:
        try:
            folder = self._selected_folder_path()
            if folder is None:
                return
            if not folder.is_dir():
                QMessageBox.warning(self, "Folder Unavailable", str(folder))
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except Exception as error:
            self._error(str(error))

    def _copy_folder_path(self) -> None:
        try:
            folder = self._selected_folder_path()
            if folder is not None:
                QApplication.clipboard().setText(str(folder))
                self.status.setText("Folder path copied to clipboard.")
        except Exception as error:
            self._error(str(error))

    def _assign_tag(self) -> None:
        image_id, tag_id = self.selected_image_id(), self.tag_choice.currentData()
        if not image_id or not tag_id:
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.tag_image(image_id, tag_id)
            self._image_changed()
        except Exception as error:
            self._error(str(error))

    def _remove_image_tag(self) -> None:
        image_id, item = self.selected_image_id(), self.image_tags.currentItem()
        if not image_id or item is None:
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.untag_image(image_id, item.data(USER_ROLE))
            self._image_changed()
        except Exception as error:
            self._error(str(error))

    def _new_tag(self) -> None:
        name, accepted = QInputDialog.getText(self, "New Tag", "Tag name:")
        if not accepted or not name.strip():
            return
        try:
            with WallpaperCatalog() as catalog:
                tag = catalog.create_tag(name)
                image_id = self.selected_image_id()
                if image_id:
                    catalog.tag_image(image_id, tag.id)
            self.refresh_collections(self.selected_collection_id())
            index = self.tag_choice.findData(tag.id)
            if index >= 0:
                self.tag_choice.setCurrentIndex(index)
            self.status.setText(f"Created tag '{tag.name}'.")
        except (ValueError, OSError) as error:
            self._error(str(error))

    def _rename_tag(self) -> None:
        tag_id = self.tag_choice.currentData()
        if not tag_id:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename Tag", "New name:", text=self.tag_choice.currentText()
        )
        if not accepted or not name.strip():
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.rename_tag(tag_id, name)
            self.refresh_collections(self.selected_collection_id())
        except (ValueError, KeyError, OSError) as error:
            self._error(str(error))

    def _delete_tag(self) -> None:
        tag_id = self.tag_choice.currentData()
        if not tag_id:
            return
        if QMessageBox.question(
            self, "Delete Tag",
            f"Delete '{self.tag_choice.currentText()}' from all images?\n"
            "No image files will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.delete_tag(tag_id)
            self.refresh_collections(self.selected_collection_id())
        except Exception as error:
            self._error(str(error))

    def scan_running(self) -> bool:
        return self._scan_thread is not None

    def _watch_loop(self) -> None:
        """Poll only file sizes/timestamps away from Qt's GUI thread."""
        detector = FolderChangeDetector()
        last_error = None
        while not self._watch_stop.is_set():
            if self._watch_active.is_set():
                try:
                    with WallpaperCatalog() as catalog:
                        links = [
                            link for collection in catalog.list_collections()
                            for link in catalog.list_folder_links(collection.id)
                        ]
                    changed = detector.poll(links)
                    last_error = None
                    if changed and not self._watch_stop.is_set():
                        self._watch_signals.changed.emit(tuple(sorted(changed)))
                except Exception as error:
                    # A busy SQLite file or temporarily unavailable drive
                    # must never terminate monitoring permanently.
                    message = str(error)
                    if message != last_error and not self._watch_stop.is_set():
                        self._watch_signals.failed.emit(message)
                    last_error = message
            self._watch_wakeup.wait(8.0)
            self._watch_wakeup.clear()

    def _watch_changed(self, folder_ids: tuple[str, ...]) -> None:
        if self.auto_scan_checkbox.isChecked():
            self._pending_auto_ids.update(folder_ids)
            self._auto_scan_timer.start(1500)  # Debounce clustered changes.

    def _watch_failed(self, message: str) -> None:
        if self.auto_scan_checkbox.isChecked():
            self.status.setText(
                "Automatic folder check will retry: " + message
            )

    def _auto_scan_toggled(self, enabled: bool) -> None:
        self._settings.setValue("library_auto_scan_enabled", enabled)
        self._settings.sync()
        if enabled:
            self._watch_active.set()
            self._watch_wakeup.set()
            # Always reconcile when re-enabling, even if files changed while
            # the watcher was deliberately turned off.
            try:
                with WallpaperCatalog() as catalog:
                    ids = tuple(
                        link.id for collection in catalog.list_collections()
                        for link in catalog.list_folder_links(collection.id)
                    )
                self._watch_changed(ids)
            except Exception as error:
                self.status.setText(f"Automatic scan will retry: {error}")
        else:
            self._watch_active.clear()
            self._auto_scan_timer.stop()
            self._pending_auto_ids.clear()
            self.status.setText("Auto-scan paused. Manual Scan buttons still work.")

    def _perform_automatic_scan(self) -> None:
        if not self.auto_scan_checkbox.isChecked() or self.scan_running():
            return
        if not self._pending_auto_ids:
            return
        delay = self._auto_retry_after - time.monotonic()
        if delay > 0:
            self._auto_scan_timer.start(max(1000, int(delay * 1000)))
            return
        ids = tuple(sorted(self._pending_auto_ids))
        self._pending_auto_ids.clear()
        self._start_scan(None, folder_ids=ids, automatic=True)

    def stop_watching(self) -> None:
        """Release the background poller before the Library UI is destroyed."""
        self._watch_stop.set()
        self._watch_wakeup.set()
        self._auto_scan_timer.stop()
        self._watch_thread.join(timeout=3.0)

    def _start_scan(
        self, collection_id: str | None, *,
        folder_ids: tuple[str, ...] | None = None, automatic: bool = False
    ) -> None:
        if self.scan_running():
            return
        self._scan_automatic = automatic
        self._scan_auto_ids = folder_ids or ()
        self._scan_thread = QThread(self)
        self._scan_worker = ScanWorker(collection_id, folder_ids)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.completed.connect(self._scan_completed)
        self._scan_worker.failed.connect(self._scan_failed)
        self._scan_worker.completed.connect(self._scan_thread.quit)
        self._scan_worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)
        self._scan_thread.finished.connect(self._scan_finished)
        self.status.setText(
            "Auto-indexing changed folders in the background..."
            if automatic else "Scanning linked folders in the background..."
        )
        self._sync_buttons()
        self._scan_thread.start()

    def _scan_completed(self, reports: list, warning_text: list) -> None:
        self.refresh_collections(self.selected_collection_id())
        added = sum(report.added for report in reports)
        updated = sum(report.updated for report in reports)
        cached = sum(report.cached for report in reports)
        skipped = sum(report.skipped for report in reports)
        problem_folders = [r for r in reports if r.status != "ok"]
        self.status.setText(
            f"{'Auto-scan' if self._scan_automatic else 'Scan'} finished: "
            f"{added} new, {updated} updated, {cached} cached, "
            f"{skipped} skipped. {len(problem_folders)} unavailable/partial "
            f"folder(s), {len(warning_text)} image warning(s)."
        )
        if self._scan_automatic:
            self._auto_failures = 0
            self._auto_retry_after = 0.0
        if (problem_folders or warning_text) and not self._scan_automatic:
            details = [f"{r.folder}: {r.status}" for r in problem_folders]
            details += warning_text[:5]
            QMessageBox.warning(
                self, "Library Scan Warnings", "\n".join(details)
            )

    def _scan_failed(self, message: str) -> None:
        self.status.setText("Scan failed: " + message)
        if self._scan_automatic:
            self._pending_auto_ids.update(self._scan_auto_ids)
            self._auto_failures += 1
            self._auto_retry_after = time.monotonic() + min(
                120, 5 * 2 ** min(self._auto_failures - 1, 5)
            )
            self.status.setText("Auto-scan will retry: " + message)
        else:
            self._error(message)

    def _scan_finished(self) -> None:
        self._scan_thread = None
        self._scan_worker = None
        self._scan_automatic = False
        self._scan_auto_ids = ()
        self._sync_buttons()
        if self._pending_auto_ids and self.auto_scan_checkbox.isChecked():
            self._auto_scan_timer.start(700)
