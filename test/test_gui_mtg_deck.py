import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


MODULE_PATH = Path(__file__).parents[1] / "!GUI_mtg_deck.py"
SPEC = importlib.util.spec_from_file_location("gui_mtg_deck", MODULE_PATH)
gui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gui)


class GuiHelperTests(unittest.TestCase):
    def test_token_name_and_scryfall_search_url(self):
        path = "12CatWarrior_token1.png"

        self.assertEqual(gui._token_name_from_path(path), "Cat Warrior")
        self.assertEqual(
            gui._token_search_url(path),
            "https://scryfall.com/search?as=grid&order=name&"
            "q=Cat+Warrior+type%3Atoken",
        )

    def test_list_download_pngs_only_checks_direct_children(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            root = Path(temp_dir)
            direct_png = root / "art.PNG"
            direct_png.write_bytes(b"png")
            (root / "ignore.jpg").write_bytes(b"jpg")
            nested = root / "nested"
            nested.mkdir()
            (nested / "other.png").write_bytes(b"png")

            self.assertEqual(
                gui._list_download_pngs(str(root)),
                [str(direct_png.resolve())],
            )

    def test_replace_image_file_converts_to_target_format(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            root = Path(temp_dir)
            source = root / "new-art.jpg"
            target = root / "3Goblin_token1.png"
            Image.new("RGB", (20, 30), "red").save(source)
            Image.new("RGBA", (5, 5), "blue").save(target)

            gui._replace_image_file(str(source), str(target))

            with Image.open(target) as replaced:
                self.assertEqual(replaced.format, "PNG")
                self.assertEqual(replaced.size, (20, 30))
                self.assertEqual(
                    replaced.convert("RGB").getpixel((0, 0)),
                    (254, 0, 0),
                )

    def test_move_download_to_cleanup_avoids_overwriting(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            root = Path(temp_dir)
            cleanup = root / "cleanup"
            cleanup.mkdir()
            (cleanup / "token.png").write_bytes(b"old")
            download = root / "token.png"
            download.write_bytes(b"new")

            with patch.object(gui, "CLEANUP_DIR", str(cleanup)):
                moved_to = gui._move_download_to_cleanup(str(download))

            self.assertEqual(Path(moved_to).name, "token_2.png")
            self.assertEqual(Path(moved_to).read_bytes(), b"new")
            self.assertFalse(download.exists())


if __name__ == "__main__":
    unittest.main()
