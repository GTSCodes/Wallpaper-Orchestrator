"""Graphical interface for Wallpaper Orchestrator."""

import sys

from pathlib import Path

from PySide6.QtCore import Qt, QRectF
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
)

from wallpaper_orchestrator.platforms.windows import discover_monitors, set_monitor_wallpaper
from wallpaper_orchestrator.state import load_monitor_mapping
from wallpaper_orchestrator.engine import create_plan, apply_plan


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

        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(self.view, stretch=1)
        layout.addWidget(self.info)
        # Wallpaper selection controls
        self.image_path = QLineEdit()
        self.image_path.setPlaceholderText("Select a wallpaper image...")
        self.image_path.setReadOnly(True)

        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_wallpaper)

        self.apply_button = QPushButton("Apply Wallpaper")
        self.apply_button.clicked.connect(self.apply_wallpaper)

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
        """Update the interface when a monitor is clicked."""
        self.selected_device_id = None
        self.apply_button.setEnabled(False)

        for item in self.scene.items():
            if not isinstance(item, QGraphicsItem):
                continue

            if not item.data(1):
                continue

            if item.isSelected():
                item.setPen(QPen(QColor("#45a3ff"), 16))

                role = item.data(0)
                self.selected_device_id = item.data(1)
                self.apply_button.setEnabled(True)

                self.info.setText(
                    f"Selected monitor: {role}"
                )

            else:
                item.setPen(QPen(QColor("#718096"), 12))
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
        """Select the wallpaper library."""

        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Wallpaper Library",
        )

        if folder:
            self.folder_path.setText(folder)


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


def main():
    app = QApplication(sys.argv)

    window = WallpaperWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())