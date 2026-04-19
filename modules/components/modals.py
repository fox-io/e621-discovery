import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from modules.database import DatabaseManager


class BaseEditorModal(tk.Toplevel):
    """Base class for a modal window with a scrollable list."""

    def __init__(self, master, title: str):
        super().__init__(master)
        self.title(title)
        self.geometry("300x500")
        self.transient(master)
        self.grab_set()

        self._build_base_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_base_ui(self):
        tk.Label(self, text=self.title(), font=tkfont.Font(family="TkDefaultFont", weight="bold")).pack(pady=(5, 10))

        list_frame = tk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(list_frame, highlightthickness=0)
        sb = tk.Scrollbar(list_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner_frame = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")
        self.inner_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        self.inner_frame.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

        tk.Button(self, text="Close", command=self._on_close).pack(pady=10)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_close(self):
        self.grab_release()
        self.destroy()


class TagsEditorModal(BaseEditorModal):
    """A modal window for editing banned tags."""

    def __init__(self, master, db: DatabaseManager, banned_tags: list, fonts: dict, on_close_callback: Callable):
        super().__init__(master, "Edit Tags")
        self.db = db
        self.banned_tags = banned_tags
        self.fonts = fonts
        self.on_close_callback = on_close_callback
        self.changes_made = False
        self.modal_tag_labels = {}

        self._build_content()

    def _build_content(self):
        for tag in sorted(self.banned_tags):
            row = tk.Frame(self.inner_frame)
            row.pack(fill="x", anchor="w")

            ban_icon = tk.Label(row, text="\U0001f6ab", cursor="pointinghand", font=("TkDefaultFont", 7))
            ban_icon.pack(side="left", padx=(0, 3))
            ban_icon.bind("<Button-1>", lambda e, t=tag: self._toggle_tag_ban(t))

            tag_label = tk.Label(row, text=tag, anchor="w", font=self.fonts["strike"], fg="grey")
            tag_label.pack(side="left")
            self.modal_tag_labels[tag] = tag_label

            for widget in (row, ban_icon, tag_label):
                widget.bind("<MouseWheel>", self._on_mousewheel)

    def _toggle_tag_ban(self, tag: str):
        label = self.modal_tag_labels[tag]
        if tag in self.banned_tags:
            # Unban
            if self.db.remove_banned_tag(tag):
                try:
                    self.banned_tags.remove(tag)
                except ValueError:
                    pass
                label.config(font=self.fonts["normal"], fg=self.fonts["default_fg"])
                self.changes_made = True
        else:
            # Ban
            if self.db.add_banned_tag(tag):
                self.banned_tags.append(tag)
                label.config(font=self.fonts["strike"], fg="grey")
                self.changes_made = True

    def _on_close(self):
        self.grab_release()
        if self.changes_made:
            self.on_close_callback()
        self.destroy()


class ArtistEditorModal(BaseEditorModal):
    """A modal window for editing followed and ignored artists."""

    def __init__(self, master, db: DatabaseManager, followed_artists: list, ignored_artists: list, fonts: dict):
        super().__init__(master, "Edit Artists")
        self.db = db
        self.followed_artists = followed_artists
        self.ignored_artists = ignored_artists
        self.fonts = fonts
        self.modal_artist_labels = {}

        self._build_content()

    def _build_content(self):
        all_artists = sorted(list(set(self.followed_artists) | set(self.ignored_artists)))
        for artist in all_artists:
            row = tk.Frame(self.inner_frame)
            row.pack(fill="x", anchor="w")
            toggle_icon = tk.Label(row, text="\u267b", cursor="pointinghand", font=("TkDefaultFont", 9))
            toggle_icon.pack(side="left", padx=(0, 3))
            toggle_icon.bind("<Button-1>", lambda e, a=artist: self._toggle_artist_status(a))

            font, fg = self.fonts["normal"], self.fonts["default_fg"]
            if artist in self.ignored_artists:
                font, fg = self.fonts["strike"], "grey"
            elif artist in self.followed_artists:
                fg = "green"
            artist_label = tk.Label(row, text=artist, anchor="w", font=font, fg=fg)
            artist_label.pack(side="left")
            self.modal_artist_labels[artist] = artist_label

            for widget in (row, toggle_icon, artist_label):
                widget.bind("<MouseWheel>", self._on_mousewheel)

    def _toggle_artist_status(self, artist: str):
        label = self.modal_artist_labels[artist]
        is_followed = artist in self.followed_artists
        is_ignored = artist in self.ignored_artists

        if is_followed:  # Followed -> Ignored
            if self.db.remove_followed_artist(artist):
                try:
                    self.followed_artists.remove(artist)
                except ValueError:
                    pass
                if self.db.add_ignored_artist(artist):
                    self.ignored_artists.append(artist)
                    label.config(font=self.fonts["strike"], fg="grey")
        elif is_ignored:  # Ignored -> Neither
            if self.db.remove_ignored_artist(artist):
                try:
                    self.ignored_artists.remove(artist)
                except ValueError:
                    pass
                label.config(font=self.fonts["normal"], fg=self.fonts["default_fg"])
        else:  # Neither -> Followed
            if self.db.add_followed_artist(artist):
                self.followed_artists.append(artist)
                label.config(font=self.fonts["normal"], fg="green")
