import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image


MODULE_PATH = Path(__file__).parents[1] / "!GUI_mtg_deck.py"
SPEC = importlib.util.spec_from_file_location("gui_mtg_deck", MODULE_PATH)
gui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gui)


class GuiHelperTests(unittest.TestCase):
    def test_dropped_art_auto_confirms_when_always_accept_is_enabled(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            image_path = Path(temp_dir) / "replacement.png"
            Image.new("RGB", (5, 7), "blue").save(image_path)
            artwork = object.__new__(gui.TokenArtworkWindow)
            artwork._win = Mock()
            artwork._win.tk.splitlist.return_value = [str(image_path)]
            artwork._set_candidate = Mock(return_value=True)
            artwork._always_accept = Mock()
            artwork._always_accept.get.return_value = True
            artwork._confirm = Mock()
            event = Mock(data=str(image_path))

            result = artwork._on_drop(event)

            self.assertEqual(result, "break")
            artwork._set_candidate.assert_called_once_with(
                str(image_path.resolve()), "Dropped image"
            )
            artwork._confirm.assert_called_once_with()

    def test_workspace_cleanup_clears_runtime_files_and_keeps_placeholders(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            root = Path(temp_dir)
            for folder_name in gui.WORKSPACE_CLEAN_FOLDERS:
                folder = root / "game" / folder_name
                folder.mkdir(parents=True)
                (folder / "working-file.png").write_bytes(b"data")
                nested = folder / "nested"
                nested.mkdir()
                (nested / "nested-file.txt").write_text("data")
                (folder / "README.md").write_text("keep")
            decklist = root / "game" / "decklist"
            (decklist / "EMPTY.md").write_text("keep")

            removed = gui._clean_workspace(str(root))

            self.assertEqual(
                removed,
                {folder_name: 2 for folder_name in gui.WORKSPACE_CLEAN_FOLDERS},
            )
            for folder_name in gui.WORKSPACE_CLEAN_FOLDERS:
                folder = root / "game" / folder_name
                self.assertTrue((folder / "README.md").exists())
                self.assertFalse((folder / "working-file.png").exists())
                self.assertFalse((folder / "nested").exists())
            self.assertTrue((decklist / "EMPTY.md").exists())

    def test_double_faced_fetch_line_is_parsed_into_download_stems(self):
        card = gui._parse_double_faced_card_line(
            "Index: 5, quantity: 1, set code: znr, collector number: 12, "
            "name: Emeria's Call // Emeria, Shattered Skyclave\n"
        )

        self.assertEqual(
            card,
            {
                "index": 5,
                "quantity": 1,
                "name": "Emeria's Call // Emeria, Shattered Skyclave",
            },
        )
        self.assertEqual(
            gui._double_faced_card_stems(card),
            {"5EmeriasCallEmeriaShatteredSkyclave1"},
        )
        self.assertIsNone(
            gui._parse_double_faced_card_line(
                "Index: 6, quantity: 1, name: Lightning Bolt"
            )
        )

    def test_print_preview_uses_eight_cards_and_pdf_order(self):
        self.assertEqual(gui.PrintPreviewWindow.PAGE_SIZE, 8)
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            root = Path(temp_dir)
            front = root / "front"
            double_sided = root / "double_sided"
            front.mkdir()
            double_sided.mkdir()
            for filename in (
                "10Third1.png",
                "2Second1.png",
                "1First1.png",
                "3DoubleSided1.png",
            ):
                Image.new("RGB", (5, 7), "blue").save(front / filename)
            Image.new("RGB", (5, 7), "red").save(
                double_sided / "3DoubleSided1.png"
            )

            ordered = gui._print_order_image_paths(
                str(front), str(double_sided)
            )

            self.assertEqual(
                [Path(path).name for path in ordered],
                [
                    "1First1.png",
                    "2Second1.png",
                    "10Third1.png",
                    "3DoubleSided1.png",
                ],
            )

    def test_deleting_preview_card_also_deletes_matching_back(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            root = Path(temp_dir)
            front = root / "7TransformCard1.png"
            double_sided = root / "double_sided"
            double_sided.mkdir()
            back = double_sided / "7TransformCard1.jpg"
            Image.new("RGB", (5, 7), "blue").save(front)
            Image.new("RGB", (5, 7), "red").save(back)

            errors = gui._delete_print_card(str(front), str(double_sided))

            self.assertEqual(errors, [])
            self.assertFalse(front.exists())
            self.assertFalse(back.exists())

    def test_orphaned_backs_are_removed_before_pdf_creation(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            root = Path(temp_dir)
            front = root / "front"
            double_sided = root / "double_sided"
            front.mkdir()
            double_sided.mkdir()
            (front / "3Transform1.png").write_bytes(b"front")
            (double_sided / "3Transform1.png").write_bytes(b"matched back")
            (double_sided / "50IncubatorPhyrexian_token1.png").write_bytes(
                b"orphaned back"
            )
            (double_sided / "README.md").write_text("keep")

            removed = gui._remove_orphaned_backs(str(front), str(double_sided))

            self.assertEqual(
                [Path(path).name for path in removed],
                ["50IncubatorPhyrexian_token1.png"],
            )
            self.assertTrue((double_sided / "3Transform1.png").exists())
            self.assertTrue((double_sided / "README.md").exists())

    def test_selector_navigation_stays_inside_available_pages(self):
        selector = object.__new__(gui.TokenReviewWindow)
        selector._page = 0
        selector._images = ["card"] * (gui.TokenReviewWindow.PAGE_SIZE + 1)
        selector._render_page = Mock()
        selector._build_bottom_buttons = Mock()

        selector._prev_page()
        self.assertEqual(selector._page, 0)
        selector._render_page.assert_not_called()

        selector._next_page()
        self.assertEqual(selector._page, 1)
        selector._next_page()
        self.assertEqual(selector._page, 1)

        selector._prev_page()
        self.assertEqual(selector._page, 0)

    def test_token_name_and_scryfall_search_url(self):
        path = "12CatWarrior_token1.png"

        self.assertEqual(gui._card_name_from_path(path), "Cat Warrior")
        self.assertEqual(
            gui._token_search_url(path),
            "https://scryfall.com/search?as=grid&order=name&"
            "q=Cat+Warrior+type%3Atoken",
        )

    def test_card_name_and_scryfall_prints_search_url(self):
        path = "1003SolRing1.png"

        self.assertEqual(gui._card_name_from_path(path), "Sol Ring")
        self.assertEqual(
            gui._card_search_url(path),
            "https://scryfall.com/search?as=grid&order=released&unique=prints"
            "&q=Sol+Ring",
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

    def test_shift_existing_card_indices_moves_files_out_of_fetch_range(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent) as temp_dir:
            root = Path(temp_dir)
            front = root / "front"
            double_sided = root / "double_sided"
            front.mkdir()
            double_sided.mkdir()
            (front / "1SolRing1.png").write_bytes(b"front")
            (front / "12Squirrel_token1.png").write_bytes(b"token")
            (front / "3Transform1.png").write_bytes(b"dfc-front")
            (double_sided / "3Transform1.png").write_bytes(b"dfc-back")

            renamed = gui._shift_existing_card_indices(
                str(front), str(double_sided)
            )

            self.assertEqual(renamed, 4)
            self.assertEqual(
                sorted(path.name for path in front.iterdir()),
                ["1001SolRing1.png", "1003Transform1.png", "1012Squirrel_token1.png"],
            )
            # Fronts and backs keep matching stems after the shift.
            self.assertEqual(
                [path.name for path in double_sided.iterdir()],
                ["1003Transform1.png"],
            )

            # A second sideload shifts into the next free thousand-range.
            (front / "1NewCard1.png").write_bytes(b"new")
            gui._shift_existing_card_indices(str(front), str(double_sided))
            self.assertIn("2001NewCard1.png", {p.name for p in front.iterdir()})
            self.assertIn("3012Squirrel_token1.png", {p.name for p in front.iterdir()})

    def test_sideload_button_stores_url_and_resumes_workflow(self):
        preview = object.__new__(gui.PrintPreviewWindow)
        preview._parent = Mock()
        preview._win = Mock()
        preview._done_event = Mock()
        preview._marked = set()
        preview._ask_sideload_url = Mock(
            return_value="https://www.moxfield.com/decks/abc"
        )

        preview._on_sideload()

        self.assertEqual(
            preview._parent._pending_sideload_url,
            "https://www.moxfield.com/decks/abc",
        )
        preview._win.destroy.assert_called_once_with()
        preview._done_event.set.assert_called_once_with()

    def test_sideload_cancel_keeps_preview_open(self):
        preview = object.__new__(gui.PrintPreviewWindow)
        preview._parent = Mock()
        preview._parent._pending_sideload_url = None
        preview._win = Mock()
        preview._done_event = Mock()
        preview._marked = set()
        preview._ask_sideload_url = Mock(return_value=None)

        preview._on_sideload()

        self.assertIsNone(preview._parent._pending_sideload_url)
        preview._win.destroy.assert_not_called()
        preview._done_event.set.assert_not_called()

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
