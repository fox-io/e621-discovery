import requests
import sqlite3
import os
import time
import sys
import logging
import atexit
import threading
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
HEADERS = {
    "User-Agent": "e621 Discovery Script by YourUsername"
}
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e621-discovery.sqlite3")

_last_api_request = 0.0

def api_get(url, **kwargs):
    """Rate-limited GET for e621 API endpoints (1 req/s)."""
    global _last_api_request
    elapsed = time.monotonic() - _last_api_request
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
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
        # Left column: artist label and buttons
        btn_frame = tk.Frame(root)
        btn_frame.grid(row=0, column=0, sticky="nw", padx=10, pady=10)
        # Random order checkbox (packed first so it appears at the top)
        random_var = tk.BooleanVar(value=random_order)
        random_cb = tk.Checkbutton(btn_frame, text="Random order", variable=random_var)
        random_cb.pack(anchor="w", pady=(0, 5))
        def perform_search():
            query = search_entry.get().strip()
            if not query:
                result_dict["action"] = "search"
                result_dict["tags"] = ""
                result_dict["random_order"] = random_var.get()
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
                    result_dict["random_order"] = random_var.get()
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
            result_dict["action"] = "search"
            result_dict["tags"] = search_entry.get().strip() or current_tags
            result_dict["random_order"] = random_var.get()
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
        thumb_frame.images = []
        thumb_labels = []
        for _ in range(3):
            lbl = tk.Label(thumb_frame, text="…", fg="gray")
            lbl.pack(pady=(0, 4))
            thumb_labels.append(lbl)
        def _load_thumbnails_bg():
            try:
                resp = api_get(API_URL, params={"tags": artist, "limit": 5})
                if resp.status_code != 200:
                    return
                candidates = [
                    p for p in resp.json().get("posts", [])
                    if p.get("id") != post.get("id")
                    and p.get("file", {}).get("ext", "") in ("jpg", "jpeg", "png", "gif", "bmp", "webp")
                ]
                loaded = 0
                for p in candidates:
                    if loaded >= len(thumb_labels):
                        break
                    preview_url = p.get("preview", {}).get("url")
                    if not preview_url:
                        continue
                    try:
                        r = requests.get(preview_url, headers=HEADERS)
                        if r.status_code != 200:
                            continue
                        thumb = Image.open(BytesIO(r.content))
                        thumb.thumbnail((100, 100), Image.Resampling.LANCZOS)
                        def apply(i=loaded, t=thumb):
                            try:
                                tk_thumb = ImageTk.PhotoImage(t)
                                thumb_labels[i].config(image=tk_thumb, text="")
                                thumb_labels[i].image = tk_thumb
                                thumb_frame.images.append(tk_thumb)
                            except tk.TclError:
                                pass
                        root.after(0, apply)
                        loaded += 1
                    except Exception:
                        continue
                for i in range(loaded, len(thumb_labels)):
                    def clear(lbl=thumb_labels[i]):
                        try:
                            lbl.config(text="(none)")
                        except tk.TclError:
                            pass
                    root.after(0, clear)
            except Exception as e:
                log.warning("Thumbnail load failed: %s", e)
        threading.Thread(target=_load_thumbnails_bg, daemon=True).start()
        # Right column: image
        tk_img = ImageTk.PhotoImage(img)
        img_label = tk.Label(root, image=tk_img)
        img_label.grid(row=0, column=2, sticky="nw", padx=10, pady=10)
        root.mainloop()
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
def shutdown():
    log.info("Shutting down e621 Discovery")

# Main function
def main():
    log.info("Starting e621 Discovery")
    atexit.register(shutdown)
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
