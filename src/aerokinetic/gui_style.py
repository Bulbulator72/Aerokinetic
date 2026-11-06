"""Shared desktop presentation; independent of the numerical model."""
import sys
import tkinter as tk
from tkinter import font, ttk

COLORS = {"background": "#eef2f6", "surface": "#ffffff", "ink": "#203249",
          "muted": "#51647a", "border": "#d7e0ea", "accent": "#1864ab",
          "selected": "#e5effa", "error": "#a52626"}


def enable_windows_dpi():
    """Set awareness before creating Tk; do not override a host's DPI policy."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def install_theme(root):
    available = {f.casefold(): f for f in font.families(root)}
    family = next((available[f.casefold()] for f in ("Segoe UI", "DejaVu Sans", "Arial")
                   if f.casefold() in available), "TkDefaultFont")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        font.nametofont(name, root=root).configure(family=family, size=10)
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.configure(background=COLORS["background"])
    style.configure(".", foreground=COLORS["ink"], background=COLORS["background"])
    style.configure("TFrame", background=COLORS["background"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("TLabel", background=COLORS["background"])
    style.configure("Surface.TLabel", background=COLORS["surface"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"])
    style.configure("Title.TLabel", font=(family, 16, "bold"))
    style.configure("Brand.TLabel", font=(family, 16, "bold"), background=COLORS["surface"])
    style.configure("Subtitle.TLabel", foreground=COLORS["muted"], background=COLORS["surface"])
    style.configure("State.TLabel", foreground=COLORS["accent"], background=COLORS["selected"], padding=(12, 7))
    style.configure("Error.TLabel", foreground=COLORS["error"])
    style.configure("TButton", padding=(10, 6))
    style.map("TButton", background=[("active", "#e1eaf3")])
    style.configure("Primary.TButton", background=COLORS["accent"], foreground="white", padding=(14, 7))
    style.map("Primary.TButton", background=[("disabled", "#c9d2de"), ("active", "#124e89")],
              foreground=[("disabled", "#526173"), ("!disabled", "white")])
    style.configure("Nav.TButton", anchor="w", background=COLORS["surface"], padding=(14, 14), relief="flat")
    style.configure("Selected.Nav.TButton", anchor="w", background=COLORS["selected"],
                    foreground=COLORS["accent"], padding=(14, 14), font=(family, 10, "bold"), relief="flat")
    style.map("Selected.Nav.TButton", background=[("active", "#d5e5f7")])
    style.configure("TLabelframe", padding=10, bordercolor=COLORS["border"])
    style.configure("TLabelframe.Label", font=(family, 10, "bold"))
    style.configure("TNotebook", tabmargins=(0, 3, 0, 0), borderwidth=0)
    style.configure("TNotebook.Tab", padding=(10, 7))
    style.map("TNotebook.Tab", background=[("selected", COLORS["surface"])],
              foreground=[("selected", COLORS["accent"])])
    style.configure("TEntry", padding=5, fieldbackground=COLORS["surface"])
    style.map("TEntry", bordercolor=[("invalid", COLORS["error"])])
    style.configure("Treeview", rowheight=28, fieldbackground=COLORS["surface"], background=COLORS["surface"])
    style.configure("Treeview.Heading", padding=6)

    def wheel(event):
        widget = event.widget
        while widget is not None:
            canvas = getattr(widget, "_scroll_canvas", None)
            if canvas is not None:
                if canvas.yview() != (0.0, 1.0):
                    delta = (-1 if event.num == 4 else 1) if getattr(event, "num", None) in (4, 5) else -int(event.delta / 120 or (1 if event.delta > 0 else -1))
                    canvas.yview_scroll(delta, "units")
                    return "break"
                return
            widget = getattr(widget, "master", None)
    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind_all(sequence, wheel, add="+")


class ScrollForm(ttk.Frame):
    def __init__(self, parent, width=340):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, width=width, highlightthickness=0,
                                background=COLORS["background"], yscrollincrement=18)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas, padding=(12, 8))
        self.body._scroll_canvas = self.canvas
        self._scroll_canvas = self.canvas
        item = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(item, width=event.width))

    def reveal(self, widget):
        self.update_idletasks()
        y = widget.winfo_rooty() - self.body.winfo_rooty()
        height = max(self.body.winfo_height(), 1)
        self.canvas.yview_moveto(max(0, (y - 30) / height))


class FormInputError(ValueError):
    def __init__(self, key, message):
        self.key = key
        super().__init__(message)
