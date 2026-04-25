import sys
import logging
import threading
import queue
from io import BytesIO
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
from PIL import Image, ImageTk

from modules.database import DatabaseManager
from modules.api import E621Client
from modules.components.thumbnail_gallery import ThumbnailGallery
from modules.components.sidebar import Sidebar
from modules.components.main_image import MainImage
from modules.components.modals import TagsEditorModal, ArtistEditorModal
from modules.engine import DiscoveryEngine
import modules.image_utils as image_utils

log = logging.getLogger(__name__)


class E621DiscoveryApp:
    """Single persistent window that updates in place for each post."""

    def __init__(self, root: tk.Tk, engine: DiscoveryEngine):
        self.root = root
        self.engine = engine
        self._is_swapping = False
        self.current_img: Image.Image | None = None # UI needs to remember the main PIL image for thumbnail scaling
        
        # UI Styles & Dynamic Colors
        self._bg_color: tuple[int, int, int]
        self._tag_font = tkfont.Font(family="TkDefaultFont", size=10)
        self._tag_strike_font = tkfont.Font(family="TkDefaultFont", size=10, overstrike=True)
        _tmp = tk.Label(self.root)
        self._tag_default_fg = _tmp.cget("fg")
        try:
            # Get background color dynamically and convert from 16-bit to 8-bit per channel
            r, g, b = self.root.winfo_rgb(_tmp.cget("bg"))
            self._bg_color = (r // 256, g // 256, b // 256)
        except tk.TclError:
            self._bg_color = (240, 240, 240) # Fallback for headless or odd environments
        _tmp.destroy()

        # Wire up the engine's callbacks to our UI methods
        self.engine.on_loading = self._set_loading

        self._build_ui()
        self.root.after(50, self._poll)
        self.engine.advance() # Kick it off

    def _build_ui(self):
        self.root.rowconfigure(0, weight=1)

        callbacks = {
            "on_random_toggle": self._on_random_toggle,
            "on_search": self._perform_search,
            "on_follow": self.engine.follow,   # Route directly to engine!
            "on_ignore": self.engine.ignore,   # Route directly to engine!
            "on_skip": self.engine.skip,       # Route directly to engine!
            "on_edit_artists": self._open_artist_editor,
            "on_edit_tags": self._open_tags_editor,
            "on_tag_search": self._add_tag_to_search,
            "on_tag_ban": self._ban_tag,
        }
        fonts = {
            "normal": self._tag_font,
            "strike": self._tag_strike_font,
            "default_fg": self._tag_default_fg,
        }

        self.sidebar = Sidebar(self.root, callbacks, fonts)
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=10, pady=10)

        # Keyboard shortcuts mapped straight to engine
        self.root.bind("<f>", lambda e: self.engine.follow() if not isinstance(e.widget, tk.Entry) else None)
        self.root.bind("<i>", lambda e: self.engine.ignore() if not isinstance(e.widget, tk.Entry) else None)
        self.root.bind("<s>", lambda e: self.engine.skip() if not isinstance(e.widget, tk.Entry) else None)

        # Need the API client for thumbnails
        self.thumbnail_gallery = ThumbnailGallery(
            self.root, self.engine.client,
            self._swap_with_thumbnail, self.engine.ui_q
        )
        self.thumbnail_gallery.grid(row=0, column=1, sticky="nw", padx=5, pady=10)

        self.main_image = MainImage(self.root)
        self.main_image.grid(row=0, column=2, sticky="nw", padx=10, pady=10)

    # ──────────────────────────────────────────────────── Engine Callbacks & Renders

    def _set_loading(self):
        self.root.config(cursor="watch")
        self.sidebar.reset_artist()
        self.main_image.set_loading()
        self.sidebar.reset_tag_list()
        self.thumbnail_gallery.reset()
        self.sidebar.set_controls_state("disabled")
 
    def _render_new_post(self, pil_img, post):
        """Fired when the engine successfully downloads a new image."""
        self.current_img = pil_img
        artist = (post.get("tags", {}).get("artist") or ["Unknown"])[0]
        
        self.sidebar.update_artist(artist)
        
        fitted = image_utils.fit_image(pil_img, (800, 600), self._bg_color)
        tk_img = ImageTk.PhotoImage(fitted)
        self.main_image.set_image(tk_img)

        tags = sorted(t for ts in post.get("tags", {}).values() for t in ts)
        self.sidebar.render_tags(tags, set(self.engine.banned_tags))
        
        self.thumbnail_gallery.start_load(artist, post.get("id"), self.engine.banned_tags)

        self.root.config(cursor="")
        self.sidebar.set_controls_state("normal")

    def _render_swap(self, pil_img, clicked_post, prev_post):
        """Fired when the engine finishes downloading a thumbnail swap."""
        self.current_img = pil_img
        fitted = image_utils.fit_image(pil_img, (800, 600), self._bg_color)
        tk_img = ImageTk.PhotoImage(fitted)
        self.main_image.set_image(tk_img)
        
        tags = sorted(t for ts in clicked_post.get("tags", {}).values() for t in ts)
        self.sidebar.render_tags(tags, set(self.engine.banned_tags))
        self.sidebar.set_controls_state("normal")
        self._end_swap()

    def _render_swap_fail(self, clicked_post, prev_post):
        """Fired if a thumbnail swap download fails."""
        self.engine.current_post = prev_post
        self._end_swap()
        self.sidebar.set_controls_state("normal")

    def _end_swap(self):
        self.root.config(cursor="")
        self.thumbnail_gallery.enable_clicks()
        self._is_swapping = False

    # ──────────────────────────────────────────────────── UI Logic

    def _add_tag_to_search(self, tag: str):
        existing = self.sidebar.get_search_query().strip().split()
        if tag not in existing:
            existing.append(tag)
        self.sidebar.set_search_query(" ".join(existing))
        self._perform_search()

    def _ban_tag(self, tag: str):
        if tag in self.engine.banned_tags:
            if self.engine.db.remove_banned_tag(tag):
                self.engine.banned_tags.remove(tag)
                self.sidebar.update_tag_style(tag, is_banned=False)
        else:
            if self.engine.db.add_banned_tag(tag):
                self.engine.banned_tags.append(tag)
                self.sidebar.update_tag_style(tag, is_banned=True)

    def _on_random_toggle(self):
        self.engine.random_order = not self.engine.random_order
        self.engine.invalidate_search()
        self.engine.advance()

    def _perform_search(self):
        query = self.sidebar.get_search_query().strip()
        tags = query.split() if query else []
        self.engine.current_tags = " ".join(tags)
        self.engine.invalidate_search()
        self.engine.advance()

    def _open_tags_editor(self):
        fonts = {"normal": self._tag_font, "strike": self._tag_strike_font, "default_fg": self._tag_default_fg}
        TagsEditorModal(self.root, self.engine.db, self.engine.banned_tags, fonts, self._on_tags_updated)

    def _open_artist_editor(self):
        fonts = {"normal": self._tag_font, "strike": self._tag_strike_font, "default_fg": self._tag_default_fg}
        ArtistEditorModal(self.root, self.engine.db, self.engine.followed_artists, self.engine.ignored_artists, fonts)

    def _on_tags_updated(self):
        if self.engine.current_post:
            tags = sorted(t for ts in self.engine.current_post.get("tags", {}).values() for t in ts)
            self.sidebar.render_tags(tags, set(self.engine.banned_tags))

    def _swap_with_thumbnail(self, slot_idx: int):
        clicked = self.thumbnail_gallery.thumb_post_map[slot_idx]
        if clicked is None or self._is_swapping: return
        url = clicked.get("file", {}).get("url")
        if not url: return

        self._is_swapping = True
        self.root.config(cursor="watch")
        self.root.update_idletasks()

        if self.current_img is not None:
            self.thumbnail_gallery.update_slot(slot_idx, self.current_img, self.engine.current_post)

        self.thumbnail_gallery.disable_clicks()
        self.sidebar.set_controls_state("disabled")
        prev_post = self.engine.current_post
        self.main_image.set_loading()
        self.sidebar.render_tags([], set())
        
        # Pass the background download work to the engine
        self.engine.start_swap(url, clicked, prev_post)

    # ──────────────────────────────────────────────────── polling loop

    def _poll(self):
        try:
            for _ in range(10):
                cb = self.engine.ui_q.get_nowait()
                cb()
        except queue.Empty: pass

        try:
            for _ in range(10):
                g, post, pil = self.engine.image_q.get_nowait()
                if g == self.engine.post_gen:
                    if pil:
                        self._render_new_post(pil, post)
                    else:
                        self.engine.advance()
        except queue.Empty: pass

        try:
            for _ in range(10):
                g, pil, cp, pp = self.engine.swap_q.get_nowait()
                if g == self.engine.post_gen:
                    if pil:
                        self._render_swap(pil, cp, pp)
                    else:
                        self._render_swap_fail(cp, pp)
        except queue.Empty: pass

        self.thumbnail_gallery.process_queue_events()
        self.root.after(50, self._poll)