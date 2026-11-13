"""Compatibility contract between B1 catalogue and later feature modules."""

import unittest

from wallpaper_orchestrator.catalog import WallpaperCatalog


class CatalogueContractTests(unittest.TestCase):
    def test_required_indexer_and_library_methods_exist(self):
        required = (
            "create_collection", "list_collections", "list_folder_links",
            "get_folder_link", "find_image_by_path", "list_folder_images",
            "apply_folder_scan", "list_collection_images", "list_image_tags",
            "rename_tag", "delete_tag", "set_monitor_collections",
        )
        missing = [method for method in required
                   if not callable(getattr(WallpaperCatalog, method, None))]
        self.assertEqual(missing, [], f"Incomplete catalogue API: {missing}")


if __name__ == "__main__":
    unittest.main()
