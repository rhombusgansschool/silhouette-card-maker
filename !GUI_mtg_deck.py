import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

from PIL import Image, ImageTk


REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
OUTPUT_PDF = os.path.join(REPO_ROOT, "game", "output", "game.pdf")

# ─── Workflow overview ───────────────────────────────────────────────────────
# 1. User enters a Moxfield deck URL and clicks "Run Workflow".
# 2. All existing files in game/front/ are deleted so the folder is clean.
# 3. plugins/mtg/fetch.py downloads every card image (including tokens)
#    into game/front/.
# 4. Execution PAUSES — a Token Review window opens.  It scans game/front/
#    for any image whose filename contains "token" and shows thumbnails in
#    an 8-column × 3-row grid (24 cards per page).  Clicking a card toggles
#    a red ✕ overlay.  If more than 24 token images exist, a "Next page"
#    button appears at the bottom.  "Delete all ✕" removes every marked
#    image from disk.  "Continue" closes the window and resumes the workflow.
# 5. create_pdf.py lays out all remaining images in game/front/ into an A4
#    PDF saved to game/output/game.pdf.
# 6. The finished PDF is opened with the system default viewer.
# ─────────────────────────────────────────────────────────────────────────────


def _clean_folder(folder: str) -> int:
    """Delete every file (non-recursive) inside *folder*. Returns the count removed."""
    removed = 0
    try:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
    except OSError:
        pass
    return removed


def script_python():
    executable = sys.executable
    if sys.platform.startswith("win") and executable.lower().endswith("pythonw.exe"):
        python_exe = os.path.join(os.path.dirname(executable), "python.exe")
        if os.path.isfile(python_exe):
            return python_exe
    return executable


class TokenReviewWindow:
    """Modal pause window: review token images before PDF creation.

    Shows every image in *folder* whose filename contains "token" as
    thumbnails in an 8-column × 3-row grid (PAGE_SIZE = 24 per page).
    Clicking a card toggles a red ✕ overlay.  "Delete all ✕" removes marked
    images from disk.  "Continue" signals *done_event* and closes the window.
    """

    COLS = 8
    ROWS = 3
    PAGE_SIZE = COLS * ROWS  # 24 cards per page
    CARD_W = 80
    CARD_H = 112

    def __init__(self, parent: "MtgDeckGui", folder: str, done_event: threading.Event):
        self._parent = parent
        self._folder = folder
        self._done_event = done_event
        self._marked: set = set()
        self._page = 0
        self._thumb_refs: list = []
        self._delete_btn = None

        self._images = self._find_token_images()

        win = tk.Toplevel(parent)
        self._win = win
        win.title(f"Review Token Cards  \u2014  {len(self._images)} token(s) found")
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

    def _find_token_images(self) -> list:
        exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        candidates = []
        try:
            for name in sorted(os.listdir(self._folder)):
                if "token" in name.lower() and os.path.splitext(name)[1].lower() in exts:
                    candidates.append(os.path.join(self._folder, name))
        except OSError:
            pass

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
                text="No token images found.",
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
                img = Image.open(path)
                img.thumbnail((self.CARD_W, self.CARD_H), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
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

        if self._page > 0:
            tk.Button(
                self._btn_frame, text="\u25c4 Prev",
                command=self._prev_page,
                bg=self._parent.colors["panel"], fg=self._parent.colors["text"],
                relief=tk.FLAT, font=("Segoe UI", 9), padx=8, pady=5, cursor="hand2",
            ).pack(side=tk.LEFT)

        if total_pages > 1:
            tk.Label(
                self._btn_frame,
                text=f"  {self._page + 1} / {total_pages}  ",
                bg=self._parent.colors["bg"], fg=self._parent.colors["muted"],
                font=("Segoe UI", 9),
            ).pack(side=tk.LEFT)

        if self._page < total_pages - 1:
            tk.Button(
                self._btn_frame, text="Next \u25ba",
                command=self._next_page,
                bg=self._parent.colors["panel"], fg=self._parent.colors["text"],
                relief=tk.FLAT, font=("Segoe UI", 9), padx=8, pady=5, cursor="hand2",
            ).pack(side=tk.LEFT)

        # Right-side buttons (packed right-to-left)
        tk.Button(
            self._btn_frame, text="Continue \u2192",
            command=self._on_continue,
            bg=self._parent.colors["accent"], fg="#07111f",
            activebackground=self._parent.colors["accent_hover"], activeforeground="#07111f",
            relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=12, pady=5, cursor="hand2",
        ).pack(side=tk.RIGHT)

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
        self._page -= 1
        self._render_page()
        self._build_bottom_buttons()

    def _next_page(self):
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

    def _on_continue(self):
        self._win.grab_release()
        self._win.destroy()
        self._done_event.set()


class MtgDeckGui(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("MTG Deck PDF Maker")
        self.geometry("860x560")
        self.minsize(700, 460)

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
        self._configure_grid()
        self._build_widgets()

    def _configure_grid(self):
        self.columnconfigure(0, weight=1)
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
            command=self.run_workflow,
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

    def run_workflow(self):
        deck_url = self.deck_url.get().strip()
        if not deck_url:
            messagebox.showerror("Deck URL required", "Enter a deck URL first.")
            return

        self.run_button.configure(state=tk.DISABLED, text="Running...")
        self.status.set("Running fetch, PDF generation, and open steps...")
        self.clear_log()

        worker = threading.Thread(target=self._run_workflow_thread, args=(deck_url,), daemon=True)
        worker.start()

    def _run_workflow_thread(self, deck_url):
        front_folder = os.path.join(REPO_ROOT, "game", "front")

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
            exit_code = self._run_command(fetch_cmd)
            if exit_code != 0:
                raise RuntimeError(f"Download failed with exit code {exit_code}.")

            # Step 4 – pause: open the token review window on the main thread and
            #           block this background thread until the user clicks Continue
            self.append_log("\nOpening token review window\u2026\n")
            review_done = threading.Event()
            self.after(0, lambda: TokenReviewWindow(self, front_folder, review_done))
            review_done.wait()
            self.append_log("Token review complete. Proceeding to PDF creation.\n")

            # Step 5 – create the A4 PDF from whatever images remain in game/front/
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

            # Step 6 – open the finished PDF with the system default viewer
            self.append_log(f"\nOpening {OUTPUT_PDF}\n")
            self._open_pdf(OUTPUT_PDF)
            self.after(0, self._set_success)

        except Exception as exc:
            self.append_log(f"\nERROR: {exc}\n", "error")
            self.after(0, lambda: self._set_failed(str(exc)))

    def _run_command(self, command):
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

        return process.wait()

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

    def _set_failed(self, message):
        self.status.set("Failed. See log for details.")
        self.run_button.configure(state=tk.NORMAL, text="Run Workflow")
        messagebox.showerror("Workflow failed", message)

    def _format_command(self, command):
        return " ".join(f'"{part}"' if " " in part else part for part in command)


if __name__ == "__main__":
    app = MtgDeckGui()
    app.mainloop()
