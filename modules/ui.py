import sys
import logging
import threading
import queue
from io import BytesIO
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
from PIL import Image, ImageDraw, ImageTk

from modules.database import DatabaseManager
from modules.api import E621Client
from modules.components.thumbnail_gallery import ThumbnailGallery
from modules.components.sidebar import Sidebar

log = logging.getLogger(__name__)


class E621DiscoveryApp:
    """Single persistent window that updates in place for each post."""

    NUM_THUMBNAILS = 5
    IMG_MAX = (800, 600)
    THUMB_MAX = (100, 100)
    ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}

    def __init__(self, root: tk.Tk, db: DatabaseManager, client: E621Client):
        self.root = root
        self.root.title("e621 Discovery")
        self.root.geometry("1175x650+0+0")
        self.root.protocol("WM_DELETE_WINDOW", lambda: sys.exit(0))

        self.db = db
        self.client = client

        # Persistent session state
        self.followed_artists, self.ignored_artists = db.load_artists()
        self.banned_tags = db.load_banned_tags()
        self.current_tags = ""
        self.random_order = True
        self.page = 1
        self.post_buffer: list = []
        self._fetching = False
        self._fetch_gen = 0  # invalidated on search/random change

        # Per-post display state
        self.current_post: dict = {}
        self.current_img: Image.Image | None = None
        self._tk_img = None          # keeps main PhotoImage alive
        self._post_gen = 0           # incremented each time we start loading a new post

        # Thread-to-main queues (threads put plain Python data only)
        self._ui_q: queue.Queue = queue.Queue()       # callbacks from batch fetch
        self._image_q: queue.Queue = queue.Queue()    # (gen, post, PIL|None)
        self._swap_q: queue.Queue = queue.Queue()     # (gen, PIL|None, clicked_post, prev_post)
        self._bg_threads: list = []

        r, g, b = self.root.winfo_rgb(self.root.cget("bg"))
        self._bg_color = (r >> 8, g >> 8, b >> 8)
        self._ph_main, self._ph_thumb, self._ph_thumb_none = self._make_placeholders()
        self._build_ui()
        self._tag_font = tkfont.Font(family="TkDefaultFont", size=10)
        self._tag_strike_font = tkfont.Font(family="TkDefaultFont", size=10, overstrike=True)
        _tmp = tk.Label(self.root)
        self._tag_default_fg: str = _tmp.cget("fg")
        _tmp.destroy()
        self._tag_text_labels: dict = {}
        self.root.after(50, self._poll)
        self._advance()  # kick off the first post

    # ──────────────────────────────────────────────────── UI (built once)

    def _build_ui(self):
        self.root.rowconfigure(0, weight=1)

        callbacks = {
            "on_random_toggle": self._on_random_toggle,
            "on_search": self._perform_search,
            "on_follow": self._follow,
            "on_ignore": self._ignore,
            "on_skip": self._skip,
            "on_edit_artists": self._open_artist_editor,
            "on_edit_tags": self._open_tags_editor,
        }
        self.sidebar = Sidebar(self.root, callbacks)
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=10, pady=10)

        def _action_key(event, action):
            if not isinstance(event.widget, tk.Entry):
                action()

        self.root.bind("<f>", lambda e: _action_key(e, self._follow))
        self.root.bind("<F>", lambda e: _action_key(e, self._follow))
        self.root.bind("<i>", lambda e: _action_key(e, self._ignore))
        self.root.bind("<I>", lambda e: _action_key(e, self._ignore))
        self.root.bind("<s>", lambda e: _action_key(e, self._skip))
        self.root.bind("<S>", lambda e: _action_key(e, self._skip))

        self.thumbnail_gallery = ThumbnailGallery(
            self.root, self.client, self._ph_thumb, self._ph_thumb_none,
            self._swap_with_thumbnail, self._ui_q
        )
        self.thumbnail_gallery.grid(row=0, column=1, sticky="nw", padx=5, pady=10)

        self._img_label = tk.Label(self.root, width=800, height=600)
        self._img_label.grid(row=0, column=2, sticky="nw", padx=10, pady=10)

    # ──────────────────────────────────────────────────── helpers

    def _fit_image(self, pil: Image.Image) -> Image.Image:
        """Scale pil to fit within IMG_MAX maintaining aspect ratio, centered on bg canvas."""
        pil = pil.copy()
        pil.thumbnail(self.IMG_MAX, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", self.IMG_MAX, self._bg_color)
        canvas.paste(pil, ((self.IMG_MAX[0] - pil.width) // 2,
                           (self.IMG_MAX[1] - pil.height) // 2))
        return canvas

    def _make_placeholders(self):
        """Create and return (main_placeholder, thumb_placeholder) as PhotoImages."""
        bg = self._bg_color
        border = (150, 150, 150)
        text_color = (80, 80, 80)
        # 800x600 main placeholder with centred "Loading..." text and 1px border
        main = Image.new("RGB", (800, 600), bg)
        d = ImageDraw.Draw(main)
        d.rectangle([0, 0, 799, 599], outline=border)
        text = "Loading..."
        bb = d.textbbox((0, 0), text)
        d.text(((800 - (bb[2] - bb[0])) // 2, (600 - (bb[3] - bb[1])) // 2),
               text, fill=text_color)
        # 100x100 thumbnail placeholder — "Loading..."
        def _thumb_img(label_text):
            img = Image.new("RGB", (100, 100), bg)
            d2 = ImageDraw.Draw(img)
            d2.rectangle([0, 0, 99, 99], outline=border)
            bb2 = d2.textbbox((0, 0), label_text)
            d2.text(((100 - (bb2[2] - bb2[0])) // 2, (100 - (bb2[3] - bb2[1])) // 2),
                    label_text, fill=text_color)
            return ImageTk.PhotoImage(img)
        return ImageTk.PhotoImage(main), _thumb_img("Loading..."), _thumb_img("None")

    def _set_loading(self):
        self.sidebar.reset_artist()
        self._img_label.config(image=self._ph_main, text="")
        self.sidebar.reset_tag_list()
        self.thumbnail_gallery.reset()

    def _build_tag_list(self, post_data: dict):
        self._tag_text_labels = {}
        self.sidebar.reset_tag_list()
        tag_inner = self.sidebar.get_tag_list_parent()
        tags = sorted(t for ts in post_data.get("tags", {}).values() for t in ts)
        banned_set = set(self.banned_tags)
        for tag in tags:
            row = tk.Frame(tag_inner)
            row.pack(fill="x", anchor="w", pady=0, ipady=0)
            search_lbl = tk.Label(row, text="\U0001f50d", cursor="pointinghand",
                                   font=("TkDefaultFont", 7), pady=0)
            search_lbl.pack(side="left", padx=(0, 1), pady=0)
            search_lbl.bind("<Button-1>", lambda e, t=tag: self._add_tag_to_search(t))

            ban_lbl = tk.Label(row, text="\U0001f6ab", cursor="pointinghand",
                               font=("TkDefaultFont", 7), pady=0)
            ban_lbl.pack(side="left", padx=(0, 3), pady=0)
            ban_lbl.bind("<Button-1>", lambda e, t=tag: self._ban_tag(t))

            is_banned = tag in banned_set
            lbl = tk.Label(row, text=tag, anchor="w", pady=0,
                           font=self._tag_strike_font if is_banned else self._tag_font)
            if is_banned:
                lbl.config(fg="grey")
            lbl.pack(side="left", pady=0)

            self._tag_text_labels[tag] = lbl
        self.sidebar.tag_canvas.configure(scrollregion=self.sidebar.tag_canvas.bbox("all"))

    def _add_tag_to_search(self, tag: str):
        existing = self.sidebar.get_search_query().strip().split()
        if tag not in existing:
            existing.append(tag)
        self.sidebar.set_search_query(" ".join(existing))
        self._perform_search()

    def _ban_tag(self, tag: str):
        lbl = self._tag_text_labels.get(tag)
        if tag in self.banned_tags:
            # Unban
            if self.db.remove_banned_tag(tag):
                self.banned_tags.remove(tag)
                if lbl:
                    lbl.config(font=self._tag_font, fg=self._tag_default_fg)
        else:
            # Ban
            if self.db.add_banned_tag(tag):
                self.banned_tags.append(tag)
                if lbl:
                    lbl.config(font=self._tag_strike_font, fg="grey")

    def _artist(self) -> str:
        return (self.current_post.get("tags", {}).get("artist") or ["Unknown"])[0]

    # ──────────────────────────────────────────────────── post navigation

    def _invalidate_search(self):
        """Discard in-flight batch fetches and reset pagination."""
        self._fetch_gen += 1
        self._fetching = False
        self.post_buffer.clear()
        self.page = 1

    def _download_image(self, url: str, post: dict, gen: int) -> None:
        """Background thread: download and enqueue a post image."""
        try:
            r = self.client.download(url, timeout=30)
            if r.status_code != 200:
                self._image_q.put((gen, post, None))
                return
            pil = Image.open(BytesIO(r.content))
            pil.thumbnail(self.IMG_MAX, Image.Resampling.LANCZOS)
            self._image_q.put((gen, post, pil))
        except Exception as ex:
            log.warning("Image download failed: %s", ex)
            self._image_q.put((gen, post, None))

    def _advance(self):
        """Pop the next valid post from the buffer and start loading its image."""
        while self.post_buffer:
            post = self.post_buffer.pop(0)
            artist = (post.get("tags", {}).get("artist") or ["Unknown"])[0]
            if artist in self.followed_artists or artist in self.ignored_artists:
                log.info("Skipping %s — artist '%s' followed/ignored", post.get("id"), artist)
                continue
            post_tags = {t for ts in post.get("tags", {}).values() for t in ts}
            hit = post_tags & set(self.banned_tags)
            if hit:
                log.info("Skipping %s — banned: %s", post.get("id"), ", ".join(hit))
                continue
            url = post.get("file", {}).get("url")
            ext = post.get("file", {}).get("ext", "")
            if not url or ext not in self.ALLOWED_EXTENSIONS:
                continue
            # Valid post — start image download
            self._post_gen += 1
            gen = self._post_gen
            self.current_post = post
            self._set_loading()
            if len(self.post_buffer) < 5:
                self._fetch_batch()
            t = threading.Thread(
                target=self._download_image, args=(url, post, gen), daemon=True)
            self._bg_threads.append(t)
            t.start()
            return
        # Buffer empty — fetch a page then retry
        self._fetch_batch(callback=self._advance)

    def _fetch_batch(self, callback=None):
        """Fetch a page of posts in a background thread."""
        if self._fetching:
            return
        self._fetching = True
        fgen = self._fetch_gen
        tags, page, rand = self.current_tags, self.page, self.random_order
        self.page += 1
 
        def fetch_posts_thread(current_tags, current_page, random_order, fetch_gen, cb):
            posts = self.client.fetch_posts(tags=current_tags, page=current_page, random_order=random_order)
            def _on_main():
                if fetch_gen != self._fetch_gen:
                    return  # search changed while fetch was in flight
                self._fetching = False
                self.post_buffer.extend(posts)
                if cb:
                    cb()
            self._ui_q.put(_on_main)

        t = threading.Thread(
            target=fetch_posts_thread, args=(tags, page, rand, fgen, callback), daemon=True
        )
        self._bg_threads.append(t)
        t.start()

    # ──────────────────────────────────────────────────── user actions

    def _follow(self):
        artist = self._artist()
        if artist not in self.followed_artists:
            if self.db.add_followed_artist(artist):
                self.followed_artists.append(artist)
        log.info("Followed artist '%s'", artist)
        self._advance()

    def _ignore(self):
        artist = self._artist()
        if artist not in self.ignored_artists:
            if self.db.add_ignored_artist(artist):
                self.ignored_artists.append(artist)
        log.info("Ignored artist '%s'", artist)
        self._advance()

    def _skip(self):
        log.info("Skipped artist '%s'", self._artist())
        self._advance()

    def _on_random_toggle(self):
        self.random_order = not self.random_order
        self._invalidate_search()
        self._advance()

    def _perform_search(self):
        query = self.sidebar.get_search_query().strip()
        tags = query.split() if query else []
        if tags:
            try:
                invalid = []
                for tag in tags:
                    resp = self.client.api_get(self.client.TAGS_URL,
                                              params={"search[name]": tag.lstrip("-")})
                    if resp.status_code == 200:
                        if not resp.json():
                            invalid.append(tag)
                    else:
                        messagebox.showerror("API Error", f"HTTP {resp.status_code}")
                        return
                if invalid:
                    messagebox.showinfo("Search", f"No tags found: {', '.join(invalid)}")
                    return
            except Exception as ex:
                messagebox.showerror("Error", str(ex))
                return
        self.current_tags = " ".join(tags)
        self._invalidate_search()
        self._advance()

    def _open_tags_editor(self):
        editor = tk.Toplevel(self.root)
        editor.title("Edit Tags")
        editor.geometry("300x500")
        editor.transient(self.root)
        editor.grab_set()

        tk.Label(editor, text="Edit Tags", font=tkfont.Font(family="TkDefaultFont", weight="bold")).pack(pady=(5, 10))

        list_frame = tk.Frame(editor)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)
        canvas = tk.Canvas(list_frame, highlightthickness=0)
        sb = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner_frame = tk.Frame(canvas)
        canvas.create_window((0, 0), window=inner_frame, anchor="nw")
        inner_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _on_modal_mousewheel(event):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        inner_frame.bind("<MouseWheel>", _on_modal_mousewheel)
        canvas.bind("<MouseWheel>", _on_modal_mousewheel)

        modal_tag_labels = {}
        changes_made = False

        def _toggle_tag_ban(tag: str):
            nonlocal changes_made
            label = modal_tag_labels[tag]
            if tag in self.banned_tags:
                # Unban
                if self.db.remove_banned_tag(tag):
                    try: self.banned_tags.remove(tag)
                    except ValueError: pass
                    label.config(font=self._tag_font, fg=self._tag_default_fg)
                    changes_made = True
            else:
                # Ban
                if self.db.add_banned_tag(tag):
                    self.banned_tags.append(tag)
                    label.config(font=self._tag_strike_font, fg="grey")
                    changes_made = True

        for tag in sorted(self.banned_tags):
            row = tk.Frame(inner_frame)
            row.pack(fill="x", anchor="w")

            ban_icon = tk.Label(row, text="\U0001f6ab", cursor="pointinghand", font=("TkDefaultFont", 7))
            ban_icon.pack(side="left", padx=(0, 3))
            ban_icon.bind("<Button-1>", lambda e, t=tag: _toggle_tag_ban(t))

            tag_label = tk.Label(row, text=tag, anchor="w", font=self._tag_strike_font, fg="grey")
            tag_label.pack(side="left")
            modal_tag_labels[tag] = tag_label

            for widget in (row, ban_icon, tag_label):
                widget.bind("<MouseWheel>", _on_modal_mousewheel)

        def _on_close():
            editor.grab_release()
            if changes_made:
                if self.current_post:
                    self._build_tag_list(self.current_post)
            editor.destroy()

        tk.Button(editor, text="Close", command=_on_close).pack(pady=10)
        editor.protocol("WM_DELETE_WINDOW", _on_close)

    def _open_artist_editor(self):
        editor = tk.Toplevel(self.root)
        editor.title("Edit Artists")
        editor.geometry("300x500")
        editor.transient(self.root)
        editor.grab_set()

        tk.Label(editor, text="Edit Artists", font=tkfont.Font(family="TkDefaultFont", weight="bold")).pack(pady=(5, 10))

        list_frame = tk.Frame(editor)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)
        canvas = tk.Canvas(list_frame, highlightthickness=0)
        sb = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner_frame = tk.Frame(canvas)
        canvas.create_window((0, 0), window=inner_frame, anchor="nw")
        inner_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _on_modal_mousewheel(event):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        inner_frame.bind("<MouseWheel>", _on_modal_mousewheel)
        canvas.bind("<MouseWheel>", _on_modal_mousewheel)

        modal_artist_labels = {}

        def _toggle_artist_status(artist: str):
            label = modal_artist_labels[artist]
            is_followed = artist in self.followed_artists
            is_ignored = artist in self.ignored_artists

            if is_followed:  # Followed -> Ignored
                if self.db.remove_followed_artist(artist):
                    try: self.followed_artists.remove(artist)
                    except ValueError: pass
                    if self.db.add_ignored_artist(artist):
                        self.ignored_artists.append(artist)
                        label.config(font=self._tag_strike_font, fg="grey")
            elif is_ignored:  # Ignored -> Neither
                if self.db.remove_ignored_artist(artist):
                    try: self.ignored_artists.remove(artist)
                    except ValueError: pass
                    label.config(font=self._tag_font, fg=self._tag_default_fg)
            else:  # Neither -> Followed
                if self.db.add_followed_artist(artist):
                    self.followed_artists.append(artist)
                    label.config(font=self._tag_font, fg="green")

        all_artists = sorted(list(set(self.followed_artists) | set(self.ignored_artists)))
        for artist in all_artists:
            row = tk.Frame(inner_frame)
            row.pack(fill="x", anchor="w")
            toggle_icon = tk.Label(row, text="\u267b", cursor="pointinghand", font=("TkDefaultFont", 9))
            toggle_icon.pack(side="left", padx=(0, 3))
            toggle_icon.bind("<Button-1>", lambda e, a=artist: _toggle_artist_status(a))

            font, fg = self._tag_font, self._tag_default_fg
            if artist in self.ignored_artists: font, fg = self._tag_strike_font, "grey"
            elif artist in self.followed_artists: fg = "green"
            artist_label = tk.Label(row, text=artist, anchor="w", font=font, fg=fg)
            artist_label.pack(side="left")
            modal_artist_labels[artist] = artist_label

            for widget in (row, toggle_icon, artist_label):
                widget.bind("<MouseWheel>", _on_modal_mousewheel)

        def _on_close():
            editor.grab_release()
            editor.destroy()

        tk.Button(editor, text="Close", command=_on_close).pack(pady=10)
        editor.protocol("WM_DELETE_WINDOW", _on_close)

    def _swap_with_thumbnail(self, slot_idx: int):
        clicked = self.thumbnail_gallery.thumb_post_map[slot_idx]
        if clicked is None:
            return
        url = clicked.get("file", {}).get("url")
        if not url:
            return

        self.thumbnail_gallery.disable_clicks()

        # Move current main image → thumbnail slot
        if self.current_img is not None:
            self.thumbnail_gallery.update_slot(slot_idx, self.current_img, self.current_post)

        prev_post = self.current_post
        self.current_post = clicked
        self._img_label.config(image=self._ph_main, text="")
        self._build_tag_list({})
        gen = self._post_gen

        def swap_thread(u, cp, pp, swap_gen):
            try:
                r = self.client.download(u, timeout=30)
                if r.status_code != 200:
                    self._swap_q.put((swap_gen, None, cp, pp))
                    return
                pil = Image.open(BytesIO(r.content))
                pil.thumbnail(self.IMG_MAX, Image.Resampling.LANCZOS)
                self._swap_q.put((swap_gen, pil, cp, pp))
            except Exception as ex:
                log.warning("Swap failed: %s", ex)
                self._swap_q.put((swap_gen, None, cp, pp))

        t = threading.Thread(
            target=swap_thread, args=(url, clicked, prev_post, gen), daemon=True
        )
        self._bg_threads.append(t)
        t.start()

    # ──────────────────────────────────────────────────── polling loop

    def _poll(self):
        self._bg_threads = [t for t in self._bg_threads if t.is_alive()]

        # Batch-fetch callbacks (capped to avoid blocking the UI thread)
        try:
            for _ in range(10):
                cb = self._ui_q.get_nowait()
                try:
                    cb()
                except Exception as ex:
                    log.warning("UI callback error: %s", ex)
        except queue.Empty:
            pass

        # Image download results
        try:
            for _ in range(10):
                g, post, pil = self._image_q.get_nowait()
                if g != self._post_gen:
                    continue
                if pil is None:
                    log.warning("Image failed; advancing to next post")
                    self.root.after(300, self._advance)
                    continue
                artist = (post.get("tags", {}).get("artist") or ["Unknown"])[0]
                self.sidebar.update_artist(artist)
                fitted = self._fit_image(pil)
                tk_img = ImageTk.PhotoImage(fitted)
                self._img_label.config(image=tk_img, text="")
                self._tk_img = tk_img
                self.current_img = pil  # store original (unpadded) for thumbnail swaps
                self.current_post = post
                self._build_tag_list(post)
                self.thumbnail_gallery.start_load(artist, post.get("id"), self.banned_tags)
        except queue.Empty:
            pass

        self.thumbnail_gallery.process_queue_events()

        # Swap results
        try:
            for _ in range(10):
                swap_gen, pil, cp, pp = self._swap_q.get_nowait()
                if swap_gen != self._post_gen:
                    continue
                if pil is not None:
                    fitted = self._fit_image(pil)
                    tk_img = ImageTk.PhotoImage(fitted)
                    self._img_label.config(image=tk_img, text="")
                    self._tk_img = tk_img
                    self.current_img = pil  # store original (unpadded) for thumbnail swaps
                    self._build_tag_list(cp)
                else:
                    self.current_post = pp

                self.thumbnail_gallery.enable_clicks()
        except queue.Empty:
            pass

        self.root.after(50, self._poll)