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
from PIL import Image
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
# Holds tkinter widget refs across display_post calls so background threads
# can never be the last holder, preventing Tcl_AsyncDelete crashes.
_tk_keepalive: list = []

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

def add_followed_artist(artist):
    """Insert an artist into the followed_artists table."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT OR IGNORE INTO followed_artists (tag, timestamp) VALUES (?, ?)", (artist, now))
    conn.commit()
    conn.close()
    log.info("DB write: added '%s' to followed_artists", artist)

def add_ignored_artist(artist):
    """Insert an artist into the ignored_artists table."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT OR IGNORE INTO ignored_artists (tag, timestamp) VALUES (?, ?)", (artist, now))
    conn.commit()
    conn.close()
    log.info("DB write: added '%s' to ignored_artists", artist)

def add_banned_tag(tag):
    """Insert a tag into the banned_tags table."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT OR IGNORE INTO banned_tags (tag, timestamp) VALUES (?, ?)", (tag, now))
    conn.commit()
    conn.close()
    log.info("DB write: added '%s' to banned_tags", tag)

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
# Display post and handle user interaction
def display_post(post, followed_artists, ignored_artists, banned_tags, current_tags="", random_order=True):
    result_dict = {}
    artist_list = post.get("tags", {}).get("artist", [])
    artist = artist_list[0] if artist_list else "Unknown"
    if artist in followed_artists:
        log.info("Skipping post %s — artist '%s' is already followed", post.get("id", "?"), artist)
        return
    if artist in ignored_artists:
        log.info("Skipping post %s — artist '%s' is already ignored", post.get("id", "?"), artist)
        return
    post_tag_set = {t for tags in post.get("tags", {}).values() for t in tags}
    hit = post_tag_set & set(banned_tags)
    if hit:
        log.info("Skipping post %s — contains banned tag(s): %s", post.get("id", "?"), ", ".join(hit))
        return
    file_info = post.get("file", {})
    image_url = file_info.get("url")
    file_ext = file_info.get("ext", "")
    if not image_url:
        return
    # Skip non-image file types (videos, flash, etc.)
    if file_ext not in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
        return
    log.info("Downloading image for post %s from %s", post.get("id", "?"), image_url)
    response = requests.get(image_url)
    if response.status_code == 200:
        img_data = response.content
        try:
            img = Image.open(BytesIO(img_data))
        except Exception:
            log.warning("Skipping post: could not decode image from %s", image_url)
            return
        # Scale image to fit within a reasonable window size
        max_size = (800, 800)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        root = tk.Tk()
        root.title("e621 Discovery")
        root.geometry("+0+0")
        root.protocol("WM_DELETE_WINDOW", lambda: sys.exit(0))
        stop_event = threading.Event()
        _ui_queue: queue.Queue = queue.Queue()
        _thumb_results: queue.Queue = queue.Queue()  # (slot, post_dict, PIL.Image) or ("done", count)
        _swap_results: queue.Queue = queue.Queue()   # (PIL.Image|None, clicked_post, prev_post)
        _poll_after_id: list = [None]
        def _poll_ui_queue():
            # Callbacks from _ui_queue (main-thread-safe operations)
            try:
                while True:
                    cb = _ui_queue.get_nowait()
                    try:
                        cb()
                    except tk.TclError:
                        pass
            except queue.Empty:
                pass
            # Thumbnail load results (thread put plain data; we do tkinter work here)
            try:
                while True:
                    item = _thumb_results.get_nowait()
                    if item[0] == "done":
                        for i in range(item[1], len(thumb_labels)):
                            try:
                                thumb_labels[i].config(text="(none)")
                            except tk.TclError:
                                pass
                    else:
                        slot, p, pil_thumb = item
                        try:
                            tk_thumb = ImageTk.PhotoImage(pil_thumb)
                            thumb_labels[slot].config(image=tk_thumb, text="", cursor="pointinghand")
                            thumb_images.append(tk_thumb)
                            thumb_post_map[slot] = p
                            thumb_labels[slot].bind("<Button-1>", lambda e, idx=slot: on_thumb_click(idx))
                        except tk.TclError:
                            pass
            except queue.Empty:
                pass
            # Image swap results
            try:
                while True:
                    pil_image, clicked_post_data, prev_post_data = _swap_results.get_nowait()
                    if pil_image is not None:
                        try:
                            new_tk_img = ImageTk.PhotoImage(pil_image)
                            img_label.config(image=new_tk_img, text="", width=0, height=0)
                            current_main["tk_img"] = new_tk_img
                            current_main["img"] = pil_image
                            build_tag_list(clicked_post_data)
                        except tk.TclError:
                            pass
                    else:
                        current_main["post"] = prev_post_data
            except queue.Empty:
                pass
            if not stop_event.is_set():
                _poll_after_id[0] = root.after(50, _poll_ui_queue)
        _poll_after_id[0] = root.after(50, _poll_ui_queue)
        def _on_destroy(e):
            if e.widget is root:
                stop_event.set()
                if _poll_after_id[0] is not None:
                    try:
                        root.after_cancel(_poll_after_id[0])
                    except tk.TclError:
                        pass
        root.bind("<Destroy>", _on_destroy)
        # Left column: artist label and buttons
        btn_frame = tk.Frame(root)
        btn_frame.grid(row=0, column=0, sticky="nw", padx=10, pady=10)
        # Random order checkbox (packed first so it appears at the top)
        random_state = [random_order]  # plain mutable; avoids BooleanVar GC crash in threads
        random_cb = tk.Checkbutton(btn_frame, text="Random order")
        if random_order:
            random_cb.select()
        random_cb.pack(anchor="w", pady=(0, 5))
        def perform_search():
            query = search_entry.get().strip()
            if not query:
                result_dict["action"] = "search"
                result_dict["tags"] = ""
                result_dict["random_order"] = random_state[0]
                root.destroy()
                return

            tags = query.split()
            try:
                tag_url = "https://e621.net/tags.json"
                invalid_tags = []
                for tag in tags:
                    params = {"search[name]": tag.lstrip("-")}
                    resp = api_get(tag_url, params=params)
                    if resp.status_code == 200:
                        if not resp.json():
                            invalid_tags.append(tag)
                    else:
                        messagebox.showerror("API Error", f"Error searching tags: {resp.status_code}")
                        return
                if invalid_tags:
                    messagebox.showinfo("Search Result", f"No tags found matching: {', '.join(invalid_tags)}")
                else:
                    result_dict["action"] = "search"
                    result_dict["tags"] = " ".join(tags)
                    result_dict["random_order"] = random_state[0]
                    root.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to connect: {e}")

        search_frame = tk.Frame(btn_frame)
        search_frame.pack(anchor="w", pady=(0, 10))
        search_entry = tk.Entry(search_frame, width=15)
        search_entry.insert(0, current_tags)
        search_entry.bind("<Return>", lambda e: perform_search())
        search_entry.pack(side="left", padx=(0, 5))
        search_btn = tk.Button(search_frame, text="🔍", command=perform_search)
        search_btn.pack(side="left")
        # Set checkbox command after search_entry exists so the toggle can read it
        def on_random_toggle():
            random_state[0] = not random_state[0]
            result_dict["action"] = "search"
            result_dict["tags"] = search_entry.get().strip() or current_tags
            result_dict["random_order"] = random_state[0]
            root.destroy()
        random_cb.config(command=on_random_toggle)
        tk.Label(btn_frame, text=f"Artist: {artist}").pack(anchor="w")
        action_frame = tk.Frame(btn_frame)
        action_frame.pack(anchor="w", pady=2)
        def skip_artist():
            log.info("Skipped artist '%s' (post %s)", artist, post.get("id", "?"))
            root.destroy()
        tk.Button(action_frame, text="❤️", command=lambda: follow_artist(artist, followed_artists, ignored_artists, root)).pack(side="left", padx=(0, 2))
        tk.Button(action_frame, text="🚫", command=lambda: ignore_artist(artist, followed_artists, ignored_artists, root)).pack(side="left", padx=(0, 2))
        tk.Button(action_frame, text="⏭️", command=skip_artist).pack(side="left")
        # Tag listbox
        tk.Label(btn_frame, text="Post Tags").pack(anchor="w", pady=(6, 0))
        all_tags = sorted(tag for tags in post.get("tags", {}).values() for tag in tags)
        tag_list_frame = tk.Frame(btn_frame)
        tag_list_frame.pack(anchor="w")
        tag_canvas = tk.Canvas(tag_list_frame, height=200, width=200, highlightthickness=0)
        tag_scrollbar = tk.Scrollbar(tag_list_frame, orient="vertical", command=tag_canvas.yview)
        tag_canvas.configure(yscrollcommand=tag_scrollbar.set)
        tag_canvas.pack(side="left", fill="both")
        tag_scrollbar.pack(side="left", fill="y")
        tag_inner = tk.Frame(tag_canvas)
        tag_canvas.create_window((0, 0), window=tag_inner, anchor="nw")
        tag_inner.bind("<Configure>", lambda e: tag_canvas.configure(scrollregion=tag_canvas.bbox("all")))
        def _on_mousewheel(event):
            tag_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        tag_canvas.bind("<MouseWheel>", _on_mousewheel)
        tag_inner.bind("<MouseWheel>", _on_mousewheel)
        def ban_tag(tag):
            add_banned_tag(tag)
            if tag not in banned_tags:
                banned_tags.append(tag)
        def add_tag_to_search(tag):
            existing = search_entry.get().strip().split()
            if tag not in existing:
                existing.append(tag)
            search_entry.delete(0, tk.END)
            search_entry.insert(0, " ".join(existing))
            perform_search()
        for tag in all_tags:
            row = tk.Frame(tag_inner)
            row.pack(fill="x", anchor="w")
            plus_lbl = tk.Label(row, text="+", fg="green", cursor="pointinghand")
            plus_lbl.pack(side="left", padx=(0, 2))
            plus_lbl.bind("<Button-1>", lambda e, t=tag: add_tag_to_search(t))
            plus_lbl.bind("<MouseWheel>", _on_mousewheel)
            minus_lbl = tk.Label(row, text="-", fg="red", cursor="pointinghand")
            minus_lbl.pack(side="left", padx=(0, 4))
            minus_lbl.bind("<Button-1>", lambda e, t=tag: ban_tag(t))
            minus_lbl.bind("<MouseWheel>", _on_mousewheel)
            tag_lbl = tk.Label(row, text=tag, anchor="w")
            tag_lbl.pack(side="left")
            tag_lbl.bind("<MouseWheel>", _on_mousewheel)
            row.bind("<MouseWheel>", _on_mousewheel)
        tk.Frame(btn_frame, height=10).pack()
        tk.Button(btn_frame, text="Quit", width=10, command=lambda: sys.exit(0)).pack(anchor="w", pady=2)
        # Middle column: artist thumbnails (loaded async after window opens)
        thumb_frame = tk.Frame(root)
        thumb_frame.grid(row=0, column=1, sticky="nw", padx=5, pady=10)
        tk.Label(thumb_frame, text="More by artist").pack(anchor="w", pady=(0, 4))
        thumb_images: list = []
        thumb_labels = []
        for _ in range(3):
            lbl = tk.Label(thumb_frame, text="…", fg="gray")
            lbl.pack(pady=(0, 4))
            thumb_labels.append(lbl)
        current_main: dict = {"post": post, "img": img}
        thumb_post_map: list = [None, None, None]
        def build_tag_list(post_data):
            for widget in tag_inner.winfo_children():
                widget.destroy()
            new_tags = sorted(tag for tags in post_data.get("tags", {}).values() for tag in tags)
            for tag in new_tags:
                row = tk.Frame(tag_inner)
                row.pack(fill="x", anchor="w")
                plus_lbl = tk.Label(row, text="+", fg="green", cursor="pointinghand")
                plus_lbl.pack(side="left", padx=(0, 2))
                plus_lbl.bind("<Button-1>", lambda e, t=tag: add_tag_to_search(t))
                plus_lbl.bind("<MouseWheel>", _on_mousewheel)
                minus_lbl = tk.Label(row, text="-", fg="red", cursor="pointinghand")
                minus_lbl.pack(side="left", padx=(0, 4))
                minus_lbl.bind("<Button-1>", lambda e, t=tag: ban_tag(t))
                minus_lbl.bind("<MouseWheel>", _on_mousewheel)
                tag_lbl = tk.Label(row, text=tag, anchor="w")
                tag_lbl.pack(side="left")
                tag_lbl.bind("<MouseWheel>", _on_mousewheel)
                row.bind("<MouseWheel>", _on_mousewheel)
            tag_canvas.configure(scrollregion=tag_canvas.bbox("all"))
        def on_thumb_click(slot_idx):
            clicked_post = thumb_post_map[slot_idx]
            if clicked_post is None:
                return
            full_url = clicked_post.get("file", {}).get("url")
            if not full_url:
                return
            # Immediately swap current main image into the thumbnail slot
            prev_thumb = current_main["img"].copy()
            prev_thumb.thumbnail((100, 100), Image.Resampling.LANCZOS)
            prev_tk_thumb = ImageTk.PhotoImage(prev_thumb)
            thumb_labels[slot_idx].config(image=prev_tk_thumb, text="")
            thumb_images.append(prev_tk_thumb)
            thumb_post_map[slot_idx] = current_main["post"]
            prev_post = current_main["post"]
            current_main["post"] = clicked_post
            img_label.config(image="", text="Loading…", compound="center", width=40, height=20)
            # Thread captures ONLY plain Python objects via default args — no tkinter refs
            def _swap_thread(url=full_url, cp=clicked_post, pp=prev_post):
                try:
                    if stop_event.is_set():
                        _swap_results.put((None, cp, pp))
                        return
                    r = requests.get(url, headers=HEADERS, timeout=10)
                    if r.status_code != 200:
                        _swap_results.put((None, cp, pp))
                        return
                    new_img_pil = Image.open(BytesIO(r.content))
                    new_img_pil.thumbnail((800, 800), Image.Resampling.LANCZOS)
                    _swap_results.put((new_img_pil, cp, pp))
                except Exception as e:
                    log.warning("Failed to load swapped image: %s", e)
                    _swap_results.put((None, cp, pp))
            t = threading.Thread(target=_swap_thread, daemon=True)
            _bg_threads.append(t)
            t.start()
        _bg_threads: list = []
        def _load_thumbnails_bg():
            # Captures ONLY plain Python objects — zero tkinter references.
            # All tkinter work is done by _poll_ui_queue reading _thumb_results.
            try:
                if stop_event.is_set():
                    _thumb_results.put(("done", 0))
                    return
                resp = api_get(API_URL, stop_event=stop_event, params={"tags": artist, "limit": 5})
                if resp.status_code != 200:
                    _thumb_results.put(("done", 0))
                    return
                candidates = [
                    p for p in resp.json().get("posts", [])
                    if p.get("id") != post.get("id")
                    and p.get("file", {}).get("ext", "") in ("jpg", "jpeg", "png", "gif", "bmp", "webp")
                ]
                loaded = 0
                for p in candidates:
                    if stop_event.is_set():
                        break
                    if loaded >= 3:
                        break
                    preview_url = p.get("preview", {}).get("url")
                    if not preview_url:
                        continue
                    try:
                        r = requests.get(preview_url, headers=HEADERS, timeout=5)
                        if r.status_code != 200:
                            continue
                        thumb = Image.open(BytesIO(r.content))
                        thumb.thumbnail((100, 100), Image.Resampling.LANCZOS)
                        _thumb_results.put((loaded, p, thumb))
                        loaded += 1
                    except Exception:
                        continue
                _thumb_results.put(("done", loaded))
            except InterruptedError:
                _thumb_results.put(("done", 0))
            except Exception as e:
                log.warning("Thumbnail load failed: %s", e)
                _thumb_results.put(("done", 0))
        t = threading.Thread(target=_load_thumbnails_bg, daemon=True)
        _bg_threads.append(t)
        t.start()
        # Right column: image
        tk_img = ImageTk.PhotoImage(img)
        img_label = tk.Label(root, image=tk_img)
        img_label.grid(row=0, column=2, sticky="nw", padx=10, pady=10)
        # Keep refs alive so background threads can never be the last holder
        # of a tkinter object. Overwritten at the start of the next call.
        _tk_keepalive[:] = [root, img_label, tag_inner, tag_canvas, tk_img, thumb_images, current_main] + list(thumb_labels)
        gc.collect()       # flush any pending cycles before threads run
        gc.disable()       # prevent background threads from triggering cyclic GC
        root.mainloop()
        stop_event.set()
        for t in _bg_threads:
            t.join(timeout=15.0)
        gc.enable()
        gc.collect()       # collect in main thread now that threads are done
        return result_dict
    else:
        log.error("Error fetching image: HTTP %d", response.status_code)
# Follow artist
def follow_artist(artist, followed_artists, ignored_artists, root):
    if artist not in followed_artists:
        followed_artists.append(artist)
        add_followed_artist(artist)
    root.destroy()
# Ignore artist
def ignore_artist(artist, followed_artists, ignored_artists, root):
    if artist not in ignored_artists:
        ignored_artists.append(artist)
        add_ignored_artist(artist)
    root.destroy()
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

# Main function
def main():
    log.info("Starting e621 Discovery")
    session_start = datetime.now(timezone.utc).isoformat()
    atexit.register(shutdown, session_start)
    init_db()
    followed_artists, ignored_artists = load_artists()
    banned_tags = load_banned_tags()
    current_tags = ""
    random_order = True
    page = 1
    while True:
        posts = fetch_posts(tags=current_tags, page=page, random_order=random_order)
        if not posts:
            log.info("No more posts available")
            break
        search_triggered = False
        for post in posts:
            res = display_post(post, followed_artists, ignored_artists, banned_tags, current_tags, random_order)
            if res and res.get("action") == "search":
                current_tags = res.get("tags", "")
                random_order = res.get("random_order", random_order)
                page = 1
                search_triggered = True
                break
        
        if not search_triggered:
            page += 1

if __name__ == "__main__":
    main()
