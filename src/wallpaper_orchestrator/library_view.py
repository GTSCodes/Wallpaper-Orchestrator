"""Library management UI for Wallpaper Orchestrator (Milestones B3/B4).

This widget does not detect monitors or change wallpapers. Every operation
uses the B1/B2 catalogue API, so the original image files remain untouched.
B4 watches linked folders for automatic background indexing.
"""

from collections import deque
import threading
import time
import warnings
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (
    QItemSelectionModel, QObject, QSize, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import QDesktopServices, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.indexer import resolve_folder_link, scan_all, scan_folder
from wallpaper_orchestrator.watcher import FolderChangeDetector
from wallpaper_orchestrator.library_c6_view import (
    DuplicateScanWorker, FolderDefaultsDialog, ImageDropGrid, ImageDropTable, ImportWorker,
    SmartCollectionsDialog, TagFilterDialog,
)
from wallpaper_orchestrator.indexer import SUPPORTED_EXTENSIONS


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
                                resolve_folder_link(catalog, folder_id)
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
        self._tag_ids_by_image: dict[str, set[str]] = {}
        self._available_tags = []
        self._filter_included: set[str] = set()
        self._filter_excluded: set[str] = set()
        self._filter_all = False
        self._thumb_pending = deque()
        self._grid_items = {}
        self._thumb_timer = QTimer(self)
        self._thumb_timer.timeout.connect(self._load_thumbnail_batch)
        self._duplicates_thread: QThread | None = None
        self._duplicates_worker: DuplicateScanWorker | None = None
        self._import_thread: QThread | None = None
        self._import_worker: ImportWorker | None = None
        self._import_folder_id: str | None = None
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
        self.tag_filter_button = QPushButton("Tags…")
        self.tag_filter_button.setToolTip("Include or exclude multiple tags in this view")
        search_row.addWidget(self.tag_filter_button)
        self.view_mode = QComboBox()
        self.view_mode.addItem("Table", "table")
        self.view_mode.addItem("Thumbnails", "grid")
        self.view_mode.setToolTip("Switch between a detailed table and image thumbnails")
        search_row.addWidget(self.view_mode)
        self.tools_button = QToolButton()
        self.tools_button.setText("Library tools ▾")
        self.tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tools_menu = QMenu(self.tools_button)
        self.import_action = self.tools_menu.addAction("Import images…")
        self.folder_defaults_action = self.tools_menu.addAction("Default tags for linked folder…")
        self.smart_action = self.tools_menu.addAction("Manage smart collections…")
        self.tools_menu.addSeparator()
        self.duplicates_action = self.tools_menu.addAction("Find exact duplicates…")
        self.tools_menu.addSeparator()
        self.excluded_action = self.tools_menu.addAction("Excluded images…")
        self.tools_button.setMenu(self.tools_menu)
        search_row.addWidget(self.tools_button)
        right_layout.addLayout(search_row)

        self.images = ImageDropTable(0, 4)
        self.images.setObjectName("libraryImages")
        self.images.setHorizontalHeaderLabels(
            ["Image", "Dimensions", "Orientation", "Status"]
        )
        self.images.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.images.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
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
        self.grid = ImageDropGrid()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setWrapping(True)
        self.grid.setIconSize(QSize(135, 105))
        self.grid.setGridSize(QSize(164, 158))
        self.grid.setSpacing(8)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Re-assert drop-only *after* setting icon mode; some Qt styles change
        # the list view's drag/drop policy as part of the view-mode switch.
        self.grid.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.grid.setAcceptDrops(True)
        self.grid.viewport().setAcceptDrops(True)
        self.grid.itemSelectionChanged.connect(self._image_changed)
        self.image_views = QStackedWidget()
        self.image_views.addWidget(self.images)
        self.image_views.addWidget(self.grid)
        right_layout.addWidget(self.image_views, 3)

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
        self.remove_image_button = QPushButton("Remove selected from collection…")
        self.remove_image_button.setToolTip(
            "Keep original image files. Linked-folder items become reversible exclusions."
        )
        detail_side.addWidget(self.remove_image_button)
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
        self.tag_filter_button.clicked.connect(self._choose_tag_filters)
        self.view_mode.currentIndexChanged.connect(self._switch_view)
        self.import_action.triggered.connect(self._choose_import_images)
        self.folder_defaults_action.triggered.connect(self._configure_folder_defaults)
        self.smart_action.triggered.connect(self._open_smart_collections)
        self.duplicates_action.triggered.connect(self._find_duplicates)
        self.excluded_action.triggered.connect(self._show_excluded_images)
        self.remove_image_button.clicked.connect(self._remove_selected_images)
        self.images.files_dropped.connect(self._import_dropped_images)
        self.grid.files_dropped.connect(self._import_dropped_images)
        for view in (self.images, self.grid):
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda pos, widget=view: self._image_context_menu(widget, pos)
            )

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
        identities = self.selected_image_ids()
        return identities[0] if identities else None

    def selected_image_ids(self) -> tuple[str, ...]:
        if self.image_views.currentWidget() is self.grid:
            return tuple(item.data(USER_ROLE) for item in self.grid.selectedItems())
        result = []
        for model_index in self.images.selectionModel().selectedRows():
            cell = self.images.item(model_index.row(), 0)
            if cell:
                result.append(cell.data(USER_ROLE))
        return tuple(result)

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
        self._available_tags = tags
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
        previous_image_ids = self.selected_image_ids()
        self.folders.clear()
        self._image_records = []
        self._tag_ids_by_image = {}
        self._thumb_timer.stop()
        self._thumb_pending.clear()
        self.images.blockSignals(True)
        self.images.setRowCount(0)
        self.images.blockSignals(False)
        self.grid.blockSignals(True)
        self.grid.clear()
        self._grid_items.clear()
        self.grid.blockSignals(False)
        self.collection_summary.setText("Select a collection")
        if collection_id:
            try:
                with WallpaperCatalog() as catalog:
                    links = catalog.list_folder_links(collection_id)
                    self._image_records = catalog.list_collection_images(collection_id)
                    self._tag_ids_by_image = catalog.list_image_tag_ids(
                        record.id for record in self._image_records
                    )
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
                if previous_image_ids:
                    self._select_images(previous_image_ids)
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
        self.grid.blockSignals(True)
        self.grid.setUpdatesEnabled(False)
        try:
            self.grid.clear()
            self._thumb_pending = deque()
            self._grid_items = {}
            for record in self._image_records:
                item = QListWidgetItem(record.path.name)
                item.setData(USER_ROLE, record.id)
                item.setToolTip(str(record.path))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
                self.grid.addItem(item)
                self._grid_items[record.id] = item
                if record.available and record.path.is_file() and record.width * record.height <= MAX_PREVIEW_PIXELS:
                    self._thumb_pending.append((record.id, record.path, record.width, record.height))
        finally:
            self.grid.setUpdatesEnabled(True)
            self.grid.blockSignals(False)
        self._filter_images()
        if self.image_views.currentWidget() is self.grid:
            self._thumb_timer.start(30)

    def _load_thumbnail_batch(self):
        if self.image_views.currentWidget() is not self.grid:
            self._thumb_timer.stop()
            return
        for _ in range(min(5, len(self._thumb_pending))):
            image_id, path, width, height = self._thumb_pending.popleft()
            item = self._grid_items.get(image_id)
            if item is None or item.isHidden():
                continue
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            scale = min(132 / width, 104 / height, 1.0)
            reader.setScaledSize(QSize(max(1, int(width * scale)),
                                       max(1, int(height * scale))))
            image = reader.read()
            if not image.isNull():
                item.setIcon(QIcon(QPixmap.fromImage(image)))
        if not self._thumb_pending:
            self._thumb_timer.stop()

    def _select_images(self, identities):
        chosen = set(identities)
        self.images.blockSignals(True)
        self.grid.blockSignals(True)
        self.images.clearSelection()
        self.grid.clearSelection()
        for row, record in enumerate(self._image_records):
            if record.id not in chosen or self.images.isRowHidden(row):
                continue
            index = self.images.model().index(row, 0)
            self.images.selectionModel().select(index,
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            item = self._grid_items.get(record.id)
            if item and not item.isHidden():
                item.setSelected(True)
        self.images.blockSignals(False)
        self.grid.blockSignals(False)
        self._image_changed()

    def _switch_view(self, *_args):
        chosen = self.selected_image_ids()
        new_index = 1 if self.view_mode.currentData() == "grid" else 0
        self.image_views.setCurrentIndex(new_index)
        self._select_images(chosen)
        if new_index == 1:
            self._queue_visible_thumbnails()

    def _queue_visible_thumbnails(self):
        pending_ids = {mid for mid, *_ in self._thumb_pending}
        for record in self._image_records:
            item = self._grid_items.get(record.id)
            if (item and not item.isHidden() and item.icon().isNull()
                    and record.id not in pending_ids and record.available
                    and record.path.is_file() and record.width * record.height <= MAX_PREVIEW_PIXELS):
                self._thumb_pending.append((record.id, record.path, record.width, record.height))
        if self._thumb_pending:
            self._thumb_timer.start(30)

    def _choose_tag_filters(self):
        dialog = TagFilterDialog(self._available_tags, self._filter_included,
                                 self._filter_excluded, self._filter_all, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._filter_included, self._filter_excluded, self._filter_all = dialog.selection()
            count = len(self._filter_included) + len(self._filter_excluded)
            self.tag_filter_button.setText(f"Tags ({count})…" if count else "Tags…")
            self._filter_images()
            if self.image_views.currentWidget() is self.grid:
                self._queue_visible_thumbnails()

    def _filter_images(self, *_args) -> None:
        query = self.search.text().casefold().strip()
        orientation = self.orientation_filter.currentData()
        for row, image in enumerate(self._image_records):
            show = (query in image.path.name.casefold()
                    and (orientation == "all" or image.orientation == orientation))
            tags = self._tag_ids_by_image.get(image.id, set())
            if self._filter_included:
                show = show and (self._filter_included.issubset(tags) if self._filter_all
                                 else bool(self._filter_included & tags))
            show = show and not bool(self._filter_excluded & tags)
            self.images.setRowHidden(row, not show)
            if row < self.grid.count():
                self.grid.item(row).setHidden(not show)
        if not self.selected_image_id():
            self._image_changed()

    def _image_changed(self) -> None:
        image_ids = self.selected_image_ids()
        image_id = image_ids[0] if image_ids else None
        self.image_tags.clear()
        self.preview.clear()
        if len(image_ids) > 1:
            common = set.intersection(*(self._tag_ids_by_image.get(mid, set())
                                        for mid in image_ids))
            self.preview.setText(f"{len(image_ids)} images selected")
            self.image_info.setText(
                f"{len(image_ids)} images selected. Assign tags to all of them; "
                "only tags shared by every selected image are shown below."
            )
            for tag in self._available_tags:
                if tag.id in common:
                    item = QListWidgetItem(tag.name)
                    item.setData(USER_ROLE, tag.id)
                    self.image_tags.addItem(item)
            self._sync_buttons()
            return
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
        for tag in self._available_tags:
            if tag.id in self._tag_ids_by_image.get(image.id, set()):
                item = QListWidgetItem(tag.name)
                item.setData(USER_ROLE, tag.id)
                self.image_tags.addItem(item)
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
        image_count = len(self.selected_image_ids())
        has_image = image_count > 0
        has_tag = self.tag_choice.currentData() is not None
        busy = self.scan_running() or self._import_thread is not None
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
        self.add_tag_button.setText("Assign to all" if image_count > 1 else "Assign")
        self.remove_tag_button.setText("Remove from all" if image_count > 1 else "Remove")
        self.folder_defaults_action.setEnabled(has_folder and not busy)
        self.duplicates_action.setEnabled(self._duplicates_thread is None)
        self.import_action.setEnabled(not busy)
        self.remove_image_button.setEnabled(has_collection and has_image and not busy)
        self.excluded_action.setEnabled(has_collection and not busy)

    def _remove_selected_images(self) -> None:
        collection_id = self.selected_collection_id()
        ids = self.selected_image_ids()
        if not collection_id or not ids:
            return
        try:
            with WallpaperCatalog() as catalog:
                linked = sum(catalog.is_linked_collection_image(collection_id, iid)
                             for iid in ids)
            explanation = (
                f"Remove {len(ids)} selected image(s) from this collection?\n\n"
                "No original files will be deleted.\n"
            )
            if linked:
                explanation += (
                    f"\n{linked} image(s) belong to linked folders and will be "
                    "placed in Excluded images. They will stay hidden after "
                    "folder rescans and can be restored later."
                )
            else:
                explanation += (
                    "\nThese images were added manually; their collection "
                    "membership will be removed without creating exclusions."
                )
            answer = QMessageBox.question(
                self, "Remove from collection", explanation,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            with WallpaperCatalog() as catalog:
                for image_id in ids:
                    catalog.remove_image_from_collection(collection_id, image_id)
            self._collection_changed()
            self.status.setText(f"Removed {len(ids)} image(s) without deleting files.")
        except Exception as error:
            self._error(str(error))

    def _show_excluded_images(self) -> None:
        collection_id = self.selected_collection_id()
        if not collection_id:
            return
        try:
            with WallpaperCatalog() as catalog:
                records = catalog.list_excluded_images(collection_id)
        except Exception as error:
            self._error(str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Excluded images")
        dialog.resize(650, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "These linked-folder images are hidden only from this collection. "
            "Their original files are untouched."
        ))
        entries = QListWidget()
        entries.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for image in records:
            item = QListWidgetItem(f"{image.path.name}  —  {image.path}")
            item.setData(USER_ROLE, image.id)
            entries.addItem(item)
        layout.addWidget(entries)
        row = QHBoxLayout()
        restore_button = QPushButton("Restore selected")
        restore_button.setEnabled(bool(records))
        close_button = QPushButton("Close")
        row.addWidget(restore_button)
        row.addStretch(1)
        row.addWidget(close_button)
        layout.addLayout(row)
        close_button.clicked.connect(dialog.reject)
        restore_button.clicked.connect(dialog.accept)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [item.data(USER_ROLE) for item in entries.selectedItems()]
        if not selected:
            return
        try:
            with WallpaperCatalog() as catalog:
                for image_id in selected:
                    catalog.restore_excluded_image(collection_id, image_id)
            self._collection_changed()
            self.status.setText(f"Restored {len(selected)} excluded image(s).")
        except Exception as error:
            self._error(str(error))

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
            return resolve_folder_link(catalog, folder_id).path

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

    def _configure_folder_defaults(self):
        folder_id = self.selected_folder_id()
        if not folder_id:
            QMessageBox.information(self, "Default Tags", "Select a linked folder on the left first.")
            return
        try:
            with WallpaperCatalog() as catalog:
                link = catalog.get_folder_link(folder_id)
                selected = [tag.id for tag in catalog.list_folder_default_tags(folder_id)]
            editor = FolderDefaultsDialog(link.path.name, self._available_tags, selected, self)
            if editor.exec() != QDialog.DialogCode.Accepted:
                return
            with WallpaperCatalog() as catalog:
                catalog.set_folder_default_tags(folder_id, editor.selected_ids())
            self._collection_changed()
            self.status.setText("Folder default tags saved and applied to existing indexed images. "
                                "Future imports and auto-scans also receive these tags.")
        except (OSError, ValueError, KeyError) as error:
            self._error(str(error))

    def _open_smart_collections(self):
        try:
            SmartCollectionsDialog(self).exec()
            self.refresh_collections()
        except (OSError, ValueError) as error:
            self._error(str(error))

    def _choose_import_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import wallpaper images", str(Path.home()),
            "Wallpaper images (*.jpg *.jpeg *.png *.bmp *.webp)",
        )
        if paths:
            self._import_dropped_images(tuple(Path(path) for path in paths))

    def _import_dropped_images(self, paths):
        if self._import_thread is not None or self.scan_running():
            self.status.setText("Please wait for the current Library operation to finish.")
            return
        collection_id = self.selected_collection_id()
        if not collection_id:
            QMessageBox.information(self, "Import Images", "Select a collection first.")
            return
        incoming = tuple(Path(path) for path in paths
                         if Path(path).suffix.lower() in SUPPORTED_EXTENSIONS)
        if not incoming:
            QMessageBox.warning(self, "Import Images", "No supported wallpaper images selected.")
            return
        try:
            with WallpaperCatalog() as catalog:
                links = [link for link in catalog.list_folder_links(collection_id)
                         if link.path.is_dir()]
        except (OSError, ValueError) as error:
            self._error(str(error))
            return
        if not links:
            QMessageBox.information(
                self, "Import Images", "This collection has no available linked folder. "
                "Use + Folder to link an existing destination first.",
            )
            return
        chosen = next((link for link in links if link.id == self.selected_folder_id()), None)
        if chosen is None and len(links) > 1:
            choices = [str(link.path) for link in links]
            name, accepted = QInputDialog.getItem(
                self, "Import destination", "Copy images into:", choices, 0, False,
            )
            if not accepted:
                return
            chosen = links[choices.index(name)]
        chosen = chosen or links[0]
        if QMessageBox.question(
            self, "Import Wallpapers",
            f"Copy {len(incoming)} image(s) to:\n{chosen.path}\n\n"
            "Original files will remain where they are. Existing files will not be overwritten.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._import_folder_id = chosen.id
        self._import_thread = QThread(self)
        self._import_worker = ImportWorker(incoming, chosen.path)
        self._import_worker.moveToThread(self._import_thread)
        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.completed.connect(self._import_completed)
        self._import_worker.failed.connect(self._import_failed)
        self._import_worker.completed.connect(self._import_thread.quit)
        self._import_worker.failed.connect(self._import_thread.quit)
        self._import_thread.finished.connect(self._import_worker.deleteLater)
        self._import_thread.finished.connect(self._import_thread.deleteLater)
        self._import_thread.finished.connect(self._import_finished)
        self.status.setText(f"Copying {len(incoming)} image(s) in the background…")
        self._sync_buttons()
        self._import_thread.start()

    def _import_completed(self, report):
        if self._import_folder_id and report.copied:
            self._pending_auto_ids.add(self._import_folder_id)
        self.status.setText(
            f"Copied {len(report.copied)} image(s), skipped {len(report.skipped)}. "
            "Their source files are unchanged. Indexing follows automatically."
        )
        if report.skipped:
            QMessageBox.information(self, "Import Details", "\n".join(report.skipped[:10]))

    def _import_failed(self, message):
        if self._import_folder_id:
            self._pending_auto_ids.add(self._import_folder_id)
        self.status.setText("Import was interrupted; any successfully copied files will be indexed.")
        self._error(message)

    def _import_finished(self):
        folder_id = self._import_folder_id
        self._import_thread = None
        self._import_worker = None
        self._import_folder_id = None
        self._sync_buttons()
        if folder_id and not self.auto_scan_checkbox.isChecked():
            self._pending_auto_ids.discard(folder_id)
            self._start_scan(None, folder_ids=(folder_id,), automatic=False)
        elif self._pending_auto_ids:
            self._auto_scan_timer.start(200)

    def _find_duplicates(self):
        if self._duplicates_thread is not None:
            return
        try:
            with WallpaperCatalog() as catalog:
                records = catalog.list_all_images()
        except (OSError, ValueError) as error:
            self._error(str(error))
            return
        self._duplicates_thread = QThread(self)
        self._duplicates_worker = DuplicateScanWorker(
            [record.path for record in records if record.available]
        )
        self._duplicates_worker.moveToThread(self._duplicates_thread)
        self._duplicates_thread.started.connect(self._duplicates_worker.run)
        self._duplicates_worker.completed.connect(self._duplicates_completed)
        self._duplicates_worker.failed.connect(self._duplicates_failed)
        self._duplicates_worker.completed.connect(self._duplicates_thread.quit)
        self._duplicates_worker.failed.connect(self._duplicates_thread.quit)
        self._duplicates_thread.finished.connect(self._duplicates_worker.deleteLater)
        self._duplicates_thread.finished.connect(self._duplicates_thread.deleteLater)
        self._duplicates_thread.finished.connect(self._duplicates_finished)
        self.status.setText(f"Comparing {len(records)} indexed images by content in the background…")
        self._sync_buttons()
        self._duplicates_thread.start()

    def _duplicates_completed(self, report):
        groups = report.groups
        self.status.setText(
            f"Duplicate check completed: {len(groups)} exact duplicate group(s). "
            "Nothing was deleted or moved."
        )
        details = "\n\n".join(
            f"Set {index} ({len(group)} copies):\n" + "\n".join(str(path) for path in group)
            for index, group in enumerate(groups, 1)
        )
        if report.unreadable:
            details += "\n\nUnavailable files:\n" + "\n".join(report.unreadable[:30])
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Exact duplicate wallpapers")
        dialog.setText(
            f"Found {len(groups)} group(s) of byte-identical images. "
            "Only images with matching size are hashed. Original files are untouched."
        )
        if details:
            dialog.setDetailedText(details)
        dialog.exec()

    def _duplicates_failed(self, message):
        self._error(f"Duplicate check failed: {message}")

    def _duplicates_finished(self):
        self._duplicates_thread = None
        self._duplicates_worker = None
        self._sync_buttons()

    def _image_context_menu(self, view, pos):
        clicked = view.itemAt(pos)
        if clicked is None:
            return
        if view is self.images:
            identity_cell = self.images.item(clicked.row(), 0)
            image_id = identity_cell.data(USER_ROLE) if identity_cell else None
        else:
            image_id = clicked.data(USER_ROLE)
        image = next((r for r in self._image_records if r.id == image_id), None)
        if image is None or not image.available or not image.path.is_file():
            return
        from wallpaper_orchestrator.engine import SUPPORTED
        if image.path.suffix.lower() not in SUPPORTED:
            menu = QMenu(self)
            menu.addAction("This format is indexed but not supported as a direct wallpaper").setEnabled(False)
            menu.exec(view.viewport().mapToGlobal(pos))
            return
        host = self.window()
        monitors = getattr(host, "monitors", [])
        roles = {mid: role for role, mid in getattr(host, "mapping", {}).items()}
        menu = QMenu(self)
        compatible = [monitor for monitor in monitors
                      if monitor.orientation == image.orientation]
        submenu = menu if len(compatible) == 1 else menu.addMenu("Apply to display")
        for monitor in compatible:
            label = roles.get(monitor.device_id, monitor.orientation)
            prefix = "Apply to " if len(compatible) == 1 else ""
            action = submenu.addAction(f"{prefix}{label} ({monitor.width} × {monitor.height})")
            action.triggered.connect(
                lambda _checked=False, mid=monitor.device_id, iid=image.id:
                    self._apply_image_to_monitor(iid, mid)
            )
        if not submenu.actions():
            submenu.addAction("No display with a matching orientation").setEnabled(False)
        menu.exec(view.viewport().mapToGlobal(pos))

    def _apply_image_to_monitor(self, image_id, device_id):
        """Explicit one-off application; do not alter folder subscriptions."""
        image = next((record for record in self._image_records if record.id == image_id), None)
        if image is None or not image.path.is_file():
            QMessageBox.warning(self, "Apply Wallpaper", "This image is no longer available.")
            return
        try:
            from wallpaper_orchestrator.engine import apply_plan
            from wallpaper_orchestrator.models import Wallpaper
            from wallpaper_orchestrator.profiles import default_profile, load_profiles, save_profiles
            current = load_profiles()
            wallpaper = Wallpaper(image.path, image.orientation, frozenset())
            apply_plan({device_id: wallpaper}, {device_id: device_id})
            profile = current.setdefault(device_id, default_profile())
            profile["last_image"] = str(image.path)
            if profile.get("mode") == "fixed":
                profile["image"] = str(image.path)
            save_profiles(current)
            host = self.window()
            if hasattr(host, "profiles"):
                host.profiles = current
            self.status.setText(f"Applied {image.path.name} to one display.")
        except Exception as error:
            self._error(f"Wallpaper could not be applied: {error}")

    def _assign_tag(self) -> None:
        image_ids, tag_id = self.selected_image_ids(), self.tag_choice.currentData()
        if not image_ids or not tag_id:
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.tag_images(image_ids, [tag_id])
            for image_id in image_ids:
                self._tag_ids_by_image.setdefault(image_id, set()).add(tag_id)
            self._image_changed()
        except Exception as error:
            self._error(str(error))

    def _remove_image_tag(self) -> None:
        image_ids, item = self.selected_image_ids(), self.image_tags.currentItem()
        if not image_ids or item is None:
            return
        try:
            with WallpaperCatalog() as catalog:
                catalog.untag_images(image_ids, [item.data(USER_ROLE)])
            for image_id in image_ids:
                self._tag_ids_by_image.setdefault(image_id, set()).discard(item.data(USER_ROLE))
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
                image_ids = self.selected_image_ids()
                if image_ids:
                    catalog.tag_images(image_ids, [tag.id])
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
        if (not self.auto_scan_checkbox.isChecked() or self.scan_running()
                or self._import_thread is not None):
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
        self._thumb_timer.stop()
        self._watch_thread.join(timeout=3.0)
        # Qt must not destroy a QThread while its worker is still hashing or
        # copying user files. Complete in-flight, already confirmed actions.
        for thread in (self._import_thread, self._duplicates_thread):
            if thread is not None:
                thread.quit()
                thread.wait()

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
