"""Monitor-first desktop interface for Wallpaper Orchestrator."""

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, QSettings, QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from wallpaper_orchestrator.engine import apply_plan, create_profile_plan
from wallpaper_orchestrator.library_view import LibraryPanel
from wallpaper_orchestrator.platforms.windows import (
    discover_monitors,
    set_monitor_wallpaper,
)
from wallpaper_orchestrator.profiles import (
    default_profile,
    load_profiles,
    save_profiles,
)
from wallpaper_orchestrator.state import load_monitor_mapping


def display_count_text(count):
    """A count label shared by selection, buttons, and success messages."""
    return f"{count} display{'s' if count != 1 else ''}"


class SelectionMarquee(QWidget):
    """Translucent blue drag rectangle, like the Windows desktop marquee."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(56, 140, 231, 64))
        painter.setPen(QPen(QColor("#65b5ff"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class MonitorView(QGraphicsView):
    """Additive click selection and a desktop-style selection marquee.

    Mouse selection is managed here, not by Qt's default graphics-scene
    handlers: their normal release handling replaces the multi-selection.
    """

    def __init__(self, scene):
        super().__init__(scene)
        self.selection_changed_handler = None
        self._press_point = None
        self._press_tile = None
        self._previous_selection = set()
        self._dragging = False
        self._skip_release = False

        self.marquee = SelectionMarquee(self.viewport())
        self.marquee.hide()

    def _tile_at(self, point):
        item = self.itemAt(point)
        while item is not None:
            if isinstance(item, MonitorTile):
                return item
            item = item.parentItem()
        return None

    def _selected_ids(self):
        return {
            item.data(1) for item in self.scene().selectedItems()
            if isinstance(item, MonitorTile)
        }

    def _set_selected_ids(self, selected_ids):
        scene = self.scene()
        previous_block = scene.blockSignals(True)
        changed = False
        try:
            for item in scene.items():
                if not isinstance(item, MonitorTile):
                    continue
                selected = item.data(1) in selected_ids
                if item.isSelected() != selected:
                    item.setSelected(selected)
                    changed = True
        finally:
            scene.blockSignals(previous_block)
        if changed and self.selection_changed_handler:
            self.selection_changed_handler()

    def cancel_selection_gesture(self):
        """Drop a partially drawn selection before its scene items are replaced."""
        self.marquee.hide()
        self._press_point = None
        self._press_tile = None
        self._previous_selection = set()
        self._dragging = False

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        point = event.position().toPoint()
        hit = self.itemAt(point)
        if isinstance(hit, ImageBadge):
            # The badge is an action; do not start selecting/dragging behind it.
            self._skip_release = True
            hit.choose_image()
            event.accept()
            return

        self._press_point = point
        self._press_tile = self._tile_at(point)
        self._previous_selection = self._selected_ids()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event):
        if self._press_point is None or not (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            super().mouseMoveEvent(event)
            return

        point = event.position().toPoint()
        if not self._dragging and (
            point - self._press_point
        ).manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
            self.marquee.show()

        if self._dragging:
            viewport_rect = QRect(self._press_point, point).normalized()
            self.marquee.setGeometry(viewport_rect)
            scene_rect = self.mapToScene(viewport_rect).boundingRect()
            hits = {
                item.data(1) for item in self.scene().items()
                if isinstance(item, MonitorTile)
                and item.sceneBoundingRect().intersects(scene_rect)
            }
            # Dragging from a selected display adds to the existing group;
            # dragging from empty canvas makes a fresh marquee selection.
            if self._press_tile is not None:
                hits.update(self._previous_selection)
            self._set_selected_ids(hits)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._skip_release:
            self._skip_release = False
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or self._press_point is None:
            super().mouseReleaseEvent(event)
            return

        if self._dragging:
            self.marquee.hide()
        elif self._press_tile is None:
            self._set_selected_ids(set())
        else:
            selected_ids = set(self._previous_selection)
            device_id = self._press_tile.data(1)
            if device_id in selected_ids:
                selected_ids.remove(device_id)
            else:
                selected_ids.add(device_id)
            self._set_selected_ids(selected_ids)

        self._press_point = None
        self._press_tile = None
        self._previous_selection = set()
        self._dragging = False
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene() and not self.scene().sceneRect().isEmpty():
            self.fitInView(
                self.scene().sceneRect(),
                Qt.AspectRatioMode.KeepAspectRatio,
            )


class ImageBadge(QGraphicsRectItem):
    """Image-picker icon on a hovered, selected display."""

    def __init__(self, choose_image, parent):
        super().__init__(QRectF(0, 0, 190, 190), parent)
        self.choose_image = choose_image
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip("Choose and apply a wallpaper")
        self.setZValue(20)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self.choose_image()
            return
        event.ignore()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#bac7d6"), 7))
        painter.setBrush(QBrush(QColor("#41566e")))
        painter.drawRoundedRect(self.rect(), 30, 30)
        painter.setPen(QPen(QColor("#ffffff"), 9))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(37, 45, 115, 99), 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawEllipse(QPointF(116, 73), 11, 11)
        painter.drawPolygon(QPolygonF([
            QPointF(43, 131), QPointF(81, 89), QPointF(104, 116),
            QPointF(122, 99), QPointF(146, 131),
        ]))


class MonitorTile(QGraphicsRectItem):
    """Display tile with selection handled by the parent MonitorView."""

    def __init__(self, monitor, name, choose_image):
        super().__init__(QRectF(0, 0, monitor.width, monitor.height))
        self.setPos(monitor.x, monitor.y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        # The entire rectangle is selectable, but uses the ordinary cursor.
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip("Click to select or deselect")
        self.setData(0, name)
        self.setData(1, monitor.device_id)
        self._hovered = False

        self.name = QGraphicsSimpleTextItem(name, self)
        font = QFont("Segoe UI")
        font.setPixelSize(85)
        font.setWeight(QFont.Weight.DemiBold)
        self.name.setFont(font)
        self.name.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        self.dimensions = QGraphicsSimpleTextItem(
            f"{monitor.width} × {monitor.height}", self
        )
        dimensions_font = QFont("Segoe UI")
        dimensions_font.setPixelSize(72)
        self.dimensions.setFont(dimensions_font)
        self.dimensions.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        label_size = self.name.boundingRect()
        dimension_size = self.dimensions.boundingRect()
        combined_height = label_size.height() + dimension_size.height() + 14
        top = (monitor.height - combined_height) / 2
        self.name.setPos(
            (monitor.width - label_size.width()) / 2,
            top,
        )
        self.dimensions.setPos(
            (monitor.width - dimension_size.width()) / 2,
            top + label_size.height() + 14,
        )
        self.image_badge = ImageBadge(choose_image, self)
        self.image_badge.setPos(max(20, monitor.width - 210), 20)
        self.refresh_appearance()

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.refresh_appearance()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.refresh_appearance()
        super().hoverLeaveEvent(event)

    def refresh_appearance(self):
        selected = self.isSelected()
        self.setOpacity(1.0 if selected else 0.30)
        self.setZValue(2 if selected else 0)
        self.setBrush(QBrush(QColor("#263447" if selected else "#263447")))
        self.setPen(QPen(
            QColor("#96abc6" if selected else "#6e7e91"),
            16 if selected else 12,
        ))
        self.name.setBrush(QBrush(QColor("#ffffff" if selected else "#c3d0dc")))
        self.dimensions.setBrush(QBrush(QColor("#d9e2ed" if selected else "#a9b8c9")))
        self.image_badge.setVisible(selected and self._hovered)


class WallpaperWindow(QMainWindow):
    """Select one or more displays and act only when the choice is valid."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wallpaper Orchestrator")
        self.resize(1030, 700)

        # Keep the existing global library preference from older GUI versions.
        self.settings = QSettings("WallpaperOrchestrator", "WallpaperOrchestrator")
        self.mapping = load_monitor_mapping()
        self.monitors = discover_monitors()
        self.profiles = load_profiles()
        self.selected_device_id = None
        self.selected_device_ids = []
        self.monitor_items = {}
        self._restoring = False

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.setCentralWidget(self.tabs)
        root = QWidget()
        self.tabs.addTab(root, "Displays")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        title = QLabel("Wallpaper Orchestrator")
        title.setObjectName("title")
        layout.addWidget(title)
        # Shared library: fallback for any monitor without a custom folder.
        library_caption = QLabel("MAIN WALLPAPER FOLDER")
        library_caption.setObjectName("sectionLabel")
        layout.addWidget(library_caption)
        library_row = QHBoxLayout()
        self.library_path = QLineEdit()
        self.library_path.setReadOnly(True)
        self.library_path.setPlaceholderText("Choose wallpaper folder")
        self.library_path.setToolTip(
            "Current folder-based monitor source. Collection subscriptions "
            "will be connected to Displays in C1."
        )
        saved = self.settings.value("wallpaper_library", "", type=str)
        if saved and Path(saved).is_dir():
            self.library_path.setText(saved)
        choose_library = QPushButton("Browse")
        choose_library.clicked.connect(self.choose_library)
        library_row.addWidget(self.library_path, 1)
        library_row.addWidget(choose_library)
        layout.addLayout(library_row)

        display_row = QHBoxLayout()
        display_caption = QLabel("DISPLAYS")
        display_caption.setObjectName("sectionLabel")
        display_row.addWidget(display_caption)
        display_row.addStretch(1)
        self.select_all_button = QPushButton("Select all")
        self.select_all_button.setObjectName("quietButton")
        self.select_all_button.clicked.connect(self.toggle_select_all)
        self.select_all_button.setEnabled(bool(self.monitors))
        display_row.addWidget(self.select_all_button)

        self.refresh_button = QToolButton()
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setText("↻")
        self.refresh_button.setToolTip("Refresh displays")
        self.refresh_button.setAutoRaise(True)
        self.refresh_button.setFixedSize(24, 24)
        self.refresh_button.clicked.connect(
            lambda: self.refresh_monitors(manual=True)
        )
        display_row.addWidget(self.refresh_button)
        layout.addLayout(display_row)
        self.scene = QGraphicsScene(self)
        self.view = MonitorView(self.scene)
        self.view.setMinimumHeight(270)
        self.view.setBackgroundBrush(QColor("#171a21"))
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout.addWidget(self.view, 1)

        # The controls below all belong to the currently selected monitor.
        self.panel = QFrame()
        self.panel.setObjectName("monitorPanel")
        # Keep the same footprint with zero, one, or several selected displays.
        self.panel.setMinimumHeight(134)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(13, 10, 13, 10)
        panel_layout.setSpacing(8)
        self.selected_heading = QLabel()
        self.selected_heading.setObjectName("panelTitle")
        panel_layout.addWidget(self.selected_heading)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Wallpaper"))
        self.source_mode = QComboBox()
        self.source_mode.addItem("Folder", "folder")
        self.source_mode.addItem("Fixed image", "fixed")
        source_row.addWidget(self.source_mode, 3)
        self.order_label = QLabel("Order")
        source_row.addWidget(self.order_label)
        self.folder_order = QComboBox()
        self.folder_order.addItem("Random", "random")
        self.folder_order.addItem("Next", "next")
        self.folder_order.addItem("Previous", "previous")
        self.folder_order.setToolTip("Choose how to move through this folder")
        source_row.addWidget(self.folder_order, 2)
        panel_layout.addLayout(source_row)

        # Source-specific controls: only the relevant picker is displayed.
        self.source_pages = QStackedWidget()
        fixed_page = QWidget()
        fixed_row = QHBoxLayout(fixed_page)
        fixed_row.setContentsMargins(0, 0, 0, 0)
        self.image_path = QLineEdit()
        self.image_path.setPlaceholderText("No image selected")
        self.image_path.setReadOnly(True)
        pick_image = QPushButton("Choose image")
        pick_image.clicked.connect(self.choose_image)
        fixed_row.addWidget(self.image_path, 1)
        fixed_row.addWidget(pick_image)
        self.source_pages.addWidget(fixed_page)

        folder_page = QWidget()
        folder_row = QHBoxLayout(folder_page)
        folder_row.setContentsMargins(0, 0, 0, 0)
        self.source_folder = QLineEdit()
        self.source_folder.setReadOnly(True)
        self.source_folder.setPlaceholderText("Using main library")
        pick_folder = QPushButton("Browse")
        pick_folder.clicked.connect(self.choose_monitor_folder)
        use_main = QPushButton("Use library")
        use_main.clicked.connect(self.use_main_library)
        folder_row.addWidget(self.source_folder, 1)
        folder_row.addWidget(pick_folder)
        folder_row.addWidget(use_main)
        self.source_pages.addWidget(folder_page)
        panel_layout.addWidget(self.source_pages)

        layout.addWidget(self.panel)

        self.action_button = QPushButton("Select a display")
        self.action_button.setObjectName("primaryButton")
        self.action_button.clicked.connect(self.change_selection)
        layout.addWidget(self.action_button)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #101319; color: #e8e9ec;
                                   font-family: 'Segoe UI'; font-size: 13px; }
            QLabel#title { font-size: 21px; font-weight: 500; }
            QLabel#panelTitle { font-size: 15px; font-weight: 500; }
            QLabel#panelTitle:disabled { color: #a3acb9; }
            QLabel#muted { color: #9ca5b1; }
            QLabel#sectionLabel { color: #aab8ca; font-size: 11px; font-weight: 600; }
            QFrame#monitorPanel { border: 1px solid #38404d; border-radius: 7px;
                                  background: #1b2029; }
            QFrame#monitorPanel:disabled { border-color: #303744;
                                           background: #171b23; }
            QFrame#monitorPanel QLabel { background: transparent; }
            QFrame#monitorPanel QLabel:disabled { color: #758092; }
            QFrame#monitorPanel QLabel#panelTitle:disabled { color: #a3acb9; }
            QLineEdit, QComboBox { background: #242933; border: 1px solid #464f5d;
                                   border-radius: 4px; padding: 6px; }
            QLineEdit:disabled, QComboBox:disabled {
                background: #191e27; color: #687384;
                border: 1px solid #303743;
            }
            QComboBox::drop-down:disabled { background: #191e27; border: 0; }
            QComboBox::down-arrow:disabled { image: none; }
            QPushButton { background: #292f3a; border: 1px solid #495362;
                          border-radius: 5px; padding: 7px 10px; }
            QPushButton:hover { background: #394456; }
            QPushButton:disabled { color: #747c88; background: #1b2028;
                                   border-color: #333b47; }
            QPushButton#quietButton { background: transparent; border: 0;
                                      color: #a7b9ce; padding: 4px; }
            QPushButton#quietButton:hover { color: white; }
            QPushButton#primaryButton:enabled { background: #34475e;
                border-color: #657991; font-weight: 600; }
            QPushButton#primaryButton:enabled:hover { background: #435873; }
            QToolButton#refreshButton { background: transparent; border: none;
                color: #8793a2; font-size: 18px; padding: 0; }
            QToolButton#refreshButton:hover { color: #d2dbe5; }
            QTabWidget#mainTabs::pane { border: 1px solid #343c49;
                                         border-radius: 5px; }
            QTabBar::tab { background: #1b2029; color: #aab8ca;
                           border: 1px solid #343c49; border-bottom: 0;
                           padding: 8px 20px; margin-right: 3px; }
            QTabBar::tab:selected { background: #292f3b; color: #f4f6fb;
                                    border-bottom: 2px solid #77a9de; }
        """)

        # Separate catalogue management from monitor wallpaper selection.
        # Collection subscriptions will be integrated into Displays in C1.
        self.library_panel = LibraryPanel(self)
        self.tabs.addTab(self.library_panel, "Library")

        self.source_mode.currentIndexChanged.connect(self.source_changed)
        self.folder_order.currentIndexChanged.connect(self.order_changed)
        self.scene.selectionChanged.connect(self.monitor_selected)
        self.view.selection_changed_handler = self.monitor_selected
        self.draw_monitors()

        # Qt emits screen-change signals on hot-plug, removal and rearrangement.
        # Debounce them so Windows has time to finish updating display geometry.
        self._display_refresh_timer = QTimer(self)
        self._display_refresh_timer.setSingleShot(True)
        self._display_refresh_timer.setInterval(650)
        self._display_refresh_timer.timeout.connect(self.refresh_monitors)
        app = QApplication.instance()
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(lambda *_: self.schedule_display_refresh())
        app.primaryScreenChanged.connect(
            lambda *_: self.schedule_display_refresh()
        )
        for screen in app.screens():
            self._watch_screen(screen)
        if self.monitors:
            self.monitor_items[self.monitors[0].device_id].setSelected(True)
        else:
            self.monitor_selected()

    def _watch_screen(self, screen):
        """Watch movement, resizing and rotation of an existing Qt screen."""
        screen.geometryChanged.connect(
            lambda *_: self.schedule_display_refresh()
        )
        screen.orientationChanged.connect(
            lambda *_: self.schedule_display_refresh()
        )
        screen.virtualGeometryChanged.connect(
            lambda *_: self.schedule_display_refresh()
        )

    def _on_screen_added(self, screen):
        self._watch_screen(screen)
        self.schedule_display_refresh()

    def schedule_display_refresh(self):
        self._display_refresh_timer.start()

    @staticmethod
    def _monitor_signature(monitors):
        """Compare real displays, including position, resolution and rotation."""
        return tuple(sorted(
            (m.device_id, m.x, m.y, m.width, m.height, m.orientation)
            for m in monitors
        ))

    def refresh_monitors(self, manual=False):
        """Rebuild the diagram safely when Windows' active monitors change."""
        try:
            detected = discover_monitors()
            mapping = load_monitor_mapping()
        except Exception as error:
            # A transient Windows display-query failure must not close the app.
            self.status.setText("Could not refresh displays. Try again.")
            self.refresh_button.setToolTip(f"Display refresh failed: {error}")
            return

        unchanged = (
            self._monitor_signature(detected) ==
            self._monitor_signature(self.monitors)
            and mapping == self.mapping
        )
        if unchanged:
            if manual:
                self.status.setText("Displays are up to date.")
            return

        preserved_selection = set(self.selected_device_ids)
        self.view.cancel_selection_gesture()
        was_blocked = self.scene.blockSignals(True)
        try:
            self.scene.clear()
            self.scene.setSceneRect(QRectF())
            self.monitor_items.clear()
            self.monitors = detected
            self.mapping = mapping
            self.draw_monitors()
            for device_id in preserved_selection:
                tile = self.monitor_items.get(device_id)
                if tile is not None:
                    tile.setSelected(True)
        finally:
            self.scene.blockSignals(was_blocked)

        self.select_all_button.setEnabled(bool(self.monitors))
        self.monitor_selected()
        self.view.viewport().update()
        self.refresh_button.setToolTip("Refresh displays")
        if self.monitors:
            self.status.setText(
                f"Detected {display_count_text(len(self.monitors))}."
            )
        else:
            self.status.setText("No active displays detected.")

    def draw_monitors(self):
        """Use Windows coordinates so orientation/placement are not hardcoded."""
        names = {device_id: role for role, device_id in self.mapping.items()}
        for index, monitor in enumerate(self.monitors, 1):
            role = names.get(monitor.device_id, f"Monitor {index}")
            item = MonitorTile(
                monitor, role,
                lambda device_id=monitor.device_id: self.choose_image_for(device_id),
            )
            self.monitor_items[monitor.device_id] = item
            self.scene.addItem(item)

        if self.monitors:
            bounds = self.scene.itemsBoundingRect()
            self.scene.setSceneRect(bounds.adjusted(-200, -200, 200, 200))
            self.view.fitInView(
                self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
            )
        else:
            self.status.setText("No displays detected.")

    def toggle_select_all(self):
        select = len(self.selected_device_ids) != len(self.monitors)
        self.scene.blockSignals(True)
        try:
            for item in self.monitor_items.values():
                item.setSelected(select)
        finally:
            self.scene.blockSignals(False)
        self.monitor_selected()

    def monitor_selected(self):
        self.selected_device_ids = [
            monitor.device_id for monitor in self.monitors
            if self.monitor_items[monitor.device_id].isSelected()
        ]
        if self.monitors:
            self.status.clear()
        count = len(self.selected_device_ids)
        self.selected_device_id = (
            self.selected_device_ids[0] if count == 1 else None
        )
        for item in self.monitor_items.values():
            item.refresh_appearance()
        self.select_all_button.setText(
            "Clear selection" if count == len(self.monitors) and count else "Select all"
        )

        # Keep the panel present so the monitor diagram never jumps in size.
        self.panel.setEnabled(count == 1)
        if count != 1:
            self.selected_heading.setText(
                f"{display_count_text(count)} selected" if count else "Select a display"
            )
            # Neutral disabled values, not the last monitor's personal settings.
            self._restoring = True
            try:
                self.source_mode.setCurrentIndex(0)
                self.image_path.clear()
                self.source_folder.clear()
                self.folder_order.setCurrentIndex(0)
            finally:
                self._restoring = False
            self.refresh_source_controls()
            return

        self.selected_heading.setText(self.monitor_items[self.selected_device_id].data(0))
        profile = self.profiles.get(self.selected_device_id, default_profile())

        self._restoring = True
        try:
            index = self.source_mode.findData(profile["mode"])
            self.source_mode.setCurrentIndex(max(0, index))
            self.image_path.setText(profile["image"])
            self.source_folder.setText(profile["folder"])
            order_index = self.folder_order.findData(profile.get("order", "random"))
            self.folder_order.setCurrentIndex(max(0, order_index))
        finally:
            self._restoring = False
        self.refresh_source_controls()

    def refresh_source_controls(self):
        fixed = self.source_mode.currentData() == "fixed"
        self.source_pages.setCurrentIndex(0 if fixed else 1)
        self.order_label.setEnabled(not fixed)
        self.folder_order.setEnabled(not fixed)
        self.update_action()

    def source_changed(self):
        if self._restoring:
            return
        self.save_current_profile()
        self.refresh_source_controls()

    def order_changed(self):
        if not self._restoring:
            self.save_current_profile()

    def save_current_profile(self):
        if self._restoring or not self.selected_device_id:
            return
        profile = self.profiles.setdefault(
            self.selected_device_id, default_profile()
        )
        profile.update({
            "mode": self.source_mode.currentData(),
            "image": self.image_path.text(),
            "folder": self.source_folder.text(),
            "order": self.folder_order.currentData(),
        })
        try:
            save_profiles(self.profiles)
        except OSError as error:
            QMessageBox.critical(self, "Preferences Error", str(error))
        self.update_action()

    def folder_for(self, device_id):
        profile = self.profiles.get(device_id, {})
        return profile.get("folder", "") or self.library_path.text()

    def update_action(self):
        ids = self.selected_device_ids
        if not ids:
            self.action_button.setText("Select a display")
            self.action_button.setEnabled(False)
            self.action_button.setToolTip("")
            return

        if len(ids) == 1 and self.profiles.get(ids[0], {}).get("mode") == "fixed":
            self.action_button.setText("Apply image")
            image_path = self.profiles[ids[0]].get("image", "")
            valid = bool(image_path) and Path(image_path).is_file()
            hint = "Choose an image first" if not valid else ""
        else:
            count = len(ids)
            selected_orders = {
                self.profiles.get(device_id, {}).get("order", "random")
                for device_id in ids
            }
            if count == 1:
                self.action_button.setText({
                    "random": "Shuffle display",
                    "next": "Next wallpaper",
                    "previous": "Previous wallpaper",
                }.get(next(iter(selected_orders)), "Change wallpaper"))
            elif len(selected_orders) == 1:
                self.action_button.setText({
                    "random": f"Shuffle {display_count_text(count)}",
                    "next": f"Next on {display_count_text(count)}",
                    "previous": f"Previous on {display_count_text(count)}",
                }.get(next(iter(selected_orders)), f"Update {display_count_text(count)}"))
            else:
                self.action_button.setText(f"Update {display_count_text(count)}")
            has_fixed = any(
                self.profiles.get(device_id, {}).get("mode") == "fixed"
                for device_id in ids
            )
            folders_valid = all(
                self.folder_for(device_id)
                and Path(self.folder_for(device_id)).is_dir()
                for device_id in ids
            )
            valid = not has_fixed and folders_valid
            hint = (
                "Fixed-image displays cannot be shuffled" if has_fixed
                else "Choose a wallpaper folder first" if not folders_valid else ""
            )

        self.action_button.setEnabled(valid)
        self.action_button.setToolTip(hint)

    def choose_library(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Main Wallpaper Library", self.library_path.text()
        )
        if folder:
            self.library_path.setText(folder)
            self.settings.setValue("wallpaper_library", folder)
            self.settings.sync()
            self.update_action()

    def choose_image(self):
        if self.selected_device_id:
            self.choose_image_for(self.selected_device_id)

    def choose_image_for(self, device_id):
        profile = self.profiles.get(device_id, default_profile())
        start = profile.get("image", "") or self.library_path.text()
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose Wallpaper", start,
            "Images (*.jpg *.jpeg *.png *.bmp)"
        )
        if not filename:
            return

        # The image icon's action is immediate: choose, apply, and save.
        try:
            set_monitor_wallpaper(device_id, Path(filename))
        except Exception as error:
            QMessageBox.critical(self, "Wallpaper Error", str(error))
            return

        updated = self.profiles.setdefault(device_id, default_profile())
        updated["mode"] = "fixed"
        updated["image"] = filename
        updated["last_image"] = filename
        try:
            save_profiles(self.profiles)
        except OSError as error:
            QMessageBox.warning(
                self, "Preferences Error",
                f"Wallpaper applied, but the preference was not saved: {error}",
            )

        # Show the image that was just assigned, even after multi-selection.
        self.scene.blockSignals(True)
        try:
            for tile_id, tile in self.monitor_items.items():
                tile.setSelected(tile_id == device_id)
        finally:
            self.scene.blockSignals(False)
        self.monitor_selected()
        self.status.setText(f"Applied {Path(filename).name}.")

    def choose_monitor_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Folder for This Monitor",
            self.source_folder.text() or self.library_path.text(),
        )
        if folder:
            self.source_folder.setText(folder)
            self.save_current_profile()

    def use_main_library(self):
        """Clear this monitor's folder override."""
        self.source_folder.clear()
        self.save_current_profile()

    def change_selection(self):
        # Recheck even if invoked programmatically instead of via the button.
        self.update_action()
        if not self.action_button.isEnabled():
            return
        ids = self.selected_device_ids[:]
        try:
            if len(ids) == 1 and self.profiles.get(ids[0], {}).get("mode") == "fixed":
                image = Path(self.profiles[ids[0]]["image"])
                set_monitor_wallpaper(ids[0], image)
                result = f"Applied {image.name}."
            else:
                shared = self.library_path.text()
                assignments, device_ids = create_profile_plan(
                    Path(shared) if shared else None, self.profiles,
                    selected_device_ids=ids,
                )
                apply_plan(assignments, device_ids)
                for device_id, wallpaper in assignments.items():
                    self.profiles.setdefault(device_id, default_profile())[
                        "last_image"
                    ] = str(wallpaper.path)
                try:
                    save_profiles(self.profiles)
                except OSError as error:
                    QMessageBox.warning(
                        self, "Preferences Error",
                        f"Wallpapers changed, but the position was not saved: {error}",
                    )
                result = f"Updated {display_count_text(len(assignments))}."
        except Exception as error:
            QMessageBox.critical(self, "Wallpaper Error", str(error))
            return
        self.status.setText(result)

    def closeEvent(self, event):
        """Keep the scan worker alive until its database update completes."""
        if self.library_panel.scan_running():
            QMessageBox.information(
                self, "Library Scan Running",
                "Please wait for the library scan to finish before closing."
            )
            event.ignore()
            return
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = WallpaperWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
