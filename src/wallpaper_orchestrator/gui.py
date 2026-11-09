"""Graphical interface for Wallpaper Orchestrator."""

import sys

from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QSettings
from PySide6.QtGui import QColor, QBrush, QPen, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QComboBox,
)

from wallpaper_orchestrator.platforms.windows import discover_monitors, set_monitor_wallpaper
from wallpaper_orchestrator.state import load_monitor_mapping
from wallpaper_orchestrator.engine import create_plan, apply_plan
from wallpaper_orchestrator.library import load_wallpapers
from wallpaper_orchestrator.models import Monitor
from wallpaper_orchestrator.selector import choose_wallpaper


class MonitorView(QGraphicsView):
    """Display monitors using their actual relative geometry."""

    def resizeEvent(self, event):
        super().resizeEvent(event)

        if self.scene() and not self.scene().sceneRect().isEmpty():
            self.fitInView(
                self.scene().sceneRect(),
                Qt.AspectRatioMode.KeepAspectRatio,
            )


class WallpaperWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.settings = QSettings(
        "WallpaperOrchestrator",
        "WallpaperOrchestrator",
    )

        self.setWindowTitle("Wallpaper Orchestrator")
        self.resize(1000, 650)

        self.mapping = load_monitor_mapping()
        self.monitors = discover_monitors()
        self.selected_device_id = None

        self.scene = QGraphicsScene(self)
        self.view = MonitorView(self.scene)
        self.view.setBackgroundBrush(QColor("#171b23"))
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)

        self.info = QLabel("Select a monitor to configure it.")
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info.setStyleSheet(
            "color: white; font-size: 15px; padding: 12px;"
        )

        self.mode_label = QLabel("Wallpaper behaviour:")
        self.mode_label.setStyleSheet("color: white;")

        self.mode_selector = QComboBox()

        self.mode_selector.addItems([
            "Fixed Image",
            "Random Folder",
            "Coordinated",
        ])

        self.mode_selector.setEnabled(False)

        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(self.view, stretch=1)
        layout.addWidget(self.info)
        layout.addWidget(self.mode_label)
        layout.addWidget(self.mode_selector)
        # Wallpaper selection controls
        self.image_path = QLineEdit()
        self.image_path.setPlaceholderText("Select a wallpaper image...")
        self.image_path.setReadOnly(True)

        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_wallpaper)

        self.apply_button = QPushButton("Apply Wallpaper")
        self.apply_button.clicked.connect(self.apply_wallpaper)

        self.shuffle_selected_button = QPushButton("Shuffle Selected Monitor")

        self.shuffle_selected_button.clicked.connect(
            self.shuffle_selected
        )

        self.shuffle_selected_button.setEnabled(False)

        layout.addWidget(self.shuffle_selected_button)

        # A monitor must be selected before applying.
        self.apply_button.setEnabled(False)

        image_row = QHBoxLayout()
        image_row.addWidget(self.image_path)
        image_row.addWidget(self.browse_button)

        layout.addLayout(image_row)
        layout.addWidget(self.apply_button)

        # Coordinated wallpaper controls
        self.folder_path = QLineEdit()
        self.folder_path.setPlaceholderText(
            "Choose your wallpaper library folder..."
        )
        self.folder_path.setReadOnly(True)
        # Restore the previously selected wallpaper folder.
        saved_folder = self.settings.value(
            "wallpaper_library",
            "",
            type=str,
        )

        if saved_folder and Path(saved_folder).is_dir():
            self.folder_path.setText(saved_folder)

        self.folder_button = QPushButton("Choose Folder")
        self.folder_button.clicked.connect(self.browse_folder)

        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_path)
        folder_row.addWidget(self.folder_button)

        self.shuffle_all_button = QPushButton("Shuffle All")
        self.shuffle_all_button.clicked.connect(self.shuffle_all)

        layout.addLayout(folder_row)
        layout.addWidget(self.shuffle_all_button)

        self.setCentralWidget(container)

        self.setStyleSheet(
            "QMainWindow { background-color: #101319; }"
        )

        self.draw_monitors()

        self.scene.selectionChanged.connect(
            self.monitor_selected
        )

    def draw_monitors(self):
        """Draw every active monitor at its actual relative position."""

        if not self.monitors:
            self.info.setText("No active monitors detected.")
            return

        names_by_id = {
            device_id: name
            for name, device_id in self.mapping.items()
        }

        for index, monitor in enumerate(self.monitors, start=1):

            role = names_by_id.get(
                monitor.device_id,
                f"Monitor {index}",
            )

            rectangle = QRectF(
                monitor.x,
                monitor.y,
                monitor.width,
                monitor.height,
            )

            item = self.scene.addRect(
                rectangle,
                QPen(QColor("#718096"), 12),
                QBrush(QColor("#263447")),
            )

            item.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            )

            item.setData(0, role)
            item.setData(1, monitor.device_id)

            # Create a label inside the monitor rectangle.
            label = self.scene.addText(
                f"{role}\n"
                f"{monitor.width} × {monitor.height}\n"
                f"{monitor.orientation.title()}"
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

            # Clicking the label should select the rectangle beneath.
            label.setAcceptedMouseButtons(
                Qt.MouseButton.NoButton
            )

        bounds = self.scene.itemsBoundingRect()

        self.scene.setSceneRect(
            bounds.adjusted(-200, -200, 200, 200)
        )

        self.view.fitInView(
            self.scene.sceneRect(),
            Qt.AspectRatioMode.KeepAspectRatio,
        )

    def monitor_selected(self):
        """Update controls when a monitor is selected."""

        self.selected_device_id = None

        # Reset controls.
        self.apply_button.setEnabled(False)
        self.mode_selector.setEnabled(False)
        self.shuffle_selected_button.setEnabled(False)

        for item in self.scene.items():

            if not item.data(1):
                continue

            if item.isSelected():
                item.setPen(QPen(QColor("#45a3ff"), 16))

                role = item.data(0)

                self.selected_device_id = item.data(1)

                self.apply_button.setEnabled(True)
                self.mode_selector.setEnabled(True)
                self.shuffle_selected_button.setEnabled(True)

                self.info.setText(
                    f"Selected monitor: {role}"
                )

            else:
                item.setPen(QPen(QColor("#718096"), 12))

        if self.selected_device_id is None:
            self.info.setText(
                "Select a monitor to configure it."
            )
    def browse_wallpaper(self):
        """Let the user select a wallpaper image."""

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Wallpaper",
            "",
            "Images (*.jpg *.jpeg *.png *.bmp)",
        )

        if filename:
            self.image_path.setText(filename)

    def browse_folder(self):
        """Select and remember the wallpaper library."""

        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Wallpaper Library",
            self.folder_path.text(),
        )

        if folder:
            self.folder_path.setText(folder)

            # Remember this folder across application launches.
            self.settings.setValue(
                "wallpaper_library",
                folder,
            )

            self.settings.sync()

    def shuffle_all(self):
        """Preview and optionally apply coordinated wallpapers."""

        folder = Path(self.folder_path.text())

        if not folder.is_dir():
            QMessageBox.warning(
                self,
                "Invalid Folder",
                "Please choose an existing wallpaper folder.",
            )
            return

        try:
            assignments, device_ids = create_plan(folder)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Planning Error",
                str(error),
            )
            return

        # Show the complete plan before applying anything.
        lines = []

        for role, wallpaper in assignments.items():
            themes = ", ".join(sorted(wallpaper.themes))

            lines.append(
                f"{role}\n"
                f"  Image: {wallpaper.path.name}\n"
                f"  Themes: {themes}"
            )

        preview = "\n\n".join(lines)

        try:
            apply_plan(assignments, device_ids)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Wallpaper Error",
                str(error),
            )
            return

        self.info.setText(
            "All wallpapers shuffled successfully."
        )


    def apply_wallpaper(self):
        """Apply the chosen image to the selected monitor."""

        if not self.selected_device_id:
            QMessageBox.warning(
                self,
                "No Monitor Selected",
                "Please select a monitor first.",
            )
            return

        image = Path(self.image_path.text())

        if not image.is_file():
            QMessageBox.warning(
                self,
                "Invalid Wallpaper",
                "Please select an existing image.",
            )
            return

        try:
            set_monitor_wallpaper(
                self.selected_device_id,
                image,
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Wallpaper Error",
                f"Could not apply wallpaper:\n{error}",
            )
            return

        self.info.setText(
            f"Wallpaper applied successfully: {image.name}"
        )

    def shuffle_selected(self):
        """Shuffle the selected monitor using its chosen mode."""

        if not self.selected_device_id:
            return

        mode = self.mode_selector.currentText()

        if mode == "Fixed Image":
            self.apply_wallpaper()
            return

        if mode == "Coordinated":
            self.shuffle_all()
            return

        folder = Path(self.folder_path.text())

        if not folder.is_dir():
            QMessageBox.warning(
                self,
                "Folder Required",
                "Please choose a wallpaper library folder.",
            )
            return

        detected = next(
            (
                monitor
                for monitor in self.monitors
                if monitor.device_id == self.selected_device_id
            ),
            None,
        )

        if detected is None:
            QMessageBox.critical(
                self,
                "Monitor Unavailable",
                "The selected monitor is no longer active.",
            )
            return

        try:
            wallpapers = load_wallpapers(folder)

            compatible = [
                wallpaper
                for wallpaper in wallpapers
                if wallpaper.path.suffix.lower()
                in {".jpg", ".jpeg", ".png", ".bmp"}
                and wallpaper.path.is_file()
            ]

            monitor = Monitor(
                role="selected",
                orientation=detected.orientation,
            )

            chosen = choose_wallpaper(
                monitor,
                compatible,
            )

            set_monitor_wallpaper(
                self.selected_device_id,
                chosen.path,
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Shuffle Error",
                str(error),
            )
            return

        self.info.setText(
            f"Wallpaper shuffled: {chosen.path.name}"
        )


def main():
    app = QApplication(sys.argv)

    window = WallpaperWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())