import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, scrolledtext
from urllib.parse import quote_plus

from natsort import natsorted
from PIL import Image, ImageOps, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # The button fallback still works if the optional package is absent.
    DND_FILES = None
    TkinterDnD = None


REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
OUTPUT_PDF = os.path.join(REPO_ROOT, "game", "output", "game.pdf")
CLEANUP_DIR = os.path.join(REPO_ROOT, "z_deletes_fromCardmaker")
BASIC_LAND_NAMES = ("Mountain", "Island", "Forest", "Swamp", "Plains")
STARTUP_CLEAN_FOLDERS = ("back", "decklist", "double_sided", "front")
WORKSPACE_PLACEHOLDERS = {"README.md", "EMPTY.md"}
IMAGE_EXTENSIONS = {
    ".png", ".apng", ".jpg", ".jpeg", ".jpx", ".jp2",
    ".gif", ".webp", ".tif", ".tiff", ".bmp",
}
WORKFLOW_STEPS = (
    ("1. Choose a routine", "Paste a Moxfield URL and select one of the three workflow buttons."),
    ("2. Download cards", "Clear old workspace files, fetch the deck and tokens, then remove basic lands."),
    ("3. Filter double faces", "Cards whose names contain // are shown briefly, then excluded."),
    ("4. Select tokens", "Click to deselect tokens. Token-art mode also enables right-click replacement."),
    ("5. Select main cards", "Review the remaining cards and deselect anything you do not want."),
    ("6. Optional review", "Pause for manual artwork changes or open the eight-card print preview."),
    ("7. Create the PDF", "Build the A4 print-ready PDF and open it in the default viewer."),
)
TkRoot = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk

# ─── Workflow overview ───────────────────────────────────────────────────────
# 1. User enters a Moxfield deck URL and clicks "Run Workflow".
# 2. Existing working files in game/back/, decklist/, double_sided/, and front/
#    are deleted at startup. Tracked README/EMPTY placeholders are preserved.
# 3. plugins/mtg/fetch.py downloads every card image (including tokens)
#    into game/front/. All copies of Mountain, Island, Forest, Swamp, and
#    Plains are then removed automatically and the counts are logged.
# 4. Execution PAUSES — a Token Review window opens.  It scans game/front/
#    for any image whose filename contains "token" and shows thumbnails in
#    an 8-column × 3-row grid (24 cards per page).  Clicking a card toggles
#    a red ✕ overlay.  If more than 24 token images exist, a "Next page"
#    button appears at the bottom.  "Delete all ✕" removes every marked
#    image from disk. "Continue" also deletes any marked images, then closes
#    the window and resumes the workflow. The token-artwork routine also lets
#    the user right-click a token to preview and confirm replacement artwork.
# 5. A second review window shows every non-token card with the same paging,
#    marking, deletion, and Continue controls.
# 6. When "Change Artwork before .pdf" was selected, execution PAUSES again
#    so the user can replace artwork in game/front/. The normal workflow skips
#    this pause.
# 7. create_pdf.py lays out all remaining images in game/front/ into an A4
#    PDF saved to game/output/game.pdf.
# 8. The finished PDF is opened with the system default viewer.
# ─────────────────────────────────────────────────────────────────────────────


def _clean_folder(folder: str) -> int:
    """Recursively delete working files while retaining tracked placeholders."""
    removed = 0
    errors = []
    os.makedirs(folder, exist_ok=True)
    for current_folder, directories, filenames in os.walk(folder, topdown=False):
        for name in filenames:
            path = os.path.join(current_folder, name)
            is_root_placeholder = (
                os.path.abspath(current_folder) == os.path.abspath(folder)
                and name in WORKSPACE_PLACEHOLDERS
            )
            if is_root_placeholder:
                continue
            try:
                os.remove(path)
                removed += 1
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        for name in directories:
            path = os.path.join(current_folder, name)
            try:
                os.rmdir(path)
            except OSError:
                # Non-empty directories are harmless; any file deletion errors
                # inside them are reported below.
                pass
    if errors:
        raise RuntimeError("Could not clean every workspace file:\n" + "\n".join(errors))
    return removed


def _clean_startup_workspace(repo_root: str) -> dict:
    game_folder = os.path.join(repo_root, "game")
    return {
        folder_name: _clean_folder(os.path.join(game_folder, folder_name))
        for folder_name in STARTUP_CLEAN_FOLDERS
    }


def _remove_basic_lands(folder: str) -> dict:
    """Remove every downloaded copy of the five basic lands."""
    removed = {land: 0 for land in BASIC_LAND_NAMES}
    patterns = {
        land: re.compile(rf"^\d+{land}\d+\.[^.]+$", re.IGNORECASE)
        for land in BASIC_LAND_NAMES
    }
    errors = []

    try:
        filenames = os.listdir(folder)
    except OSError as exc:
        raise RuntimeError(f"Cannot scan basic lands in {folder}: {exc}") from exc

    for filename in filenames:
        land = next(
            (name for name, pattern in patterns.items() if pattern.fullmatch(filename)),
            None,
        )
        if land is None:
            continue

        path = os.path.join(folder, filename)
        if not os.path.isfile(path):
            continue

        try:
            os.remove(path)
        except OSError as exc:
            errors.append(f"{filename}: {exc}")
        else:
            removed[land] += 1

    if errors:
        raise RuntimeError("Failed to remove basic lands:\n" + "\n".join(errors))

    return removed


def script_python():
    executable = sys.executable
    if sys.platform.startswith("win") and executable.lower().endswith("pythonw.exe"):
        python_exe = os.path.join(os.path.dirname(executable), "python.exe")
        if os.path.isfile(python_exe):
            return python_exe
    return executable


def _downloads_folder() -> str:
    """Return the user's Downloads folder, including relocated Windows folders."""
    if sys.platform.startswith("win"):
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            downloads_guid = "{374DE290-123F-4565-9164-39C4925E467B}"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, downloads_guid)
            return os.path.abspath(os.path.expandvars(value))
        except (OSError, ImportError):
            pass
    return os.path.abspath(os.path.join(os.path.expanduser("~"), "Downloads"))


def _list_download_pngs(folder: str) -> list:
    """Return direct-child PNG files as absolute path strings."""
    try:
        return sorted(
            os.path.abspath(os.path.join(folder, name))
            for name in os.listdir(folder)
            if name.lower().endswith(".png")
            and os.path.isfile(os.path.join(folder, name))
        )
    except OSError:
        return []


def _token_name_from_path(path: str) -> str:
    """Turn a fetched filename such as ``6CatWarrior_token1.png`` into a name."""
    stem = os.path.splitext(os.path.basename(path))[0]
    match = re.fullmatch(r"\d+(.+)_token\d+", stem, flags=re.IGNORECASE)
    compact_name = match.group(1) if match else stem
    compact_name = re.sub(r"_token\d*$", "", compact_name, flags=re.IGNORECASE)
    compact_name = compact_name.replace("_", " ")
    compact_name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", compact_name)
    return compact_name.strip() or stem


def _token_search_url(path: str) -> str:
    query = quote_plus(f"{_token_name_from_path(path)} type:token")
    return f"https://scryfall.com/search?as=grid&order=name&q={query}"


def _replace_image_file(source: str, target: str) -> None:
    """Convert *source* to the target's image format and atomically replace it."""
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target)):
        raise ValueError("Choose a different image from the original token artwork.")

    target_extension = os.path.splitext(target)[1].lower()
    image_format = Image.registered_extensions().get(target_extension)
    if image_format is None:
        raise ValueError(f"Unsupported target image format: {target_extension}")

    fd, temporary_path = tempfile.mkstemp(
        prefix=".token-artwork-",
        suffix=target_extension,
        dir=os.path.dirname(target),
    )
    os.close(fd)
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            if image_format == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(temporary_path, format=image_format)
        os.replace(temporary_path, target)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def _move_download_to_cleanup(path: str) -> str:
    """Move a used download into the project cleanup folder without overwriting."""
    os.makedirs(CLEANUP_DIR, exist_ok=True)
    stem, extension = os.path.splitext(os.path.basename(path))
    destination = os.path.join(CLEANUP_DIR, f"{stem}{extension}")
    counter = 2
    while os.path.exists(destination):
        destination = os.path.join(CLEANUP_DIR, f"{stem}_{counter}{extension}")
        counter += 1
    return shutil.move(path, destination)


def _print_order_image_paths(front_folder: str, double_sided_folder: str) -> list:
    """Return front images in the same single-sided/DFC natural order as the PDF."""
    def relative_images(folder: str) -> list:
        images = []
        try:
            for current_folder, _, filenames in os.walk(folder):
                for filename in filenames:
                    if os.path.splitext(filename)[1].lower() not in IMAGE_EXTENSIONS:
                        continue
                    full_path = os.path.join(current_folder, filename)
                    if os.path.isfile(full_path):
                        images.append(os.path.relpath(full_path, folder))
        except OSError:
            return []
        return images

    front_images = relative_images(front_folder)
    double_sided_stems = {
        os.path.splitext(os.path.basename(path))[0]
        for path in relative_images(double_sided_folder)
    }
    single_sided = [
        path for path in front_images
        if os.path.splitext(os.path.basename(path))[0] not in double_sided_stems
    ]
    double_sided = [
        path for path in front_images
        if os.path.splitext(os.path.basename(path))[0] in double_sided_stems
    ]
    ordered = natsorted(single_sided) + natsorted(double_sided)
    return [os.path.join(front_folder, path) for path in ordered]


def _delete_print_card(front_path: str, double_sided_folder: str) -> list:
    """Delete a selected front and any matching double-sided back images."""
    stem = os.path.splitext(os.path.basename(front_path))[0]
    back_paths = []
    try:
        for current_folder, _, filenames in os.walk(double_sided_folder):
            for filename in filenames:
                if os.path.splitext(filename)[0] == stem:
                    back_paths.append(os.path.join(current_folder, filename))
    except OSError:
        pass

    errors = []
    # Remove backs first. If a back is locked, retain the front so the user can
    # retry without leaving an unmatched back that would break PDF validation.
    for path in back_paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"{os.path.basename(path)}: {exc}")
    if errors:
        return errors

    try:
        os.remove(front_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        errors.append(f"{os.path.basename(front_path)}: {exc}")
    return errors


def _parse_double_faced_card_line(line: str):
    """Parse a fetch log entry for a card whose deck name contains ``//``."""
    match = re.match(
        r"^Index:\s*(\d+),\s*quantity:\s*(\d+).*?,\s*name:\s*(.+?)\s*$",
        line.strip(),
    )
    if match is None or "//" not in match.group(3):
        return None
    return {
        "index": int(match.group(1)),
        "quantity": int(match.group(2)),
        "name": match.group(3),
    }


def _double_faced_card_stems(card: dict) -> set:
    clean_name = re.sub(r"[^\w]", "", card["name"])
    return {
        f'{card["index"]}{clean_name}{copy_number}'
        for copy_number in range(1, card["quantity"] + 1)
    }


def _find_images_with_stems(folder: str, stems: set) -> list:
    matches = []
    try:
        for current_folder, _, filenames in os.walk(folder):
            for filename in filenames:
                if os.path.splitext(filename)[0] in stems:
                    path = os.path.join(current_folder, filename)
                    if os.path.isfile(path):
                        matches.append(path)
    except OSError:
        return []
    return natsorted(matches)


class DoubleFacedRemovalWindow:
    """Show a downloaded double-faced card briefly, then remove both faces."""

    AUTO_CLOSE_SECONDS = 10
    PREVIEW_WIDTH = 300
    PREVIEW_HEIGHT = 420

    def __init__(
        self,
        parent: "MtgDeckGui",
        card: dict,
        front_paths: list,
        back_paths: list,
        done_event: threading.Event,
    ):
        self._parent = parent
        self._card = card
        self._done_event = done_event
        self._paths = list(dict.fromkeys(front_paths + back_paths))
        self._seconds_remaining = self.AUTO_CLOSE_SECONDS
        self._timer_job = None
        self._preview_ref = None

        win = tk.Toplevel(parent)
        self._win = win
        win.title("Double-faced card")
        win.configure(bg=parent.colors["bg"])
        width, height = 420, 600
        x = max(0, (parent.winfo_screenwidth() - width) // 2)
        y = max(0, (parent.winfo_screenheight() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.resizable(False, False)
        win.transient(parent)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._remove_and_close)

        tk.Label(
            win,
            text="Double faced cards will be removed",
            bg=parent.colors["bg"],
            fg=parent.colors["error"],
            font=("Segoe UI", 13, "bold"),
        ).pack(padx=16, pady=(16, 4))
        tk.Label(
            win,
            text=card["name"],
            bg=parent.colors["bg"],
            fg=parent.colors["text"],
            font=("Segoe UI", 9),
            wraplength=380,
        ).pack(padx=16, pady=(0, 10))

        preview_frame = tk.Frame(
            win,
            width=self.PREVIEW_WIDTH,
            height=self.PREVIEW_HEIGHT,
            bg=parent.colors["field"],
        )
        preview_frame.pack(padx=16)
        preview_frame.pack_propagate(False)
        preview_label = tk.Label(
            preview_frame,
            text="Card image unavailable",
            bg=parent.colors["field"],
            fg=parent.colors["muted"],
            font=("Segoe UI", 10),
        )
        preview_label.pack(fill=tk.BOTH, expand=True)

        preview_path = front_paths[0] if front_paths else (
            back_paths[0] if back_paths else None
        )
        if preview_path:
            try:
                with Image.open(preview_path) as opened:
                    image = ImageOps.exif_transpose(opened)
                    image.thumbnail(
                        (self.PREVIEW_WIDTH - 8, self.PREVIEW_HEIGHT - 8),
                        Image.LANCZOS,
                    )
                    self._preview_ref = ImageTk.PhotoImage(image.copy())
                preview_label.configure(image=self._preview_ref, text="")
            except Exception:
                pass

        self._countdown = tk.StringVar()
        tk.Label(
            win,
            textvariable=self._countdown,
            bg=parent.colors["bg"],
            fg=parent.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(padx=16, pady=8)
        tk.Button(
            win,
            text="Remove now",
            command=self._remove_and_close,
            bg=parent.colors["error"],
            fg="#07111f",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=6,
            cursor="hand2",
        ).pack(pady=(0, 12))

        self._update_countdown()

    def _update_countdown(self):
        if self._seconds_remaining <= 0:
            self._remove_and_close()
            return
        self._countdown.set(
            f"Removing automatically in {self._seconds_remaining} seconds..."
        )
        self._seconds_remaining -= 1
        self._timer_job = self._win.after(1000, self._update_countdown)

    def _remove_and_close(self):
        if self._timer_job is not None:
            try:
                self._win.after_cancel(self._timer_job)
            except tk.TclError:
                pass
            self._timer_job = None

        errors = []
        for path in self._paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
        if errors:
            messagebox.showerror(
                "Could not remove double-faced card",
                "\n".join(errors),
                parent=self._win,
            )
            self._countdown.set("Removal failed. Use Remove now to retry.")
            return

        self._parent.append_log(
            f'Removed double-faced card: {self._card["name"]}\n'
        )
        self._win.grab_release()
        self._win.destroy()
        self._done_event.set()


class TokenReviewWindow:
    """Modal pause window for token or non-token images before PDF creation.

    Shows matching images from *folder* as
    thumbnails in an 8-column × 3-row grid (PAGE_SIZE = 24 per page).
    Clicking a card toggles a red ✕ overlay.  "Delete all ✕" removes marked
    images from disk. "Continue" deletes any remaining marked images, signals
    *done_event*, and closes the window.
    """

    COLS = 8
    ROWS = 3
    PAGE_SIZE = COLS * ROWS  # 24 cards per page
    CARD_W = 80
    CARD_H = 112

    def __init__(
        self,
        parent: "MtgDeckGui",
        folder: str,
        done_event: threading.Event,
        include_tokens: bool = True,
        allow_token_artwork: bool = False,
    ):
        self._parent = parent
        self._folder = folder
        self._done_event = done_event
        self._include_tokens = include_tokens
        self._allow_token_artwork = include_tokens and allow_token_artwork
        self._marked: set = set()
        self._page = 0
        self._thumb_refs: list = []
        self._delete_btn = None

        self._images = self._find_review_images()

        win = tk.Toplevel(parent)
        self._win = win
        if include_tokens:
            win.title(
                f"Selector_Window Token  \u2014  {len(self._images)} token(s) found"
            )
            self._empty_message = "No token images found."
        else:
            win.title(
                f"Selector_Window Main  \u2014  {len(self._images)} card(s) found"
            )
            self._empty_message = "No non-token card images found."
        win.configure(bg=parent.colors["bg"])
        win.resizable(True, True)

        # Size to full screen first; defer zoomed state so the WM has mapped
        # the window before we ask it to maximise.
        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        win.geometry(f"{sw}x{sh}+0+0")
        win.after(0, lambda: win.state("zoomed"))

        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._on_continue)

        # Compute card dimensions to fill the maximised work area.
        # Approximate work area: full screen minus taskbar (~40 px) and UI
        # chrome (title bar ~30 px, button bar ~50 px, pack gaps ~20 px).
        _avail_w = sw - 32          # grid_frame padx=16 each side
        _avail_h = sh - 40 - 100   # taskbar + title bar + btn bar + gaps
        self.CARD_W = max(40, (_avail_w - self.COLS * 8) // self.COLS)
        self.CARD_H = max(56, (_avail_h - self.ROWS * 8) // self.ROWS)

        self._grid_frame = tk.Frame(win, bg=parent.colors["bg"])
        self._grid_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(12, 6))

        self._btn_frame = tk.Frame(win, bg=parent.colors["bg"])
        self._btn_frame.pack(fill=tk.X, padx=16, pady=(0, 10))

        self._render_page()
        self._build_bottom_buttons()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _find_review_images(self) -> list:
        exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        candidates = []
        try:
            for name in sorted(os.listdir(self._folder)):
                is_token = "token" in name.lower()
                if is_token == self._include_tokens and os.path.splitext(name)[1].lower() in exts:
                    candidates.append(os.path.join(self._folder, name))
        except OSError:
            pass

        if not self._include_tokens:
            return candidates

        # Deduplicate: strip any leading digits to get the canonical filename
        # (e.g. "6Squirrel_token1.png" and "32Squirrel_token1.png" both become
        # "Squirrel_token1.png").  Keep the first occurrence in sorted order and
        # delete every subsequent duplicate from disk immediately.
        kept: dict = {}  # canonical_name -> path
        for path in candidates:
            canonical = os.path.basename(path).lstrip("0123456789")
            if canonical not in kept:
                kept[canonical] = path
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass

        return list(kept.values())

    def _draw_x(self, canvas: tk.Canvas) -> tuple:
        """Draw a semi-transparent red ✕ overlay. Returns item IDs for later removal."""
        cw = int(canvas.cget("width"))
        ch = int(canvas.cget("height"))
        rect = canvas.create_rectangle(
            0, 0, cw, ch, fill="#cc0000", stipple="gray50", outline=""
        )
        font_size = max(20, cw // 4)
        text = canvas.create_text(
            cw // 2, ch // 2, text="\u2715",
            fill="white", font=("Segoe UI", font_size, "bold"),
        )
        return (rect, text)

    # ── page rendering ───────────────────────────────────────────────────────

    def _render_page(self):
        for widget in self._grid_frame.winfo_children():
            widget.destroy()
        self._thumb_refs.clear()

        start = self._page * self.PAGE_SIZE
        page_items = self._images[start : start + self.PAGE_SIZE]

        if not page_items:
            tk.Label(
                self._grid_frame,
                text=self._empty_message,
                bg=self._parent.colors["bg"],
                fg=self._parent.colors["muted"],
                font=("Segoe UI", 12),
            ).pack(expand=True)
            return

        for idx, path in enumerate(page_items):
            row, col = divmod(idx, self.COLS)

            canvas = tk.Canvas(
                self._grid_frame,
                width=self.CARD_W,
                height=self.CARD_H,
                bg=self._parent.colors["panel"],
                highlightthickness=2,
                highlightbackground=self._parent.colors["panel"],
                cursor="hand2",
            )
            canvas.grid(row=row, column=col, padx=2, pady=2)

            photo = None
            try:
                with Image.open(path) as opened:
                    img = ImageOps.exif_transpose(opened)
                    img.thumbnail((self.CARD_W, self.CARD_H), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img.copy())
                canvas.create_image(
                    self.CARD_W // 2, self.CARD_H // 2, image=photo, anchor="center"
                )
                self._thumb_refs.append(photo)
            except Exception:
                canvas.create_text(
                    self.CARD_W // 2, self.CARD_H // 2,
                    text=os.path.basename(path),
                    fill=self._parent.colors["muted"],
                    font=("Segoe UI", 7),
                    width=self.CARD_W - 4,
                )

            # Store per-canvas state as attributes
            canvas._path = path  # type: ignore[attr-defined]
            canvas._x_ids = ()   # type: ignore[attr-defined]

            if path in self._marked:
                canvas._x_ids = self._draw_x(canvas)  # type: ignore[attr-defined]
                canvas.configure(highlightbackground=self._parent.colors["error"])

            canvas.bind("<Button-1>", self._on_card_click)
            if self._allow_token_artwork:
                canvas.bind("<Button-3>", self._on_card_right_click)

    # ── interactions ─────────────────────────────────────────────────────────

    def _on_card_click(self, event: tk.Event):  # type: ignore[type-arg]
        canvas: tk.Canvas = event.widget
        path: str = canvas._path  # type: ignore[attr-defined]
        if path in self._marked:
            self._marked.discard(path)
            for item_id in canvas._x_ids:  # type: ignore[attr-defined]
                canvas.delete(item_id)
            canvas._x_ids = ()  # type: ignore[attr-defined]
            canvas.configure(highlightbackground=self._parent.colors["panel"])
        else:
            self._marked.add(path)
            canvas._x_ids = self._draw_x(canvas)  # type: ignore[attr-defined]
            canvas.configure(highlightbackground=self._parent.colors["error"])
        self._update_delete_btn()

    def _on_card_right_click(self, event: tk.Event):  # type: ignore[type-arg]
        canvas: tk.Canvas = event.widget
        TokenArtworkWindow(self, canvas._path)  # type: ignore[attr-defined]

    def _update_delete_btn(self):
        if self._delete_btn is None:
            return
        n = len(self._marked)
        if n:
            self._delete_btn.configure(state=tk.NORMAL, text=f"Delete all \u2715  ({n})")
        else:
            self._delete_btn.configure(state=tk.DISABLED, text="Delete all \u2715")

    def _build_bottom_buttons(self):
        for w in self._btn_frame.winfo_children():
            w.destroy()
        self._delete_btn = None

        total_pages = max(1, -(-len(self._images) // self.PAGE_SIZE))  # ceiling division

        if self._allow_token_artwork:
            tk.Label(
                self._btn_frame,
                text="Right-click a token to change its artwork",
                bg=self._parent.colors["bg"], fg=self._parent.colors["muted"],
                font=("Segoe UI", 9),
            ).pack(side=tk.LEFT, padx=(0, 12))

        tk.Button(
            self._btn_frame, text="\u25c4 Previous page",
            command=self._prev_page,
            bg=self._parent.colors["panel"], fg=self._parent.colors["text"],
            disabledforeground=self._parent.colors["muted"],
            relief=tk.FLAT, font=("Segoe UI", 9), padx=8, pady=5,
            cursor="hand2" if self._page > 0 else "arrow",
            state=tk.NORMAL if self._page > 0 else tk.DISABLED,
        ).pack(side=tk.LEFT)

        tk.Label(
            self._btn_frame,
            text=f"  Current page {self._page + 1}/{total_pages}  ",
            bg=self._parent.colors["bg"], fg=self._parent.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)

        tk.Button(
            self._btn_frame, text="Next page \u25ba",
            command=self._next_page,
            bg=self._parent.colors["panel"], fg=self._parent.colors["text"],
            disabledforeground=self._parent.colors["muted"],
            relief=tk.FLAT, font=("Segoe UI", 9), padx=8, pady=5,
            cursor="hand2" if self._page < total_pages - 1 else "arrow",
            state=tk.NORMAL if self._page < total_pages - 1 else tk.DISABLED,
        ).pack(side=tk.LEFT)

        # Right-side buttons (packed right-to-left)
        tk.Button(
            self._btn_frame, text="Continue \u2192",
            command=self._on_continue,
            bg=self._parent.colors["accent"], fg="#07111f",
            activebackground=self._parent.colors["accent_hover"], activeforeground="#07111f",
            relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=12, pady=5, cursor="hand2",
        ).pack(side=tk.RIGHT)

        if not self._include_tokens:
            tk.Button(
                self._btn_frame,
                text="Continue and show Preview",
                command=self._on_continue_with_preview,
                bg=self._parent.colors["panel"], fg=self._parent.colors["text"],
                activebackground=self._parent.colors["field"],
                activeforeground=self._parent.colors["text"],
                relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
                padx=12, pady=5, cursor="hand2",
            ).pack(side=tk.RIGHT, padx=(0, 6))

        self._delete_btn = tk.Button(
            self._btn_frame, text="Delete all \u2715",
            command=self._delete_marked,
            bg=self._parent.colors["error"], fg="#07111f",
            relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=10, pady=5, cursor="hand2",
            state=tk.DISABLED,
        )
        self._delete_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self._update_delete_btn()

    def _prev_page(self):
        if self._page <= 0:
            return
        self._page -= 1
        self._render_page()
        self._build_bottom_buttons()

    def _next_page(self):
        total_pages = max(1, -(-len(self._images) // self.PAGE_SIZE))
        if self._page >= total_pages - 1:
            return
        self._page += 1
        self._render_page()
        self._build_bottom_buttons()

    def _delete_marked(self):
        errors = []
        for path in list(self._marked):
            try:
                os.remove(path)
            except OSError as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
            else:
                self._marked.discard(path)
                self._images = [p for p in self._images if p != path]

        if errors:
            messagebox.showerror("Delete failed", "\n".join(errors), parent=self._win)

        total_pages = max(1, -(-len(self._images) // self.PAGE_SIZE))
        if self._page >= total_pages:
            self._page = total_pages - 1

        self._render_page()
        self._build_bottom_buttons()
        return not errors

    def _on_continue(self):
        # Apply any pending red-X selections before continuing. This lets the
        # user mark images and continue without clicking the separate delete
        # button first.
        if self._marked and not self._delete_marked():
            return

        self._win.grab_release()
        self._win.destroy()
        self._done_event.set()

    def _on_continue_with_preview(self):
        if self._include_tokens:
            return
        if self._marked and not self._delete_marked():
            return

        self._win.grab_release()
        self._win.destroy()
        PrintPreviewWindow(
            self._parent,
            self._folder,
            os.path.join(REPO_ROOT, "game", "double_sided"),
            self._done_event,
        )


class PrintPreviewWindow:
    """Read-only, eight-cards-per-page preview in final PDF print order."""

    COLS = 4
    ROWS = 2
    PAGE_SIZE = COLS * ROWS

    def __init__(
        self,
        parent: "MtgDeckGui",
        front_folder: str,
        double_sided_folder: str,
        done_event: threading.Event,
    ):
        self._parent = parent
        self._done_event = done_event
        self._front_folder = front_folder
        self._double_sided_folder = double_sided_folder
        self._images = _print_order_image_paths(
            front_folder, double_sided_folder
        )
        self._page = 0
        self._marked = set()
        self._thumb_refs = []
        self._reload_button = None

        win = tk.Toplevel(parent)
        self._win = win
        win.title(f"Print Preview  \u2014  {len(self._images)} card(s)")
        win.configure(bg=parent.colors["bg"])
        win.resizable(True, True)

        screen_width = parent.winfo_screenwidth()
        screen_height = parent.winfo_screenheight()
        win.geometry(f"{screen_width}x{screen_height}+0+0")
        win.after(0, lambda: win.state("zoomed"))
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._on_continue)

        available_width = screen_width - 32
        available_height = screen_height - 40 - 110
        self._card_width = max(
            80, (available_width - self.COLS * 8) // self.COLS
        )
        self._card_height = max(
            112, (available_height - self.ROWS * 8) // self.ROWS
        )

        self._grid_frame = tk.Frame(win, bg=parent.colors["bg"])
        self._grid_frame.pack(
            fill=tk.BOTH, expand=True, padx=16, pady=(12, 6)
        )
        self._button_frame = tk.Frame(win, bg=parent.colors["bg"])
        self._button_frame.pack(fill=tk.X, padx=16, pady=(0, 10))

        self._render_page()
        self._build_buttons()

    def _render_page(self):
        for widget in self._grid_frame.winfo_children():
            widget.destroy()
        self._thumb_refs.clear()

        start = self._page * self.PAGE_SIZE
        page_images = self._images[start:start + self.PAGE_SIZE]
        if not page_images:
            tk.Label(
                self._grid_frame,
                text="No card images remain to preview.",
                bg=self._parent.colors["bg"],
                fg=self._parent.colors["muted"],
                font=("Segoe UI", 12),
            ).pack(expand=True)
            return

        for index, path in enumerate(page_images):
            row, column = divmod(index, self.COLS)
            canvas = tk.Canvas(
                self._grid_frame,
                width=self._card_width,
                height=self._card_height,
                bg=self._parent.colors["panel"],
                highlightthickness=2,
                highlightbackground=self._parent.colors["panel"],
                cursor="hand2",
            )
            canvas.grid(row=row, column=column, padx=2, pady=2)

            try:
                with Image.open(path) as opened:
                    image = ImageOps.exif_transpose(opened)
                    image.thumbnail(
                        (self._card_width, self._card_height),
                        Image.LANCZOS,
                    )
                    photo = ImageTk.PhotoImage(image.copy())
                canvas.create_image(
                    self._card_width // 2,
                    self._card_height // 2,
                    image=photo,
                    anchor="center",
                )
                self._thumb_refs.append(photo)
            except Exception:
                canvas.create_text(
                    self._card_width // 2,
                    self._card_height // 2,
                    text=os.path.basename(path),
                    fill=self._parent.colors["muted"],
                    font=("Segoe UI", 9),
                    width=self._card_width - 20,
                )

            print_position = start + index + 1
            canvas.create_rectangle(
                7, 7, 58, 34,
                fill=self._parent.colors["field"], outline="",
            )
            canvas.create_text(
                32, 20,
                text=f"#{print_position}",
                fill=self._parent.colors["text"],
                font=("Segoe UI", 10, "bold"),
            )
            canvas._path = path  # type: ignore[attr-defined]
            canvas._x_ids = ()  # type: ignore[attr-defined]
            if path in self._marked:
                canvas._x_ids = self._draw_x(canvas)  # type: ignore[attr-defined]
                canvas.configure(
                    highlightbackground=self._parent.colors["error"]
                )
            canvas.bind("<Button-1>", self._on_card_click)

    def _draw_x(self, canvas: tk.Canvas) -> tuple:
        width = int(canvas.cget("width"))
        height = int(canvas.cget("height"))
        rectangle = canvas.create_rectangle(
            0, 0, width, height,
            fill="#cc0000", stipple="gray50", outline="",
        )
        cross = canvas.create_text(
            width // 2,
            height // 2,
            text="\u2715",
            fill="white",
            font=("Segoe UI", max(24, width // 5), "bold"),
        )
        return rectangle, cross

    def _on_card_click(self, event: tk.Event):  # type: ignore[type-arg]
        canvas: tk.Canvas = event.widget
        path = canvas._path  # type: ignore[attr-defined]
        if path in self._marked:
            self._marked.discard(path)
            for item_id in canvas._x_ids:  # type: ignore[attr-defined]
                canvas.delete(item_id)
            canvas._x_ids = ()  # type: ignore[attr-defined]
            canvas.configure(
                highlightbackground=self._parent.colors["panel"]
            )
        else:
            self._marked.add(path)
            canvas._x_ids = self._draw_x(canvas)  # type: ignore[attr-defined]
            canvas.configure(
                highlightbackground=self._parent.colors["error"]
            )
        self._update_reload_button()

    def _build_buttons(self):
        for widget in self._button_frame.winfo_children():
            widget.destroy()
        self._reload_button = None

        total_pages = max(1, -(-len(self._images) // self.PAGE_SIZE))
        tk.Button(
            self._button_frame,
            text="\u25c4 Previous page",
            command=self._previous_page,
            bg=self._parent.colors["panel"],
            fg=self._parent.colors["text"],
            disabledforeground=self._parent.colors["muted"],
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            padx=8,
            pady=5,
            cursor="hand2" if self._page > 0 else "arrow",
            state=tk.NORMAL if self._page > 0 else tk.DISABLED,
        ).pack(side=tk.LEFT)
        tk.Label(
            self._button_frame,
            text=f"  Current page {self._page + 1}/{total_pages}  ",
            bg=self._parent.colors["bg"],
            fg=self._parent.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT)
        tk.Button(
            self._button_frame,
            text="Next page \u25ba",
            command=self._next_page,
            bg=self._parent.colors["panel"],
            fg=self._parent.colors["text"],
            disabledforeground=self._parent.colors["muted"],
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            padx=8,
            pady=5,
            cursor="hand2" if self._page < total_pages - 1 else "arrow",
            state=tk.NORMAL if self._page < total_pages - 1 else tk.DISABLED,
        ).pack(side=tk.LEFT)
        tk.Label(
            self._button_frame,
            text="Click a card to toggle deselection",
            bg=self._parent.colors["bg"],
            fg=self._parent.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(14, 0))
        tk.Button(
            self._button_frame,
            text="Continue to PDF \u2192",
            command=self._on_continue,
            bg=self._parent.colors["accent"],
            fg="#07111f",
            activebackground=self._parent.colors["accent_hover"],
            activeforeground="#07111f",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
            cursor="hand2",
        ).pack(side=tk.RIGHT)
        self._reload_button = tk.Button(
            self._button_frame,
            text="Reload Preview",
            command=self._reload_preview,
            bg=self._parent.colors["panel"],
            fg=self._parent.colors["text"],
            activebackground=self._parent.colors["field"],
            activeforeground=self._parent.colors["text"],
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        self._reload_button.pack(side=tk.RIGHT, padx=(0, 6))
        self._update_reload_button()

    def _update_reload_button(self):
        if self._reload_button is None:
            return
        if self._marked:
            self._reload_button.configure(
                text=f"Reload Preview ({len(self._marked)} deselected)",
                bg=self._parent.colors["error"],
                fg="#07111f",
            )
        else:
            self._reload_button.configure(
                text="Reload Preview",
                bg=self._parent.colors["panel"],
                fg=self._parent.colors["text"],
            )

    def _reload_preview(self) -> bool:
        errors = []
        for path in list(self._marked):
            path_errors = _delete_print_card(
                path, self._double_sided_folder
            )
            if path_errors:
                errors.extend(path_errors)
            if not os.path.exists(path):
                self._marked.discard(path)

        self._images = _print_order_image_paths(
            self._front_folder, self._double_sided_folder
        )
        self._marked.intersection_update(self._images)
        total_pages = max(1, -(-len(self._images) // self.PAGE_SIZE))
        if self._page >= total_pages:
            self._page = total_pages - 1
        self._render_page()
        self._build_buttons()

        if errors:
            messagebox.showerror(
                "Could not deselect every card",
                "\n".join(errors),
                parent=self._win,
            )
            return False
        return True

    def _previous_page(self):
        if self._page <= 0:
            return
        self._page -= 1
        self._render_page()
        self._build_buttons()

    def _next_page(self):
        total_pages = max(1, -(-len(self._images) // self.PAGE_SIZE))
        if self._page >= total_pages - 1:
            return
        self._page += 1
        self._render_page()
        self._build_buttons()

    def _on_continue(self):
        if self._marked and not self._reload_preview():
            return
        self._win.grab_release()
        self._win.destroy()
        self._done_event.set()


class TokenArtworkWindow:
    """Preview dropped/downloaded art and replace one token after confirmation."""

    PREVIEW_W = 300
    PREVIEW_H = 410
    WATCH_INTERVAL_MS = 3000

    def __init__(self, owner: TokenReviewWindow, token_path: str):
        self._owner = owner
        self._parent = owner._parent
        self._token_path = token_path
        self._downloads_folder = _downloads_folder()
        self._download_pngs = _list_download_pngs(self._downloads_folder)
        self._known_download_pngs = {
            os.path.normcase(path) for path in self._download_pngs
        }
        self._candidate = None
        self._preview_ref = None
        self._watch_job = None
        self._always_accept = tk.BooleanVar(
            value=self._parent.always_accept_token_artwork
        )

        win = tk.Toplevel(owner._win)
        self._win = win
        win.title(f"Token artwork - {_token_name_from_path(token_path)}")
        win.configure(bg=self._parent.colors["bg"])
        win.geometry("520x690")
        win.resizable(False, False)
        win.transient(owner._win)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", self._close)

        tk.Label(
            win,
            text=f"Replace {_token_name_from_path(token_path)} artwork",
            bg=self._parent.colors["bg"], fg=self._parent.colors["text"],
            font=("Segoe UI", 14, "bold"),
        ).pack(padx=20, pady=(18, 4))
        tk.Label(
            win,
            text=(
                "Drop an image below, or download a PNG from the Scryfall window.\n"
                "New PNG files in Downloads are detected every 3 seconds."
            ),
            bg=self._parent.colors["bg"], fg=self._parent.colors["muted"],
            font=("Segoe UI", 9), justify=tk.CENTER,
        ).pack(padx=20, pady=(0, 12))

        drop_frame = tk.Frame(
            win,
            width=self.PREVIEW_W,
            height=self.PREVIEW_H,
            bg=self._parent.colors["field"],
            relief=tk.GROOVE,
            borderwidth=2,
        )
        drop_frame.pack(padx=20)
        drop_frame.pack_propagate(False)
        self._drop_zone = tk.Label(
            drop_frame,
            text="Drag & drop an image here",
            bg=self._parent.colors["field"], fg=self._parent.colors["muted"],
            font=("Segoe UI", 11, "bold"),
            compound=tk.CENTER,
        )
        self._drop_zone.pack(fill=tk.BOTH, expand=True)

        if DND_FILES is not None:
            try:
                self._drop_zone.drop_target_register(DND_FILES)
                self._drop_zone.dnd_bind("<<Drop>>", self._on_drop)
            except tk.TclError:
                pass

        self._status = tk.StringVar(value="Waiting for an image...")
        tk.Label(
            win,
            textvariable=self._status,
            bg=self._parent.colors["bg"], fg=self._parent.colors["muted"],
            font=("Segoe UI", 9), wraplength=470,
        ).pack(fill=tk.X, padx=20, pady=8)

        buttons = tk.Frame(win, bg=self._parent.colors["bg"])
        buttons.pack(fill=tk.X, padx=20, pady=(0, 16))
        tk.Button(
            buttons, text="Choose image...", command=self._choose_image,
            bg=self._parent.colors["panel"], fg=self._parent.colors["text"],
            activebackground=self._parent.colors["field"],
            activeforeground=self._parent.colors["text"],
            relief=tk.FLAT, font=("Segoe UI", 9), padx=10, pady=7,
            cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            buttons, text="Cancel", command=self._close,
            bg=self._parent.colors["panel"], fg=self._parent.colors["text"],
            activebackground=self._parent.colors["field"],
            activeforeground=self._parent.colors["text"],
            relief=tk.FLAT, font=("Segoe UI", 9), padx=10, pady=7,
            cursor="hand2",
        ).pack(side=tk.RIGHT)
        self._confirm_button = tk.Button(
            buttons, text="Use this artwork", command=self._confirm,
            bg=self._parent.colors["accent"], fg="#07111f",
            activebackground=self._parent.colors["accent_hover"],
            activeforeground="#07111f", relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"), padx=12, pady=7,
            cursor="hand2", state=tk.DISABLED,
        )
        self._confirm_button.pack(side=tk.RIGHT, padx=(0, 8))

        tk.Checkbutton(
            win,
            text="Always accept new token artwork this instance",
            variable=self._always_accept,
            command=self._remember_always_accept,
            bg=self._parent.colors["bg"],
            fg=self._parent.colors["text"],
            activebackground=self._parent.colors["bg"],
            activeforeground=self._parent.colors["text"],
            selectcolor=self._parent.colors["field"],
            font=("Segoe UI", 9),
            cursor="hand2",
        ).pack(anchor="w", padx=20, pady=(0, 14))

        # Let the dialog paint before the browser takes focus.
        win.after(150, self._open_search_and_start_watcher)

    def _open_search_and_start_watcher(self):
        search_url = _token_search_url(self._token_path)
        try:
            opened = webbrowser.open(search_url, new=2)
        except Exception as exc:
            messagebox.showwarning(
                "Could not open Scryfall",
                f"Open this page manually:\n{search_url}\n\n{exc}",
                parent=self._win,
            )
        else:
            if not opened:
                messagebox.showwarning(
                    "Could not open Scryfall",
                    f"Open this page manually:\n{search_url}",
                    parent=self._win,
                )
        self._watch_job = self._win.after(
            self.WATCH_INTERVAL_MS, self._check_downloads
        )

    def _check_downloads(self):
        self._download_pngs = _list_download_pngs(self._downloads_folder)
        new_paths = [
            path for path in self._download_pngs
            if os.path.normcase(path) not in self._known_download_pngs
        ]

        if new_paths:
            existing_new_paths = [path for path in new_paths if os.path.isfile(path)]
            if existing_new_paths:
                newest = max(existing_new_paths, key=lambda path: os.path.getmtime(path))
                if self._set_candidate(newest, "New PNG detected in Downloads"):
                    self._known_download_pngs.update(
                        os.path.normcase(path) for path in new_paths
                    )
                    if self._always_accept.get():
                        self._confirm()
                        return

        if self._win.winfo_exists():
            self._watch_job = self._win.after(
                self.WATCH_INTERVAL_MS, self._check_downloads
            )

    def _on_drop(self, event):
        try:
            paths = list(self._win.tk.splitlist(event.data))
        except (tk.TclError, TypeError):
            paths = []
        valid_paths = [
            os.path.abspath(path) for path in paths
            if os.path.isfile(path)
            and os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS
        ]
        if not valid_paths:
            messagebox.showerror(
                "Unsupported drop",
                "Drop a PNG, JPG, JPEG, WEBP, or BMP image file.",
                parent=self._win,
            )
            return "break"
        if self._set_candidate(valid_paths[0], "Dropped image"):
            if self._always_accept.get():
                self._confirm()
        return "break"

    def _choose_image(self):
        path = filedialog.askopenfilename(
            parent=self._win,
            title="Choose replacement token artwork",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._set_candidate(path, "Selected image")

    def _remember_always_accept(self):
        self._parent.always_accept_token_artwork = self._always_accept.get()

    def _set_candidate(self, path: str, source_label: str):
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened)
                image.thumbnail((self.PREVIEW_W - 12, self.PREVIEW_H - 12), Image.LANCZOS)
                preview = ImageTk.PhotoImage(image.copy())
        except Exception as exc:
            messagebox.showerror(
                "Invalid image", f"Could not preview {os.path.basename(path)}:\n{exc}",
                parent=self._win,
            )
            return False

        self._candidate = os.path.abspath(path)
        self._preview_ref = preview
        self._drop_zone.configure(image=preview, text="")
        self._status.set(f"{source_label}: {os.path.basename(path)}")
        self._confirm_button.configure(state=tk.NORMAL)
        return True

    def _confirm(self):
        if self._candidate is None:
            return
        try:
            _replace_image_file(self._candidate, self._token_path)
            moved_to = None
            if (
                os.path.splitext(self._candidate)[1].lower() == ".png"
                and os.path.normcase(os.path.dirname(self._candidate))
                == os.path.normcase(os.path.abspath(self._downloads_folder))
            ):
                moved_to = _move_download_to_cleanup(self._candidate)
        except Exception as exc:
            messagebox.showerror("Artwork replacement failed", str(exc), parent=self._win)
            return

        self._owner._render_page()
        self._parent.append_log(
            f"Replaced token artwork: {os.path.basename(self._token_path)}\n"
        )
        if moved_to:
            self._parent.append_log(
                f"Moved used download to {os.path.relpath(moved_to, REPO_ROOT)}\n"
            )
        self._close()

    def _close(self):
        if self._watch_job is not None:
            try:
                self._win.after_cancel(self._watch_job)
            except tk.TclError:
                pass
            self._watch_job = None
        try:
            self._win.grab_release()
        except tk.TclError:
            pass
        self._win.destroy()
        if self._owner._win.winfo_exists():
            self._owner._win.grab_set()


class MtgDeckGui(TkRoot):
    def __init__(self):
        super().__init__()

        self.title("MTG Deck PDF Maker")
        self.geometry("1180x650")
        self.minsize(1000, 560)

        self.colors = {
            "bg": "#171a21",
            "panel": "#20242e",
            "field": "#10131a",
            "text": "#eef1f7",
            "muted": "#aab2c0",
            "accent": "#6aa5ff",
            "accent_hover": "#87b7ff",
            "error": "#ff7a7a",
            "success": "#86efac",
        }

        self.configure(bg=self.colors["bg"])
        self.always_accept_token_artwork = False
        self._configure_grid()
        self._build_widgets()
        self._perform_startup_cleanup()

    def _perform_startup_cleanup(self):
        try:
            removed_by_folder = _clean_startup_workspace(REPO_ROOT)
        except Exception as exc:
            error_message = str(exc)
            self._append_log(f"Startup cleanup failed: {error_message}\n", "error")
            self.status.set("Startup cleanup failed. See the log.")
            self.after_idle(
                lambda error_message=error_message: messagebox.showerror(
                    "Startup cleanup failed", error_message, parent=self
                )
            )
            return

        total_removed = sum(removed_by_folder.values())
        details = ", ".join(
            f"{folder}: {count}"
            for folder, count in removed_by_folder.items()
        )
        self._append_log(
            f"Startup cleanup removed {total_removed} working file(s) "
            f"({details}).\n"
        )
        self.status.set(f"Ready. Startup cleanup removed {total_removed} file(s).")

    def _configure_grid(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=310)
        self.rowconfigure(2, weight=1)

    def _build_widgets(self):
        header = tk.Frame(self, bg=self.colors["bg"], padx=24, pady=20)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title = tk.Label(
            header,
            text="MTG Deck PDF Maker",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI", 20, "bold"),
            anchor="w",
        )
        title.grid(row=0, column=0, sticky="ew")

        subtitle = tk.Label(
            header,
            text="Fetch a Moxfield deck with tokens, create an A4 PDF, then open the result.",
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
            anchor="w",
        )
        subtitle.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        form = tk.Frame(self, bg=self.colors["panel"], padx=18, pady=18)
        form.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 16))
        form.columnconfigure(1, weight=1)

        deck_label = tk.Label(
            form,
            text="Deck URL",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        deck_label.grid(row=0, column=0, sticky="w", padx=(0, 12))

        self.deck_url = tk.StringVar(
            value="https://www.moxfield.com/decks/NpykznkOl0q5hIs7yTBcmg"
        )
        deck_entry = tk.Entry(
            form,
            textvariable=self.deck_url,
            bg=self.colors["field"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief=tk.FLAT,
            font=("Segoe UI", 11),
        )
        deck_entry.grid(row=0, column=1, sticky="ew", ipady=8)
        deck_entry.focus_set()

        self.run_button = tk.Button(
            form,
            text="Run Workflow",
            command=lambda: self.run_workflow(change_artwork=False),
            bg=self.colors["accent"],
            fg="#07111f",
            activebackground=self.colors["accent_hover"],
            activeforeground="#07111f",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=9,
            cursor="hand2",
        )
        self.run_button.grid(row=0, column=2, sticky="e", padx=(12, 0))

        self.artwork_button = tk.Button(
            form,
            text="Change Artwork before .pdf",
            command=lambda: self.run_workflow(change_artwork=True),
            bg=self.colors["panel"],
            fg=self.colors["text"],
            activebackground=self.colors["field"],
            activeforeground=self.colors["text"],
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=9,
            cursor="hand2",
        )
        self.artwork_button.grid(row=1, column=2, sticky="e", padx=(12, 0), pady=(8, 0))

        self.token_artwork_button = tk.Button(
            form,
            text="Wanna change token artwork?",
            command=lambda: self.run_workflow(change_token_artwork=True),
            bg=self.colors["panel"],
            fg=self.colors["text"],
            activebackground=self.colors["field"],
            activeforeground=self.colors["text"],
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=9,
            cursor="hand2",
        )
        self.token_artwork_button.grid(
            row=2, column=2, sticky="e", padx=(12, 0), pady=(8, 0)
        )

        self._build_workflow_guide()

        log_frame = tk.Frame(self, bg=self.colors["panel"], padx=12, pady=12)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 18))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log = scrolledtext.ScrolledText(
            log_frame,
            bg=self.colors["field"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief=tk.FLAT,
            font=("Consolas", 10),
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        self.log.grid(row=0, column=0, sticky="nsew")

        self.status = tk.StringVar(value="Ready")
        status_bar = tk.Label(
            self,
            textvariable=self.status,
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            padx=24,
            pady=8,
        )
        status_bar.grid(row=3, column=0, sticky="ew")

    def _build_workflow_guide(self):
        panel = tk.Frame(
            self,
            bg=self.colors["panel"],
            padx=18,
            pady=18,
            width=300,
        )
        self.workflow_guide = panel
        panel.grid(
            row=0,
            column=1,
            rowspan=4,
            sticky="nsew",
            padx=(0, 24),
            pady=20,
        )
        panel.grid_propagate(False)

        tk.Label(
            panel,
            text="Workflow",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 15, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            panel,
            text="From deck URL to print-ready PDF",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 14))

        for heading, description in WORKFLOW_STEPS:
            tk.Label(
                panel,
                text=heading,
                bg=self.colors["panel"],
                fg=self.colors["accent"],
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            ).pack(fill=tk.X)
            tk.Label(
                panel,
                text=description,
                bg=self.colors["panel"],
                fg=self.colors["muted"],
                font=("Segoe UI", 8),
                justify=tk.LEFT,
                anchor="w",
                wraplength=260,
            ).pack(fill=tk.X, pady=(1, 10))

    def run_workflow(self, change_artwork=False, change_token_artwork=False):
        deck_url = self.deck_url.get().strip()
        if not deck_url:
            messagebox.showerror("Deck URL required", "Enter a deck URL first.")
            return

        self.run_button.configure(state=tk.DISABLED, text="Running...")
        self.artwork_button.configure(state=tk.DISABLED)
        self.token_artwork_button.configure(state=tk.DISABLED)
        self.status.set("Running fetch, PDF generation, and open steps...")
        self.clear_log()

        worker = threading.Thread(
            target=self._run_workflow_thread,
            args=(deck_url, change_artwork, change_token_artwork),
            daemon=True,
        )
        worker.start()

    def _run_workflow_thread(
        self, deck_url, change_artwork=False, change_token_artwork=False
    ):
        front_folder = os.path.join(REPO_ROOT, "game", "front")
        double_sided_folder = os.path.join(REPO_ROOT, "game", "double_sided")

        try:
            # Step 2 – clean game/front/ so we start with a fresh set of images
            cleaned = _clean_folder(front_folder)
            self.append_log(f"Cleaned {cleaned} existing file(s) from game/front/\n")

            # Step 3 – download card images (including tokens) via the MTG plugin
            fetch_cmd = [
                script_python(),
                os.path.join("plugins", "mtg", "fetch.py"),
                deck_url,
                "url",
                "--tokens",
            ]
            self.append_log("\n== Downloading card images ==\n")
            self.append_log(f"{self._format_command(fetch_cmd)}\n\n")
            double_faced_cards = []

            def capture_double_faced_card(line):
                card = _parse_double_faced_card_line(line)
                if card is not None and card not in double_faced_cards:
                    double_faced_cards.append(card)

            exit_code = self._run_command(
                fetch_cmd, line_callback=capture_double_faced_card
            )
            if exit_code != 0:
                raise RuntimeError(f"Download failed with exit code {exit_code}.")

            for card in double_faced_cards:
                stems = _double_faced_card_stems(card)
                front_paths = _find_images_with_stems(front_folder, stems)
                back_paths = _find_images_with_stems(double_sided_folder, stems)
                if not front_paths and not back_paths:
                    self.append_log(
                        f'Could not find downloaded files for double-faced card: '
                        f'{card["name"]}\n',
                        "error",
                    )
                    continue

                removal_done = threading.Event()
                self.after(
                    0,
                    lambda card=card, front_paths=front_paths,
                    back_paths=back_paths, removal_done=removal_done:
                    DoubleFacedRemovalWindow(
                        self,
                        card,
                        front_paths,
                        back_paths,
                        removal_done,
                    ),
                )
                removal_done.wait()

            # Basic lands are intentionally excluded from every generated PDF.
            for land, count in _remove_basic_lands(front_folder).items():
                if count:
                    self.append_log(f"{count} {land} removed\n")

            # Step 4 – pause: open the token review window on the main thread and
            #           block this background thread until the user clicks Continue
            self.append_log("\nOpening token review window\u2026\n")
            review_done = threading.Event()
            self.after(
                0,
                lambda: TokenReviewWindow(
                    self,
                    front_folder,
                    review_done,
                    allow_token_artwork=change_token_artwork,
                ),
            )
            review_done.wait()
            self.append_log("Token review complete.\n")

            # Step 5 - review all remaining non-token cards before continuing.
            self.append_log("\nOpening all-card review window...\n")
            card_review_done = threading.Event()
            self.after(
                0,
                lambda: TokenReviewWindow(
                    self,
                    front_folder,
                    card_review_done,
                    include_tokens=False,
                ),
            )
            card_review_done.wait()
            self.append_log("All-card review complete.\n")

            if change_artwork:
                # Pause so artwork can be replaced before the PDF is created.
                self.append_log("\nWaiting for artwork changes before PDF creation...\n")
                artwork_ready = threading.Event()
                self.after(0, lambda: self._prompt_for_artwork_changes(artwork_ready))
                artwork_ready.wait()
                self.append_log("Artwork is ready. Proceeding to PDF creation.\n")
            else:
                self.append_log("Proceeding to PDF creation.\n")

            # Step 7 – create the A4 PDF from whatever images remain in game/front/
            pdf_cmd = [
                script_python(),
                "create_pdf.py",
                "--paper_size",
                "a4",
            ]
            self.append_log("\n== Creating A4 PDF ==\n")
            self.append_log(f"{self._format_command(pdf_cmd)}\n\n")
            exit_code = self._run_command(pdf_cmd)
            if exit_code != 0:
                raise RuntimeError(f"PDF creation failed with exit code {exit_code}.")

            if not os.path.isfile(OUTPUT_PDF):
                raise FileNotFoundError(f"PDF was not created: {OUTPUT_PDF}")

            # Step 8 – open the finished PDF with the system default viewer
            self.append_log(f"\nOpening {OUTPUT_PDF}\n")
            self._open_pdf(OUTPUT_PDF)
            self.after(0, self._set_success)

        except Exception as exc:
            error_message = str(exc)
            self.append_log(f"\nERROR: {error_message}\n", "error")
            self.after(
                0,
                lambda error_message=error_message: self._set_failed(error_message),
            )

    def _run_command(self, command, line_callback=None):
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        assert process.stdout is not None
        for line in process.stdout:
            self.append_log(line)
            if line_callback is not None:
                line_callback(line)

        return process.wait()

    def _prompt_for_artwork_changes(self, done_event):
        """Pause the workflow until the user confirms the artwork is ready."""
        messagebox.showinfo(
            "Change Artwork before .pdf",
            "You can now replace or edit the images in game/front/.\n\n"
            "Click OK when the artwork is ready. The PDF will be created afterwards.",
            parent=self,
        )
        done_event.set()

    def _open_pdf(self, path):
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def append_log(self, text, tag=None):
        self.after(0, lambda: self._append_log(text, tag))

    def _append_log(self, text, tag=None):
        self.log.configure(state=tk.NORMAL)
        if tag == "error":
            self.log.tag_configure("error", foreground=self.colors["error"])
            self.log.insert(tk.END, text, "error")
        else:
            self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def clear_log(self):
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def _set_success(self):
        self.status.set("Done. PDF opened.")
        self.run_button.configure(state=tk.NORMAL, text="Run Workflow")
        self.artwork_button.configure(state=tk.NORMAL)
        self.token_artwork_button.configure(state=tk.NORMAL)

    def _set_failed(self, message):
        self.status.set("Failed. See log for details.")
        self.run_button.configure(state=tk.NORMAL, text="Run Workflow")
        self.artwork_button.configure(state=tk.NORMAL)
        self.token_artwork_button.configure(state=tk.NORMAL)
        messagebox.showerror("Workflow failed", message)

    def _format_command(self, command):
        return " ".join(f'"{part}"' if " " in part else part for part in command)


if __name__ == "__main__":
    app = MtgDeckGui()
    app.mainloop()
