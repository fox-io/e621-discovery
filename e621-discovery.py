import requests
import sqlite3
import os
import time
import sys
import logging
import atexit
import threading
import queue
import json
import gc
from contextlib import closing
from datetime import datetime, timezone
from PIL import Image, ImageDraw
from io import BytesIO
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
from PIL import ImageTk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
try:
    with open(_config_path) as _f:
        _config = json.load(_f)
except FileNotFoundError:
    raise SystemExit("config.json not found. Copy config.json.example to config.json and fill in your e621 username.")
_e621_username = _config.get("e621_username", "")
if not _e621_username or _e621_username == "<your_username>":
    raise SystemExit("Set e621_username in config.json before running.")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e621-discovery.sqlite3")


class DatabaseManager:
    """Manages all SQLite database operations."""

    def __init__(self, path: str):
        self._path = path

    def _connect(self):
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init(self):
        log.info("Initializing database at %s", self._path)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS followed_artists (
                    tag TEXT UNIQUE NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS ignored_artists (
                    tag TEXT UNIQUE NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS banned_tags (
                    tag TEXT UNIQUE NOT NULL,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
                )""")
        log.info("Database initialized successfully")

    def load_artists(self):
        log.info("Loading artists from database")
        with closing(self._connect()) as conn:
            followed = [row[0] for row in conn.execute("SELECT tag FROM followed_artists").fetchall()]
            ignored = [row[0] for row in conn.execute("SELECT tag FROM ignored_artists").fetchall()]
        log.info("Loaded %d followed and %d ignored artists", len(followed), len(ignored))
        return followed, ignored

    def load_banned_tags(self):
        with closing(self._connect()) as conn:
            tags = [row[0] for row in conn.execute("SELECT tag FROM banned_tags").fetchall()]
        log.info("Loaded %d banned tags", len(tags))
        return tags

    def add_followed_artist(self, artist) -> bool:
        try:
            now = datetime.now(timezone.utc).isoformat()
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO followed_artists (tag, timestamp) VALUES (?, ?)",
                        (artist, now))
            log.info("DB write: added '%s' to followed_artists", artist)
            return True
        except Exception as e:
            log.error("DB error adding followed artist '%s': %s", artist, e)
            return False

    def add_ignored_artist(self, artist) -> bool:
        try:
            now = datetime.now(timezone.utc).isoformat()
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO ignored_artists (tag, timestamp) VALUES (?, ?)",
                        (artist, now))
            log.info("DB write: added '%s' to ignored_artists", artist)
            return True
        except Exception as e:
            log.error("DB error adding ignored artist '%s': %s", artist, e)
            return False

    def add_banned_tag(self, tag) -> bool:
        try:
            now = datetime.now(timezone.utc).isoformat()
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO banned_tags (tag, timestamp) VALUES (?, ?)",
                        (tag, now))
            log.info("DB write: added '%s' to banned_tags", tag)
            return True
        except Exception as e:
            log.error("DB error adding banned tag '%s': %s", tag, e)
            return False

    def remove_banned_tag(self, tag) -> bool:
        try:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute("DELETE FROM banned_tags WHERE tag = ?", (tag,))
            log.info("DB write: removed '%s' from banned_tags", tag)
            return True
        except Exception as e:
            log.error("DB error removing banned tag '%s': %s", tag, e)
            return False

    def get_followed_since(self, since: str) -> list:
        with closing(self._connect()) as conn:
            return [row[0] for row in conn.execute(
                "SELECT tag FROM followed_artists WHERE timestamp >= ? ORDER BY timestamp",
                (since,)
            ).fetchall()]


class E621Client:
    """Handles all e621 API communication with rate limiting and connection pooling."""

    API_URL = "https://e621.net/posts.json"
    TAGS_URL = "https://e621.net/tags.json"

    def __init__(self, username: str):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": f"e621 Discovery Script by {username}"})
        self._last_request = 0.0

    def api_get(self, url: str, stop_event=None, **kwargs) -> requests.Response:
        """Rate-limited GET for e621 API endpoints (1 req/s)."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < 1.0:
            remaining = 1.0 - elapsed
            if stop_event:
                if stop_event.wait(timeout=remaining):
                    raise InterruptedError("api_get aborted via stop_event")
            else:
                time.sleep(remaining)
        if stop_event and stop_event.is_set():
            raise InterruptedError("api_get aborted via stop_event")
        self._last_request = time.monotonic()
        return self._session.get(url, **kwargs)

    def download(self, url: str, **kwargs) -> requests.Response:
        """Non-rate-limited GET for CDN image/thumbnail downloads."""
        return self._session.get(url, **kwargs)

    def fetch_posts(self, tags: str = "", page: int = 1, random_order: bool = True) -> list:
        if random_order:
            combined = ("order:random " + tags).strip() if tags else "order:random"
        else:
            combined = tags
        log.info("Fetching posts (tags=%r, page=%d, random=%s)", combined, page, random_order)
        resp = self.api_get(self.API_URL, params={"tags": combined, "page": page})
        if resp.status_code == 200:
            posts = resp.json().get("posts", [])
            log.info("Received %d posts", len(posts))
            return posts
        log.error("Error fetching posts: HTTP %d", resp.status_code)
        return []
# ──────────────────────────────────────────────────────────────────────────────
# Single persistent window
# ──────────────────────────────────────────────────────────────────────────────
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
        self.thumb_images: list = [] # keeps thumbnail PhotoImages alive
        self.thumb_post_map: list = [None] * self.NUM_THUMBNAILS
        self._post_gen = 0           # incremented each time we start loading a new post
        self._thumb_candidates: list = []  # all filtered candidates for current artist
        self._thumb_page: int = 0          # current thumbnail page (0-indexed)
        self._thumb_load_id: int = 0       # invalidated on every new load or page change

        # Thread-to-main queues (threads put plain Python data only)
        self._ui_q: queue.Queue = queue.Queue()       # callbacks from batch fetch
        self._image_q: queue.Queue = queue.Queue()    # (gen, post, PIL|None)
        self._thumb_q: queue.Queue = queue.Queue()    # (gen, kind, slot, post|None, PIL|None)
        self._swap_q: queue.Queue = queue.Queue()     # (gen, PIL|None, clicked_post, prev_post)
        self._bg_threads: list = []

        self._build_ui()
        self._tag_font = tkfont.Font(family="TkDefaultFont", size=10)
        self._tag_strike_font = tkfont.Font(family="TkDefaultFont", size=10, overstrike=True)
        _tmp = tk.Label(self.root)
        self._tag_default_fg: str = _tmp.cget("fg")
        _tmp.destroy()
        self._tag_text_labels: dict = {}
        r, g, b = self.root.winfo_rgb(self.root.cget("bg"))
        self._bg_color = (r >> 8, g >> 8, b >> 8)
        self._ph_main, self._ph_thumb, self._ph_thumb_none = self._make_placeholders()
        self.root.after(50, self._poll)
        self._advance()  # kick off the first post

    # ──────────────────────────────────────────────────── UI (built once)

    def _build_ui(self):
        self.root.rowconfigure(0, weight=1)
        left = tk.Frame(self.root)
        left.grid(row=0, column=0, sticky="nsw", padx=10, pady=10)

        self._random_state = [True]
        self._random_cb = tk.Checkbutton(left, text="Random order",
                                         command=self._on_random_toggle)
        self._random_cb.select()
        self._random_cb.pack(anchor="w", pady=(0, 5))

        sf = tk.Frame(left)
        sf.pack(anchor="w", pady=(0, 10))
        self._search_entry = tk.Entry(sf, width=15)
        self._search_entry.bind("<Return>", lambda e: self._perform_search())
        self._search_entry.pack(side="left", padx=(0, 5))
        tk.Button(sf, text="\U0001f50d", command=self._perform_search).pack(side="left")

        self._artist_label = tk.Label(left, text="Artist: \u2014")
        self._artist_label.pack(anchor="w")

        af = tk.Frame(left)
        af.pack(anchor="center", pady=2)
        tk.Button(af, text="\u2764\ufe0f", command=self._follow).pack(side="left", padx=(0, 2))
        tk.Button(af, text="\U0001f6ab", command=self._ignore).pack(side="left", padx=(0, 2))
        tk.Button(af, text="\u23ed\ufe0f", command=self._skip).pack(side="left")

        def _action_key(event, action):
            if not isinstance(event.widget, tk.Entry):
                action()
        
        self.root.bind("<f>", lambda e: _action_key(e, self._follow))
        self.root.bind("<F>", lambda e: _action_key(e, self._follow))
        self.root.bind("<i>", lambda e: _action_key(e, self._ignore))
        self.root.bind("<I>", lambda e: _action_key(e, self._ignore))
        self.root.bind("<s>", lambda e: _action_key(e, self._skip))
        self.root.bind("<S>", lambda e: _action_key(e, self._skip))

        tk.Label(left, text="Post Tags").pack(anchor="w", pady=(6, 0))
        # Quit pinned to bottom before the expanding tag frame
        tk.Button(left, text="Quit", width=10, command=lambda: sys.exit(0)).pack(
            side="bottom", anchor="w", pady=2)
        tk.Frame(left, height=10).pack(side="bottom")
        tf = tk.Frame(left)
        tf.pack(fill="both", expand=True)
        self._tag_canvas = tk.Canvas(tf, width=200, highlightthickness=0)
        sb = tk.Scrollbar(tf, orient="vertical", command=self._tag_canvas.yview)
        self._tag_canvas.configure(yscrollcommand=sb.set)
        self._tag_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self._tag_inner = tk.Frame(self._tag_canvas)
        self._tag_canvas.create_window((0, 0), window=self._tag_inner, anchor="nw")
        self._tag_inner.bind(
            "<Configure>",
            lambda e: self._tag_canvas.configure(
                scrollregion=self._tag_canvas.bbox("all")))
        self._tag_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._tag_inner.bind("<MouseWheel>", self._on_mousewheel)

        mid = tk.Frame(self.root)
        mid.grid(row=0, column=1, sticky="nw", padx=5, pady=10)
        tk.Label(mid, text="More by artist").pack(anchor="w", pady=(0, 4))
        nav = tk.Frame(mid)
        nav.pack(anchor="w", pady=(0, 4))
        self._thumb_prev_btn = tk.Button(nav, text="<<", state="disabled", fg="grey",
                                         command=self._prev_thumb_page)
        self._thumb_prev_btn.pack(side="left", padx=(0, 4))
        self._thumb_next_btn = tk.Button(nav, text=">>", state="disabled", fg="grey",
                                         command=self._next_thumb_page)
        self._thumb_next_btn.pack(side="left")
        self._thumb_labels: list = []
        for _ in range(self.NUM_THUMBNAILS):
            lbl = tk.Label(mid, width=100, height=100)
            lbl.pack(pady=(0, 4))
            self._thumb_labels.append(lbl)

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

    def _on_mousewheel(self, event):
        self._tag_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _set_loading(self):
        self._artist_label.config(text="Artist: \u2014")
        self._img_label.config(image=self._ph_main, text="")
        for w in self._tag_inner.winfo_children():
            w.destroy()
        self._tag_canvas.yview_moveto(0)
        self._reset_thumbnails()

    def _clear_thumb_slots(self):
        """Reset the visual thumbnail slots to loading placeholders (used by pagination)."""
        self.thumb_images.clear()
        self.thumb_post_map = [None] * self.NUM_THUMBNAILS
        for lbl in self._thumb_labels:
            lbl.config(image=self._ph_thumb, text="", cursor="")
            lbl.unbind("<Button-1>")

    def _reset_thumbnails(self):
        """Full reset: clear candidates, page state, slots, and nav buttons."""
        self._thumb_candidates = []
        self._thumb_page = 0
        self._thumb_load_id += 1
        self._clear_thumb_slots()
        self._update_thumb_nav()

    def _update_thumb_nav(self):
        can_prev = self._thumb_page > 0
        can_next = (self._thumb_page + 1) * self.NUM_THUMBNAILS < len(self._thumb_candidates)
        for btn, enabled in ((self._thumb_prev_btn, can_prev), (self._thumb_next_btn, can_next)):
            btn.config(state="normal" if enabled else "disabled",
                       fg="black" if enabled else "grey")

    def _prev_thumb_page(self):
        if self._thumb_page > 0:
            self._thumb_page -= 1
            self._thumb_load_id += 1
            self._update_thumb_nav()
            self._load_thumb_page(self._thumb_page)

    def _next_thumb_page(self):
        if (self._thumb_page + 1) * self.NUM_THUMBNAILS < len(self._thumb_candidates):
            self._thumb_page += 1
            self._thumb_load_id += 1
            self._update_thumb_nav()
            self._load_thumb_page(self._thumb_page)

    def _build_tag_list(self, post_data: dict):
        self._tag_text_labels = {}
        for w in self._tag_inner.winfo_children():
            w.destroy()
        self._tag_canvas.yview_moveto(0)
        tags = sorted(t for ts in post_data.get("tags", {}).values() for t in ts)
        banned_set = set(self.banned_tags)
        for tag in tags:
            row = tk.Frame(self._tag_inner)
            row.pack(fill="x", anchor="w", pady=0, ipady=0)
            search_lbl = tk.Label(row, text="\U0001f50d", cursor="pointinghand",
                                   font=("TkDefaultFont", 7), pady=0)
            search_lbl.pack(side="left", padx=(0, 1), pady=0)
            search_lbl.bind("<Button-1>", lambda e, t=tag: self._add_tag_to_search(t))
            search_lbl.bind("<MouseWheel>", self._on_mousewheel)
            ban_lbl = tk.Label(row, text="\U0001f6ab", cursor="pointinghand",
                               font=("TkDefaultFont", 7), pady=0)
            ban_lbl.pack(side="left", padx=(0, 3), pady=0)
            ban_lbl.bind("<Button-1>", lambda e, t=tag: self._ban_tag(t))
            ban_lbl.bind("<MouseWheel>", self._on_mousewheel)
            is_banned = tag in banned_set
            lbl = tk.Label(row, text=tag, anchor="w", pady=0,
                           font=self._tag_strike_font if is_banned else self._tag_font)
            if is_banned:
                lbl.config(fg="grey")
            lbl.pack(side="left", pady=0)
            lbl.bind("<MouseWheel>", self._on_mousewheel)
            row.bind("<MouseWheel>", self._on_mousewheel)
            self._tag_text_labels[tag] = lbl
        self._tag_canvas.configure(scrollregion=self._tag_canvas.bbox("all"))

    def _add_tag_to_search(self, tag: str):
        existing = self._search_entry.get().strip().split()
        if tag not in existing:
            existing.append(tag)
        self._search_entry.delete(0, tk.END)
        self._search_entry.insert(0, " ".join(existing))
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

        def _thread():
            posts = self.client.fetch_posts(tags=tags, page=page, random_order=rand)
            def _on_main():
                if fgen != self._fetch_gen:
                    return  # search changed while fetch was in flight
                self._fetching = False
                self.post_buffer.extend(posts)
                if callback:
                    callback()
            self._ui_q.put(_on_main)

        t = threading.Thread(target=_thread, daemon=True)
        self._bg_threads.append(t)
        t.start()

    def _start_thumbnail_load(self, artist: str, exclude_id):
        """Phase 1: fetch all filtered candidates in background, then kick off page 0."""
        self._thumb_load_id += 1
        load_id = self._thumb_load_id
        banned = frozenset(self.banned_tags)

        def _thread(a=artist, eid=exclude_id, lid=load_id, b=banned):
            candidates: list = []
            page = 1
            max_pages = 5   # cap: at most 5 rate-limited API calls
            per_page = 25
            try:
                while len(candidates) < per_page and page <= max_pages:
                    resp = self.client.api_get(
                        self.client.API_URL,
                        params={"tags": a, "limit": per_page, "page": page})
                    if resp.status_code != 200:
                        break
                    raw = resp.json().get("posts", [])
                    if not raw:
                        break  # artist has no more posts
                    for p in raw:
                        if p.get("id") == eid:
                            continue
                        if p.get("file", {}).get("ext", "") not in self.ALLOWED_EXTENSIONS:
                            continue
                        hit = {t for ts in p.get("tags", {}).values() for t in ts} & b
                        if hit:
                            log.info("Skipping more-by-artist post %s — banned: %s",
                                     p.get("id"), ", ".join(hit))
                            continue
                        candidates.append(p)
                    if len(raw) < per_page:
                        break  # reached the last page of this artist's posts
                    page += 1
            except InterruptedError:
                pass  # stop_event fired during rate-limit sleep
            except Exception as ex:
                log.warning("Thumbnail candidate fetch failed: %s", ex)

            def _on_main():
                if lid != self._thumb_load_id:
                    return  # stale
                self._thumb_candidates = candidates
                self._thumb_page = 0
                self._update_thumb_nav()
                self._load_thumb_page(0)
            self._ui_q.put(_on_main)

        t = threading.Thread(target=_thread, daemon=True)
        self._bg_threads.append(t)
        t.start()

    def _load_thumb_page(self, page: int):
        """Phase 2: download preview images for one page of _thumb_candidates."""
        self._clear_thumb_slots()
        lid = self._thumb_load_id
        start = page * self.NUM_THUMBNAILS
        page_posts = self._thumb_candidates[start:start + self.NUM_THUMBNAILS]

        def _thread(posts=page_posts, lid=lid):
            for slot, p in enumerate(posts):
                preview_url = p.get("preview", {}).get("url")
                if not preview_url:
                    self._thumb_q.put((lid, "fail", slot, None, None))
                    continue
                try:
                    r = self.client.download(preview_url, timeout=5)
                    if r.status_code != 200:
                        self._thumb_q.put((lid, "fail", slot, None, None))
                        continue
                    thumb = Image.open(BytesIO(r.content))
                    thumb.thumbnail(self.THUMB_MAX, Image.Resampling.LANCZOS)
                    self._thumb_q.put((lid, "thumb", slot, p, thumb))
                except Exception:
                    self._thumb_q.put((lid, "fail", slot, None, None))
            self._thumb_q.put((lid, "done", len(posts), None, None))

        t = threading.Thread(target=_thread, daemon=True)
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
        self._random_state[0] = not self._random_state[0]
        self.random_order = self._random_state[0]
        self._invalidate_search()
        self._advance()

    def _perform_search(self):
        query = self._search_entry.get().strip()
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

    def _on_thumb_click(self, slot_idx: int):
        clicked = self.thumb_post_map[slot_idx]
        if clicked is None:
            return
        url = clicked.get("file", {}).get("url")
        if not url:
            return

        # Disable thumbnail clicks until the new image is loaded
        for lbl in self._thumb_labels:
            lbl.unbind("<Button-1>")
            lbl.config(cursor="")

        # Move current main image → thumbnail slot
        if self.current_img is not None:
            prev_thumb = self.current_img.copy()
            prev_thumb.thumbnail(self.THUMB_MAX, Image.Resampling.LANCZOS)
            prev_tk = ImageTk.PhotoImage(prev_thumb)
            self._thumb_labels[slot_idx].config(image=prev_tk, text="")
            self.thumb_images.append(prev_tk)
        self.thumb_post_map[slot_idx] = self.current_post
        prev_post = self.current_post
        self.current_post = clicked
        self._img_label.config(image=self._ph_main, text="")
        self._build_tag_list({})
        gen = self._post_gen

        def _thread(u=url, cp=clicked, pp=prev_post, g=gen):
            try:
                r = self.client.download(u, timeout=10)
                if r.status_code != 200:
                    self._swap_q.put((g, None, cp, pp))
                    return
                pil = Image.open(BytesIO(r.content))
                pil.thumbnail(self.IMG_MAX, Image.Resampling.LANCZOS)
                self._swap_q.put((g, pil, cp, pp))
            except Exception as ex:
                log.warning("Swap failed: %s", ex)
                self._swap_q.put((g, None, cp, pp))

        t = threading.Thread(target=_thread, daemon=True)
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
                self._artist_label.config(text=f"Artist: {artist}")
                fitted = self._fit_image(pil)
                tk_img = ImageTk.PhotoImage(fitted)
                self._img_label.config(image=tk_img, text="")
                self._tk_img = tk_img
                self.current_img = pil  # store original (unpadded) for thumbnail swaps
                self.current_post = post
                self._build_tag_list(post)
                self._reset_thumbnails()
                self._start_thumbnail_load(artist, post.get("id"))
        except queue.Empty:
            pass

        # Thumbnail results
        try:
            for _ in range(10):
                item = self._thumb_q.get_nowait()
                lid, kind = item[0], item[1]
                if lid != self._thumb_load_id:
                    continue
                if kind == "done":
                    # fill any remaining slots beyond what this page contained
                    for i in range(item[2], self.NUM_THUMBNAILS):
                        self._thumb_labels[i].config(image=self._ph_thumb_none, text="")
                elif kind == "fail":
                    self._thumb_labels[item[2]].config(image=self._ph_thumb_none, text="")
                else:  # "thumb"
                    _, _, slot, p, pil_thumb = item
                    tk_thumb = ImageTk.PhotoImage(pil_thumb)
                    self._thumb_labels[slot].config(
                        image=tk_thumb, text="", cursor="pointinghand")
                    self.thumb_images.append(tk_thumb)
                    self.thumb_post_map[slot] = p
                    self._thumb_labels[slot].bind(
                        "<Button-1>", lambda e, idx=slot: self._on_thumb_click(idx))
        except queue.Empty:
            pass

        # Swap results
        try:
            for _ in range(10):
                g, pil, cp, pp = self._swap_q.get_nowait()
                if g != self._post_gen:
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

                # Re-enable thumbnail clicks
                for i, post in enumerate(self.thumb_post_map):
                    if post:
                        lbl = self._thumb_labels[i]
                        lbl.config(cursor="pointinghand")
                        lbl.bind("<Button-1>", lambda e, idx=i: self._on_thumb_click(idx))
        except queue.Empty:
            pass

        self.root.after(50, self._poll)


def _shutdown(db: DatabaseManager, session_start: str):
    log.info("Shutting down e621 Discovery")
    rows = db.get_followed_since(session_start)
    if rows:
        log.info("Artists followed this session:")
        print("\n" + "\n".join(rows))
    else:
        log.info("No artists followed this session.")


def main():
    log.info("Starting e621 Discovery")
    session_start = datetime.now(timezone.utc).isoformat()
    db = DatabaseManager(DB_PATH)
    db.init()
    client = E621Client(_e621_username)
    atexit.register(_shutdown, db, session_start)
    root = tk.Tk()
    E621DiscoveryApp(root, db, client)
    root.mainloop()


if __name__ == "__main__":
    main()
