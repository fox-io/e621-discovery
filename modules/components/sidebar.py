import sys
import tkinter as tk


class Sidebar(tk.Frame):
    """A widget to display the main controls and tag list for the application."""

    def __init__(self, master, callbacks: dict, fonts: dict):
        """
        Initializes the Sidebar frame.

        Args:
            master: The parent tkinter widget.
            callbacks (dict): A dictionary of callbacks for button commands.
                Expected keys:
                    "on_random_toggle", "on_search", "on_follow", "on_ignore",
                    "on_skip", "on_edit_artists", "on_edit_tags",
                    "on_tag_search", "on_tag_ban".
            fonts (dict): A dictionary of fonts for tag rendering.
                Expected keys: "normal", "strike", "default_fg".
        """
        super().__init__(master)
        self.callbacks = callbacks
        self.fonts = fonts
        self._tag_text_labels: dict = {}
        self._build_ui()

    def _build_ui(self):
        self._random_cb = tk.Checkbutton(self, text="Random order", command=self.callbacks["on_random_toggle"])
        self._random_cb.select()
        self._random_cb.pack(anchor="w", pady=(0, 5))

        sf = tk.Frame(self)
        sf.pack(anchor="w", pady=(0, 10))
        self.search_entry = tk.Entry(sf, width=15, cursor="xterm")
        self.search_entry.bind("<Return>", lambda e: self.callbacks["on_search"]())
        self.search_entry.pack(side="left", padx=(0, 5))
        tk.Button(sf, text="\U0001f50d", command=self.callbacks["on_search"], cursor="pointinghand").pack(side="left")

        self.artist_label = tk.Label(self, text="Artist: \u2014")
        self.artist_label.pack(anchor="w")

        af = tk.Frame(self)
        af.pack(anchor="center", pady=2)
        tk.Button(af, text="\u2764\ufe0f", command=self.callbacks["on_follow"], cursor="pointinghand").pack(side="left", padx=(0, 2))
        tk.Button(af, text="\U0001f6ab", command=self.callbacks["on_ignore"], cursor="pointinghand").pack(side="left", padx=(0, 0))
        tk.Button(af, text="\u23ed\ufe0f", command=self.callbacks["on_skip"], cursor="pointinghand").pack(side="left", padx=(2, 0))

        tk.Label(self, text="Post Tags").pack(anchor="w", pady=(6, 0))

        tk.Button(self, text="Quit", width=10, command=lambda: sys.exit(0), cursor="pointinghand").pack(side="bottom", anchor="w", pady=2)
        tk.Button(self, text="Edit Artists", command=self.callbacks["on_edit_artists"], cursor="pointinghand").pack(
            side="bottom", anchor="w", pady=(0, 2), fill="x")
        tk.Button(self, text="Edit Tags", command=self.callbacks["on_edit_tags"], cursor="pointinghand").pack(
            side="bottom", anchor="w", pady=(0, 2), fill="x")

        tf = tk.Frame(self)
        tf.pack(fill="both", expand=True, pady=(0, 5))
        self.tag_canvas = tk.Canvas(tf, width=200, highlightthickness=0)
        sb = tk.Scrollbar(tf, orient="vertical", command=self.tag_canvas.yview)
        self.tag_canvas.configure(yscrollcommand=sb.set)
        self.tag_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tag_inner = tk.Frame(self.tag_canvas)
        self.tag_canvas.create_window((0, 0), window=self.tag_inner, anchor="nw")

        self.tag_inner.bind(
            "<Configure>",
            lambda e: self.tag_canvas.configure(
                scrollregion=self.tag_canvas.bbox("all")))
        self.tag_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.tag_inner.bind("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        self.tag_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def get_search_query(self) -> str:
        return self.search_entry.get()

    def set_search_query(self, query: str):
        self.search_entry.delete(0, tk.END)
        self.search_entry.insert(0, query)

    def update_artist(self, artist_name: str):
        self.artist_label.config(text=f"Artist: {artist_name}")

    def reset_artist(self):
        self.artist_label.config(text="Artist: \u2014")

    def reset_tag_list(self):
        for w in self.tag_inner.winfo_children():
            w.destroy()
        self.tag_canvas.yview_moveto(0)

    def render_tags(self, all_tags: list, banned_set: set):
        self.reset_tag_list()
        self._tag_text_labels = {}
        tags = sorted(all_tags)

        for tag in tags:
            row = tk.Frame(self.tag_inner)
            row.pack(fill="x", anchor="w", pady=0, ipady=0)
            search_lbl = tk.Label(row, text="\U0001f50d", cursor="pointinghand",
                                   font=("TkDefaultFont", 7), pady=0)
            search_lbl.pack(side="left", padx=(0, 1), pady=0)
            search_lbl.bind("<Button-1>", lambda e, t=tag: self.callbacks["on_tag_search"](t))

            ban_lbl = tk.Label(row, text="\U0001f6ab", cursor="pointinghand",
                               font=("TkDefaultFont", 7), pady=0)
            ban_lbl.pack(side="left", padx=(0, 3), pady=0)
            ban_lbl.bind("<Button-1>", lambda e, t=tag: self.callbacks["on_tag_ban"](t))

            is_banned = tag in banned_set
            lbl = tk.Label(row, text=tag, anchor="w", pady=0,
                           font=self.fonts["strike"] if is_banned else self.fonts["normal"])
            if is_banned:
                lbl.config(fg="grey")
            lbl.pack(side="left", pady=0)

            for widget in (row, search_lbl, ban_lbl, lbl):
                widget.bind("<MouseWheel>", self._on_mousewheel)

            self._tag_text_labels[tag] = lbl
        self.tag_canvas.configure(scrollregion=self.tag_canvas.bbox("all"))

    def update_tag_style(self, tag: str, is_banned: bool):
        lbl = self._tag_text_labels.get(tag)
        if lbl:
            if is_banned:
                lbl.config(font=self.fonts["strike"], fg="grey")
            else:
                lbl.config(font=self.fonts["normal"], fg=self.fonts["default_fg"])