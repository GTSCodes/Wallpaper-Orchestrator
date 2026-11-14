"""C8.3 regression tests: migration safety, delivery and support export."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator import desktop_entry
from wallpaper_orchestrator.diagnostics import export_diagnostics, redact
from wallpaper_orchestrator.state_backup import create_backup


ROOT = Path(__file__).resolve().parents[1]


class StateBackupTests(unittest.TestCase):
    def test_empty_user_does_not_get_an_unnecessary_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(create_backup(root=Path(temp), include_qsettings=False))

    def test_consistent_wal_database_json_and_first_restore_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "catalog.sqlite3"
            with WallpaperCatalog(database) as catalog:
                catalog.create_collection("Cathedral Art")
            (root / "profiles.json").write_text('{"test": 1}', encoding="utf-8")
            baseline = root / "wallpaper-first-restore-point.json"
            initial = '{"immutable":true}'
            baseline.write_text(initial, encoding="utf-8")
            backup = create_backup(root=root, include_qsettings=False)
            self.assertIsNotNone(backup)
            self.assertEqual(baseline.read_text(), initial)
            self.assertEqual((backup / baseline.name).read_text(), initial)
            with WallpaperCatalog(backup / "catalog.sqlite3") as snap:
                self.assertEqual([c.name for c in snap.list_collections()],
                                 ["Cathedral Art"])
            manifest = json.loads((backup / "manifest.json").read_text())
            self.assertEqual(manifest["format"], 1)
            self.assertIn("catalog.sqlite3", [f["name"] for f in manifest["files"]])
            self.assertNotEqual((backup / "catalog.sqlite3"), database)

    def test_v2_to_v3_migration_creates_pre_migration_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_file = root / "catalog.sqlite3"
            with WallpaperCatalog(db_file) as catalog:
                original_id = catalog.create_collection("Keep This").id
            with closing(sqlite3.connect(db_file)) as db:
                db.execute("DROP TABLE collection_exclusions")
                db.execute("PRAGMA user_version = 2")
                db.commit()
            with WallpaperCatalog(db_file) as catalog:
                self.assertEqual(catalog.list_collections()[0].id, original_id)
                self.assertEqual(catalog._db.execute("PRAGMA user_version").fetchone()[0], 3)
            backups = list((root / "backups").glob("backup-*/catalog.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as previous:
                self.assertEqual(previous.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(previous.execute("SELECT id FROM collections").fetchone()[0], original_id)

    def test_unreadable_catalogue_never_publishes_partial_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "catalog.sqlite3").write_bytes(b"not a SQLite database")
            with self.assertRaises(sqlite3.DatabaseError):
                create_backup(root=root, include_qsettings=False)
            self.assertEqual(list((root / "backups").iterdir()), [])

    def test_dispatch_backup_is_explicit(self):
        with patch("wallpaper_orchestrator.state_backup.create_backup") as create:
            self.assertEqual(desktop_entry.main(["--backup-user-data"]), 0)
            create.assert_called_once_with()


class ReleaseContractTests(unittest.TestCase):
    def test_version_sources_and_release_tag_match(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from check_version import check_version
        self.assertEqual(check_version(ROOT, tag="v0.8.0b3"), "0.8.0b3")
        with self.assertRaises(ValueError):
            check_version(ROOT, tag="v0.8.0b2")

    def test_tag_release_is_gated_on_successful_windows_build(self):
        workflow = (ROOT / ".github/workflows/windows-portable.yml").read_text()
        self.assertIn("needs: windows-package", workflow)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v')", workflow)
        self.assertIn("--prerelease", workflow)
        self.assertIn("--verify-tag", workflow)
        self.assertIn("SHA256SUMS", workflow)
        self.assertIn("constraints/windows-ci.txt", workflow)

    def test_installer_preserves_user_data_and_checks_backup_first(self):
        installer = (ROOT / "installer/WallpaperOrchestrator.iss").read_text()
        self.assertIn("PrivilegesRequired=lowest", installer)
        self.assertIn("--stop-agent", installer)
        self.assertIn("--quit-gui", installer)
        self.assertIn("--backup-user-data", installer)
        self.assertIn("--repair-startup", installer)
        self.assertLess(installer.index("'--backup-user-data'"),
                        installer.index("'--repair-startup'"))
        self.assertNotIn("[UninstallDelete]", installer)
        build = (ROOT / "scripts/build_windows.ps1").read_text()
        self.assertIn("--file-version=$windowsVersion", build)
        self.assertIn("--product-version=$windowsVersion", build)


class DiagnosticsTests(unittest.TestCase):
    def test_sensitive_paths_and_credentials_are_redacted(self):
        text = r"Failure at C:\Users\private\images\Wallpaper.jpg" + "\n" + "Authorization: Bearer secret123\n"
        cleaned = redact(text)
        self.assertNotIn("private", cleaned)
        self.assertNotIn("Wallpaper.jpg", cleaned)
        self.assertNotIn("secret123", cleaned)
        self.assertIn("<local path>", cleaned)

    def test_support_bundle_never_includes_catalogue_or_wallpapers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "background-launch.log").write_text(
                "at C:\\Users\\private\\Documents\\image.jpg\n", encoding="utf-8")
            (root / "profiles.json").write_text("PRIVATE SETTINGS", encoding="utf-8")
            (root / "catalog.sqlite3").write_bytes(b"PRIVATE DATABASE")
            (root / "wallpaper.jpg").write_bytes(b"PRIVATE WALLPAPER")
            output = root / "out.zip"
            export_diagnostics(output, root=root)
            with zipfile.ZipFile(output) as bundle:
                self.assertEqual(set(bundle.namelist()),
                                 {"summary.json", "logs/background-launch.log"})
                self.assertNotIn("private", bundle.read("logs/background-launch.log").decode())


if __name__ == "__main__":
    unittest.main()
