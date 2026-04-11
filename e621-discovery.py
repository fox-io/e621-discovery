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
from datetime import datetime, timezone
from PIL import Image, ImageDraw
from io import BytesIO
import tkinter as tk
from tkinter import messagebox
from PIL import ImageTk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Constants
API_URL = "https://e621.net/posts.json"
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
try:
    with open(_config_path) as _f:
        _config = json.load(_f)
except FileNotFoundError:
    raise SystemExit("config.json not found. Copy config.json.example to config.json and fill in your e621 username.")
_e621_username = _config.get("e621_username", "")
if not _e621_username or _e621_username == "<your_username>":
    raise SystemExit("Set e621_username in config.json before running.")
HEADERS = {
    "User-Agent": f"e621 Discovery Script by {_e621_username}"
}
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e621-discovery.sqlite3")

_last_api_request = 0.0

def api_get(url, stop_event=None, **kwargs):
    """Rate-limited GET for e621 API endpoints (1 req/s).
    Pass stop_event to allow the rate-limit sleep to be interrupted."""
    global _last_api_request
    elapsed = time.monotonic() - _last_api_request
    if elapsed < 1.0:
        remaining = 1.0 - elapsed
        if stop_event:
            if stop_event.wait(timeout=remaining):
                raise InterruptedError("api_get aborted via stop_event")
        else:
            time.sleep(remaining)
    if stop_event and stop_event.is_set():
        raise InterruptedError("api_get aborted via stop_event")
    _last_api_request = time.monotonic()
    kwargs.setdefault("headers", HEADERS)
    return requests.get(url, **kwargs)

def get_db():
    """Return a connection to the SQLite database."""
    log.debug("Opening database connection: %s", DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    """Ensure tables exist (idempotent)."""
    log.info("Initializing database at %s", DB_PATH)
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS followed_artists (
        tag TEXT UNIQUE NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS ignored_artists (
        tag TEXT UNIQUE NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS banned_tags (
        tag TEXT UNIQUE NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.commit()
    conn.close()
    log.info("Database initialized successfully")

def load_artists():
    """Load followed and ignored artist lists from the database."""
    log.info("Loading artists from database")
    conn = get_db()
    followed = [row[0] for row in conn.execute("SELECT tag FROM followed_artists").fetchall()]
    ignored = [row[0] for row in conn.execute("SELECT tag FROM ignored_artists").fetchall()]
    conn.close()
    log.info("Loaded %d followed and %d ignored artists", len(followed), len(ignored))
    return followed, ignored

def add_followed_artist(artist) -> bool:
    """Insert an artist into the followed_artists table. Returns True on success."""
    try:
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR IGNORE INTO followed_artists (tag, timestamp) VALUES (?, ?)", (artist, now))
        conn.commit()
        conn.close()
        log.info("DB write: added '%s' to followed_artists", artist)
        return True
    except Exception as e:
        log.error("DB error adding followed artist '%s': %s", artist, e)
        return False

def add_ignored_artist(artist) -> bool:
    """Insert an artist into the ignored_artists table. Returns True on success."""
    try:
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR IGNORE INTO ignored_artists (tag, timestamp) VALUES (?, ?)", (artist, now))
        conn.commit()
        conn.close()
        log.info("DB write: added '%s' to ignored_artists", artist)
        return True
    except Exception as e:
        log.error("DB error adding ignored artist '%s': %s", artist, e)
        return False

def add_banned_tag(tag) -> bool:
    """Insert a tag into the banned_tags table. Returns True on success."""
    try:
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR IGNORE INTO banned_tags (tag, timestamp) VALUES (?, ?)", (tag, now))
        conn.commit()
        conn.close()
        log.info("DB write: added '%s' to banned_tags", tag)
        return True
    except Exception as e:
        log.error("DB error adding banned tag '%s': %s", tag, e)
        return False

def load_banned_tags():
    """Load banned tags list from the database."""
    conn = get_db()
    tags = [row[0] for row in conn.execute("SELECT tag FROM banned_tags").fetchall()]
    conn.close()
    log.info("Loaded %d banned tags", len(tags))
    return tags
# Fetch posts from e621 API
def fetch_posts(tags="", page=1, random_order=True):
    if random_order:
        combined_tags = ("order:random " + tags).strip() if tags else "order:random"
    else:
        combined_tags = tags
    log.info("Fetching posts from API (tags=%r, page=%d, random=%s)", combined_tags, page, random_order)
    params = {
        "tags": combined_tags,
        "page": page
    }
    response = api_get(API_URL, params=params)
    if response.status_code == 200:
        posts = response.json().get("posts", [])
        log.info("Received %d posts", len(posts))
        return posts
    else:
        log.error("Error fetching posts: HTTP %d", response.status_code)
        return []
# ──────────────────────────────────────────────────────────────────────────────
# Single persistent window
# ──────────────────────────────────────────────────────────────────────────────
class E621DiscoveryApp:
    """Single persistent window that updates in place for each post."""

    NUM_THUMBNAILS = 5
    IMG_MAX = (800, 600)
    THUMB_MAX = (100, 100)

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("e621 Discovery")
        self.root.geometry("1175x650+0+0")
        self.root.protocol("WM_DELETE_WINDOW", lambda: sys.exit(0))

        # Persistent session state
        self.followed_artists, self.ignored_artists = load_artists()
        self.banned_tags = load_banned_tags()
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
        af.pack(anchor="w", pady=2)
        tk.Button(af, text="\u2764\ufe0f", command=self._follow).pack(side="left", padx=(0, 2))
        tk.Button(af, text="\U0001f6ab", command=self._ignore).pack(side="left", padx=(0, 2))
        tk.Button(af, text="\u23ed\ufe0f", command=self._skip).pack(side="left")

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
        for w in self._tag_inner.winfo_children():
            w.destroy()
        tags = sorted(t for ts in post_data.get("tags", {}).values() for t in ts)
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
            lbl = tk.Label(row, text=tag, anchor="w", pady=0, font=("TkDefaultFont", 10))
            lbl.pack(side="left", pady=0)
            lbl.bind("<MouseWheel>", self._on_mousewheel)
            row.bind("<MouseWheel>", self._on_mousewheel)
        self._tag_canvas.configure(scrollregion=self._tag_canvas.bbox("all"))

    def _add_tag_to_search(self, tag: str):
        existing = self._search_entry.get().strip().split()
        if tag not in existing:
            existing.append(tag)
        self._search_entry.delete(0, tk.END)
        self._search_entry.insert(0, " ".join(existing))
        self._perform_search()

    def _ban_tag(self, tag: str):
        if tag not in self.banned_tags:
            if add_banned_tag(tag):
                self.banned_tags.append(tag)

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
            r = requests.get(url, headers=HEADERS, timeout=30)
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
            if not url or ext not in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
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
            posts = fetch_posts(tags=tags, page=page, random_order=rand)
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
            try:
                resp = api_get(API_URL, params={"tags": a, "limit": 25})
                if resp.status_code == 200:
                    for p in resp.json().get("posts", []):
                        if p.get("id") == eid:
                            continue
                        if p.get("file", {}).get("ext", "") not in (
                                "jpg", "jpeg", "png", "gif", "bmp", "webp"):
                            continue
                        hit = {t for ts in p.get("tags", {}).values() for t in ts} & b
                        if hit:
                            log.info("Skipping more-by-artist post %s — banned: %s",
                                     p.get("id"), ", ".join(hit))
                            continue
                        candidates.append(p)
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
                    r = requests.get(preview_url, headers=HEADERS, timeout=5)
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
            if add_followed_artist(artist):
                self.followed_artists.append(artist)
        log.info("Followed artist '%s'", artist)
        self._advance()

    def _ignore(self):
        artist = self._artist()
        if artist not in self.ignored_artists:
            if add_ignored_artist(artist):
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
                    resp = api_get("https://e621.net/tags.json",
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
                r = requests.get(u, headers=HEADERS, timeout=10)
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

        # Batch-fetch callbacks
        try:
            while True:
                cb = self._ui_q.get_nowait()
                try:
                    cb()
                except Exception as ex:
                    log.warning("UI callback error: %s", ex)
        except queue.Empty:
            pass

        # Image download results
        try:
            while True:
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
            while True:
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
            while True:
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
        except queue.Empty:
            pass

        self.root.after(50, self._poll)


def shutdown(session_start: str):
    log.info("Shutting down e621 Discovery")
    conn = get_db()
    rows = conn.execute(
        "SELECT tag FROM followed_artists WHERE timestamp >= ? ORDER BY timestamp",
        (session_start,)
    ).fetchall()
    conn.close()
    if rows:
        artists = [row[0] for row in rows]
        log.info("Artists followed this session:")
        print("\n" + "\n".join(artists))
    else:
        log.info("No artists followed this session.")


def main():
    log.info("Starting e621 Discovery")
    session_start = datetime.now(timezone.utc).isoformat()
    atexit.register(shutdown, session_start)
    init_db()
    root = tk.Tk()
    E621DiscoveryApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
