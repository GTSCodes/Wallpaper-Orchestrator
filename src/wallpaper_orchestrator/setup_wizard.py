"""C8.2 guided Windows setup; all wallpaper changes remain opt-in.

The wizard stores per-user settings, but never applies an image or replaces
existing files. Advanced configuration stays in the original app tabs.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget, QWizard,
    QWizardPage,
)

from wallpaper_orchestrator.automation import automation_path, load_automation, save_automation, set_interval
from wallpaper_orchestrator.background import set_background_enabled
from wallpaper_orchestrator.monitor_labels import monitor_labels
from wallpaper_orchestrator.onboarding_state import default_background_for_setup
from wallpaper_orchestrator.profiles import default_profile, load_profiles, save_profiles


def _page(title: str, explanation: str) -> tuple[QWizardPage, QVBoxLayout]:
    page = QWizardPage()
    page.setTitle(title)
    page.setSubTitle(explanation)
    layout = QVBoxLayout(page)
    return page, layout


class FirstRunWizard(QWizard):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Wallpaper Orchestrator — Setup")
        self.resize(670, 460)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)

        welcome, layout = _page(
            "Welcome to Wallpaper Orchestrator",
            "Choose your folders and displays. Nothing changes your wallpaper until you ask.",
        )
        introduction = QLabel(
            "You can organise your wallpaper library now, or skip this setup and "
            "return later from the Setup menu. Existing collections and saved "
            "settings are never cleared by this wizard."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        layout.addStretch()
        self.addPage(welcome)

        library, layout = _page(
            "Choose your main wallpaper folder",
            "This is a fallback source for monitors without a custom folder.",
        )
        row = QHBoxLayout()
        self.folder = QLineEdit(window.settings.value("wallpaper_library", "", type=str))
        self.folder.setPlaceholderText("Optional: choose an existing image folder")
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse(self.folder))
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        layout.addWidget(QLabel("Your files stay where they are; no images are copied or changed."))
        layout.addStretch()
        self.addPage(library)

        displays, layout = _page(
            "Assign folders to detected displays",
            "Optional per-monitor overrides. Blank means use the main folder.",
        )
        self.monitor_folders: dict[str, QLineEdit] = {}
        labels = monitor_labels(window.monitors, window.mapping)
        saved_profiles = load_profiles()
        if not window.monitors:
            layout.addWidget(QLabel("No active displays detected. You can set them up later."))
        for monitor in window.monitors:
            mid = monitor.device_id
            name = QLabel(labels[mid])
            name.setWordWrap(True)
            layout.addWidget(name)
            row = QHBoxLayout()
            existing = saved_profiles.get(mid, default_profile())
            field = QLineEdit(existing.get("folder", ""))
            field.setPlaceholderText("Use main wallpaper folder")
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _checked=False, edit=field: self._browse(edit))
            clear = QPushButton("Use main")
            clear.clicked.connect(field.clear)
            row.addWidget(field, 1)
            row.addWidget(browse)
            row.addWidget(clear)
            layout.addLayout(row)
            self.monitor_folders[mid] = field
        layout.addStretch()
        self.addPage(displays)

        automation, layout = _page(
            "Optional automatic wallpaper changes",
            "Shuffling runs in a separate background process, even after closing the app.",
        )
        self.automate = QCheckBox("Automatically shuffle these displays")
        self.automate.setChecked(False)  # Never create an interval without consent.
        layout.addWidget(self.automate)
        self.background = QCheckBox(
            "Keep enabled automations running after closing the app "
            "(start with Windows)"
        )
        # Enabled for genuinely new installs only. Existing users' explicit
        # choices, and a safety-restoration pause, must not be overwritten.
        existing = load_automation()
        self.background.setChecked(default_background_for_setup(
            existing, saved_preference_exists=automation_path().exists()
        ))
        layout.addWidget(self.background)
        frequency = QFormLayout()
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 10080)
        self.minutes.setValue(30)
        self.minutes.setSuffix(" minutes")
        self.minutes.setEnabled(False)
        self.automate.toggled.connect(self.minutes.setEnabled)
        frequency.addRow("Shuffle every", self.minutes)
        layout.addLayout(frequency)
        note = QLabel(
            "Background startup is selected by default for new users and "
            "applied when you finish setup. Uncheck it to opt out. "
            "Automatic shuffle is separate: no interval is created until "
            "you select it. Existing pause settings and interval conflicts remain."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        self.addPage(automation)

        finish, layout = _page(
            "Ready to use Wallpaper Orchestrator",
            "Your folder choices will be saved when you select Finish.",
        )
        label = QLabel(
            "You can reopen this wizard from Setup → Run setup wizard. "
            "The installer and wizard never change your current wallpaper automatically."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()
        self.addPage(finish)

    def _browse(self, field: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Wallpaper Folder", field.text())
        if folder:
            field.setText(folder)

    def _valid_folder(self, text: str) -> bool:
        return not text or Path(text).is_dir()

    def accept(self) -> None:
        library = self.folder.text().strip()
        overrides = {mid: field.text().strip()
                     for mid, field in self.monitor_folders.items()}
        if not self._valid_folder(library) or any(
            not self._valid_folder(value) for value in overrides.values()
        ):
            QMessageBox.warning(self, "Missing folder", "Choose existing wallpaper folders.")
            return
        if self.automate.isChecked() and (
            not self.monitor_folders or
            any(not (overrides[mid] or library) for mid in self.monitor_folders)
        ):
            QMessageBox.warning(
                self, "Automatic shuffle", "Assign an image folder to each selected "
                "monitor before enabling the automatic shuffle."
            )
            return
        try:
            profiles = load_profiles()
            # Do not convert existing fixed-image / collection modes into folder
            # mode merely because a wizard was opened again.
            for mid, folder in overrides.items():
                profile = profiles.setdefault(mid, default_profile())
                if folder != profile.get("folder", ""):
                    profile["folder"] = folder
                    profile["mode"] = "folder"
            if any(overrides[mid] != self.window.profiles.get(mid, {}).get("folder", "")
                   for mid in overrides):
                save_profiles(profiles)
            if library and library != self.window.settings.value(
                    "wallpaper_library", "", type=str):
                self.window.settings.setValue("wallpaper_library", library)
                self.window.settings.sync()
            if self.automate.isChecked():
                config = set_interval(load_automation(), list(self.monitor_folders),
                                      self.minutes.value() * 60)
                save_automation(config)
            # Apply the visible preference even if the user configured no
            # interval yet. Never automatically clear a previous safety pause.
            if self.background.isChecked() != load_automation().background_enabled:
                set_background_enabled(self.background.isChecked())
            self.window.settings.setValue("setup/finished", True)
            self.window.settings.sync()
            self.window.profiles = profiles
            if library:
                self.window.library_path.setText(library)
            self.window.monitor_selected()
            self.window.update_action()
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.critical(self, "Setup could not finish", str(error))
            return
        super().accept()

    def reject(self) -> None:
        # Skipping is a valid first-run decision; the menu can reopen setup.
        self.window.settings.setValue("setup/finished", True)
        self.window.settings.sync()
        super().reject()
