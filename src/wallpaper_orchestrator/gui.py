"""Monitor-first desktop interface for Wallpaper Orchestrator."""

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from wallpaper_orchestrator.engine import apply_plan, create_profile_plan
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


class MonitorView(QGraphicsView):
    """Fit the monitor geometry to the available view space."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene() and not self.scene().sceneRect().isEmpty():
            self.fitInView(
                self.scene().sceneRect(),
                Qt.AspectRatioMode.KeepAspectRatio,
            )


class WallpaperWindow(QMainWindow):
    """One selected monitor, one source, and two explicit actions."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wallpaper Orchestrator")
        self.resize(1080, 800)

        # Keep the existing global library preference from older GUI versions.
        self.settings = QSettings("WallpaperOrchestrator", "WallpaperOrchestrator")
        self.mapping = load_monitor_mapping()
        self.monitors = discover_monitors()
        self.profiles = load_profiles()
        self.selected_device_id = None
        self.monitor_items = {}
        self._restoring = False

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Wallpaper Orchestrator")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel(
            "Choose a display, set its wallpaper source, then change one or all displays."
        )
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        # Shared library: fallback for any monitor without a custom folder.
        library_caption = QLabel("MAIN WALLPAPER LIBRARY")
        library_caption.setObjectName("sectionLabel")
        layout.addWidget(library_caption)
        library_row = QHBoxLayout()
        self.library_path = QLineEdit()
        self.library_path.setReadOnly(True)
        self.library_path.setPlaceholderText("Choose your main wallpaper library...")
        saved = self.settings.value("wallpaper_library", "", type=str)
        if saved and Path(saved).is_dir():
            self.library_path.setText(saved)
        choose_library = QPushButton("Choose Library")
        choose_library.clicked.connect(self.choose_library)
        library_row.addWidget(self.library_path, 1)
        library_row.addWidget(choose_library)
        layout.addLayout(library_row)

        display_caption = QLabel("YOUR DISPLAYS · Click a monitor to configure it")
        display_caption.setObjectName("sectionLabel")
        layout.addWidget(display_caption)
        self.scene = QGraphicsScene(self)
        self.view = MonitorView(self.scene)
        self.view.setMinimumHeight(270)
        self.view.setBackgroundBrush(QColor("#151b24"))
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout.addWidget(self.view, 1)

        # The controls below all belong to the currently selected monitor.
        self.panel = QFrame()
        self.panel.setObjectName("monitorPanel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(10)
        self.selected_heading = QLabel("Select a monitor above")
        self.selected_heading.setObjectName("panelTitle")
        panel_layout.addWidget(self.selected_heading)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Wallpaper source"))
        self.source_mode = QComboBox()
        self.source_mode.addItem("One specific wallpaper", "fixed")
        self.source_mode.addItem("Random images from a folder", "folder")
        source_row.addWidget(self.source_mode, 1)
        panel_layout.addLayout(source_row)

        # Source-specific controls: only the relevant picker is displayed.
        self.source_pages = QStackedWidget()
        fixed_page = QWidget()
        fixed_row = QHBoxLayout(fixed_page)
        fixed_row.setContentsMargins(0, 0, 0, 0)
        self.image_path = QLineEdit()
        self.image_path.setPlaceholderText("Choose the image to keep on this monitor")
        self.image_path.setReadOnly(True)
        pick_image = QPushButton("Browse Image")
        pick_image.clicked.connect(self.choose_image)
        fixed_row.addWidget(self.image_path, 1)
        fixed_row.addWidget(pick_image)
        self.source_pages.addWidget(fixed_page)

        folder_page = QWidget()
        folder_row = QHBoxLayout(folder_page)
        folder_row.setContentsMargins(0, 0, 0, 0)
        self.source_folder = QLineEdit()
        self.source_folder.setReadOnly(True)
        self.source_folder.setPlaceholderText("Uses the main wallpaper library")
        pick_folder = QPushButton("Choose Folder")
        pick_folder.clicked.connect(self.choose_monitor_folder)
        use_main = QPushButton("Use Main Library")
        use_main.clicked.connect(self.use_main_library)
        folder_row.addWidget(self.source_folder, 1)
        folder_row.addWidget(pick_folder)
        folder_row.addWidget(use_main)
        self.source_pages.addWidget(folder_page)
        panel_layout.addWidget(self.source_pages)

        self.include_all = QCheckBox("Include this monitor when I click Shuffle All")
        panel_layout.addWidget(self.include_all)
        self.source_hint = QLabel("")
        self.source_hint.setWordWrap(True)
        self.source_hint.setObjectName("muted")
        panel_layout.addWidget(self.source_hint)
        layout.addWidget(self.panel)

        # Actions have a single, consistent scope.
        action_row = QHBoxLayout()
        self.single_action = QPushButton("Shuffle This Monitor")
        self.single_action.clicked.connect(self.change_selected)
        self.shuffle_all_button = QPushButton("Shuffle All Participating Monitors")
        self.shuffle_all_button.setObjectName("primaryButton")
        self.shuffle_all_button.clicked.connect(self.shuffle_all)
        action_row.addWidget(self.single_action, 1)
        action_row.addWidget(self.shuffle_all_button, 1)
        layout.addLayout(action_row)
        self.status = QLabel("Ready. Wallpaper preferences save automatically.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #10151c; color: #e7edf5; }
            QLabel#title { font-size: 22px; font-weight: 650; }
            QLabel#panelTitle { font-size: 16px; font-weight: 600; }
            QLabel#muted { color: #9aaabd; }
            QLabel#sectionLabel { color: #94a9c4; font-size: 11px; font-weight: bold; }
            QFrame#monitorPanel { border: 1px solid #354257; border-radius: 9px;
                                  background: #1b2532; }
            QFrame#monitorPanel QLabel { background: transparent; }
            QLineEdit, QComboBox { background: #222e3d; border: 1px solid #45556a;
                                   border-radius: 6px; padding: 8px; }
            QPushButton { background: #2b394b; border: 1px solid #465771;
                          border-radius: 7px; padding: 9px 12px; }
            QPushButton:hover { background: #384a60; }
            QPushButton:disabled { color: #708093; background: #1b2532; }
            QPushButton#primaryButton { background: #2360aa; border-color: #357bc9;
                                        font-weight: bold; }
            QPushButton#primaryButton:hover { background: #3476c3; }
        """)

        self.source_mode.currentIndexChanged.connect(self.source_changed)
        self.include_all.toggled.connect(self.save_current_profile)
        self.scene.selectionChanged.connect(self.monitor_selected)
        self.draw_monitors()
        self.panel.setEnabled(False)
        self.single_action.setEnabled(False)

    def draw_monitors(self):
        """Use Windows coordinates so orientation/placement are not hardcoded."""
        names = {device_id: role for role, device_id in self.mapping.items()}
        for index, monitor in enumerate(self.monitors, 1):
            role = names.get(monitor.device_id, f"Monitor {index}")
            rect = QRectF(monitor.x, monitor.y, monitor.width, monitor.height)
            item = self.scene.addRect(
                rect, QPen(QColor("#778aa3"), 12), QBrush(QColor("#29394d"))
            )
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            item.setData(0, role)
            item.setData(1, monitor.device_id)
            self.monitor_items[monitor.device_id] = item

            label = self.scene.addText(
                f"{role}\n{monitor.width} × {monitor.height}\n{monitor.orientation.title()}"
            )
            label.setDefaultTextColor(QColor("white"))
            font = label.font()
            font.setPixelSize(90)
            label.setFont(font)
            bounds = label.boundingRect()
            label.setPos(
                monitor.x + (monitor.width - bounds.width()) / 2,
                monitor.y + (monitor.height - bounds.height()) / 2,
            )
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        if self.monitors:
            bounds = self.scene.itemsBoundingRect()
            self.scene.setSceneRect(bounds.adjusted(-200, -200, 200, 200))
            self.view.fitInView(
                self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
            )
        else:
            self.status.setText("No active monitors found. Check Windows Display Settings.")

    def monitor_selected(self):
        selected = self.scene.selectedItems()
        active_item = next((i for i in selected if i.data(1)), None)
        self.selected_device_id = active_item.data(1) if active_item else None
        for item in self.monitor_items.values():
            color = "#5aaffe" if item.isSelected() else "#778aa3"
            item.setPen(QPen(QColor(color), 16 if item.isSelected() else 12))

        has_selection = self.selected_device_id is not None
        self.panel.setEnabled(has_selection)
        self.single_action.setEnabled(has_selection)
        if not has_selection:
            self.selected_heading.setText("Select a monitor above")
            return

        monitor = next(m for m in self.monitors if m.device_id == self.selected_device_id)
        self.selected_heading.setText(
            f"{active_item.data(0)} · {monitor.width} × {monitor.height} "
            f"({monitor.orientation})"
        )
        profile = self.profiles.get(self.selected_device_id, default_profile())

        self._restoring = True
        index = self.source_mode.findData(profile["mode"])
        self.source_mode.setCurrentIndex(max(0, index))
        self.image_path.setText(profile["image"])
        self.source_folder.setText(profile["folder"])
        self.include_all.setChecked(profile["shuffle_all"])
        self._restoring = False
        self.refresh_source_controls()

    def refresh_source_controls(self):
        fixed = self.source_mode.currentData() == "fixed"
        self.source_pages.setCurrentIndex(0 if fixed else 1)
        self.include_all.setEnabled(not fixed)
        self.single_action.setText(
            "Apply This Wallpaper" if fixed else "Shuffle This Monitor"
        )
        self.source_hint.setText(
            "This specific image stays unchanged during Shuffle All."
            if fixed else
            "Only this display changes with Shuffle This Monitor. Shuffle All "
            "changes every participating random-folder monitor and respects "
            "the global theme limits in config.json."
        )

    def source_changed(self):
        self.refresh_source_controls()
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
            "shuffle_all": self.include_all.isChecked(),
        })
        try:
            save_profiles(self.profiles)
        except OSError as error:
            QMessageBox.critical(self, "Preferences Error", str(error))
        else:
            self.status.setText("Monitor preferences saved automatically.")

    def choose_library(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Main Wallpaper Library", self.library_path.text()
        )
        if folder:
            self.library_path.setText(folder)
            self.settings.setValue("wallpaper_library", folder)
            self.settings.sync()
            self.status.setText("Main wallpaper library saved.")

    def choose_image(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose Specific Wallpaper", self.image_path.text(),
            "Images (*.jpg *.jpeg *.png *.bmp)"
        )
        if filename:
            self.image_path.setText(filename)
            self.save_current_profile()

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

    def change_selected(self):
        if not self.selected_device_id:
            return
        try:
            if self.source_mode.currentData() == "fixed":
                image = Path(self.image_path.text())
                if not self.image_path.text() or not image.is_file():
                    raise ValueError("Choose an existing image for this monitor first.")
                set_monitor_wallpaper(self.selected_device_id, image)
                result = f"Applied {image.name} to the selected monitor."
            else:
                shared = self.library_path.text()
                assignments, device_ids = create_profile_plan(
                    Path(shared) if shared else None, self.profiles,
                    selected_device_id=self.selected_device_id,
                )
                apply_plan(assignments, device_ids)
                result = "Changed only the selected monitor."
        except Exception as error:
            QMessageBox.critical(self, "Wallpaper Error", str(error))
            return
        self.status.setText(result)

    def shuffle_all(self):
        try:
            shared = self.library_path.text()
            assignments, device_ids = create_profile_plan(
                Path(shared) if shared else None, self.profiles
            )
            if not assignments:
                self.status.setText(
                    "Nothing to shuffle. Every monitor is fixed or excluded."
                )
                return
            apply_plan(assignments, device_ids)
        except Exception as error:
            QMessageBox.critical(self, "Shuffle Error", str(error))
            return
        self.status.setText(
            f"Updated {len(assignments)} participating monitor(s); "
            "fixed and excluded monitors were left unchanged."
        )


def main():
    app = QApplication(sys.argv)
    window = WallpaperWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
