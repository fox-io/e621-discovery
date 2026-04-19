import sys
import tkinter as tk


class Sidebar(tk.Frame):
    """A widget to display the main controls and tag list for the application."""

    def __init__(self, master, callbacks: dict):
        """
        Initializes the Sidebar frame.

        Args:
            master: The parent tkinter widget.
            callbacks (dict): A dictionary of callbacks for button commands.
                Expected keys: "on_random_toggle", "on_search", "on_follow",
                "on_ignore", "on_skip", "on_edit_artists", "on_edit_tags".
        """
        super().__init__(master)
        self.callbacks = callbacks
        self._build_ui()

    def _build_ui(self):
        self._random_cb = tk.Checkbutton(self, text="Random order", command=self.callbacks["on_random_toggle"])
        self._random_cb.select()
        self._random_cb.pack(anchor="w", pady=(0, 5))

        sf = tk.Frame(self)
        sf.pack(anchor="w", pady=(0, 10))
        self.search_entry = tk.Entry(sf, width=15)
        self.search_entry.bind("<Return>", lambda e: self.callbacks["on_search"]())
        self.search_entry.pack(side="left", padx=(0, 5))
        tk.Button(sf, text="\U0001f50d", command=self.callbacks["on_search"]).pack(side="left")

        self.artist_label = tk.Label(self, text="Artist: \u2014")
        self.artist_label.pack(anchor="w")

        af = tk.Frame(self)
        af.pack(anchor="center", pady=2)
        tk.Button(af, text="\u2764\ufe0f", command=self.callbacks["on_follow"]).pack(side="left", padx=(0, 2))
        tk.Button(af, text="\U0001f6ab", command=self.callbacks["on_ignore"]).pack(side="left", padx=(0, 2))
        tk.Button(af, text="\u23ed\ufe0f", command=self.callbacks["on_skip"]).pack(side="left")

        tk.Label(self, text="Post Tags").pack(anchor="w", pady=(6, 0))

        tk.Button(self, text="Quit", width=10, command=lambda: sys.exit(0)).pack(side="bottom", anchor="w", pady=2)
        tk.Button(self, text="Edit Artists", command=self.callbacks["on_edit_artists"]).pack(
            side="bottom", anchor="w", pady=(0, 2), fill="x")
        tk.Button(self, text="Edit Tags", command=self.callbacks["on_edit_tags"]).pack(
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

    def get_tag_list_parent(self) -> tk.Frame:
        return self.tag_inner

    def reset_tag_list(self):
        for w in self.tag_inner.winfo_children():
            w.destroy()
        self.tag_canvas.yview_moveto(0)