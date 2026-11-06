"""Small desktop layout helpers; no solver imports."""
import tkinter as tk
from tkinter import font, ttk


def parameter_entry(parent, row, label, variable, width=10):
    """Keep the value reachable when a narrow panel meets a large system font."""
    field = ttk.Frame(parent)
    field.grid(row=row, column=0, columnspan=2, sticky='ew', pady=3)
    caption = ttk.Label(field, text=label, wraplength=190)
    entry = ttk.Entry(field, textvariable=variable, width=width)
    field.columnconfigure(0, weight=1)
    parent.columnconfigure(1, weight=1)
    def arrange(event):
        stacked = event.width < max(260, font.nametofont('TkDefaultFont').metrics('linespace') * 18)
        caption.configure(wraplength=max(80, event.width-4) if stacked else max(80, int(event.width*.62)))
        caption.grid(row=0, column=0, columnspan=2 if stacked else 1, sticky='w')
        entry.grid(row=1 if stacked else 0, column=0 if stacked else 1,
                   columnspan=2 if stacked else 1, sticky='ew', padx=(0 if stacked else 7, 0), pady=(3 if stacked else 0, 0))
    field.bind('<Configure>', arrange)
    return caption, entry


def parameter_panel(parent,width=365):
    from .gui_style import ScrollForm
    wrapper=ScrollForm(parent,width=width)
    wrapper.pack(side='left',fill='y')
    return wrapper.body
