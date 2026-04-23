import logging
import queue
import threading
from io import BytesIO
import tkinter as tk

from PIL import Image, ImageTk

from modules.api import E621Client

log = logging.getLogger(__name__)


class ThumbnailGallery(tk.Frame):
    """A widget to display a paginated gallery of thumbnails for an artist."""

    NUM_THUMBNAILS = 5
    THUMB_MAX = (100, 100)
    ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}

    def __init__(self, master, client: E621Client, ph_thumb, ph_thumb_none, on_click_callback, ui_queue: queue.Queue):
        super().__init__(master)
        self.client = client
        self.ph_thumb = ph_thumb
        self.ph_thumb_none = ph_thumb_none
        self.on_click_callback = on_click_callback
        self.ui_queue = ui_queue

        # State
        self.banned_tags = set()
        self.thumb_images: list = []  # Keeps PhotoImage objects alive
        self.thumb_post_map: list = [None] * self.NUM_THUMBNAILS
        self._thumb_candidates: list = []
        self._thumb_page: int = 0
        self._thumb_load_id: int = 0
        self._thumb_slot_idx: int = 0
        self._thumb_next_candidate_idx: int = 0

        self._thumb_q: queue.Queue = queue.Queue()
        self._bg_threads: list = []

        self._build_ui()

    def _build_ui(self):
        tk.Label(self, text="More by artist").pack(anchor="w", pady=(0, 4))
        nav = tk.Frame(self)
        nav.pack(anchor="w", pady=(0, 4))
        self._thumb_prev_btn = tk.Button(nav, text="<<", state="disabled", fg="grey", command=self._prev_thumb_page)
        self._thumb_prev_btn.pack(side="left", padx=(0, 4))
        self._thumb_next_btn = tk.Button(nav, text=">>", state="disabled", fg="grey", command=self._next_thumb_page)
        self._thumb_next_btn.pack(side="left")

        self._thumb_labels: list = []
        for i in range(self.NUM_THUMBNAILS):
            lbl = tk.Label(self, width=100, height=100)
            lbl.pack(pady=(0, 4))
            self._thumb_labels.append(lbl)

    def _on_thumb_click(self, slot_idx: int):
        if self.thumb_post_map[slot_idx]:
            self.on_click_callback(slot_idx)

    def update_slot(self, slot_idx: int, image: Image.Image, post: dict):
        """Called by the parent app to fill a slot after a swap."""
        thumb = image.copy()
        thumb.thumbnail(self.THUMB_MAX, Image.Resampling.LANCZOS)
        tk_thumb = ImageTk.PhotoImage(thumb)

        # ONLY update the visual image and the data map.
        # Do not touch the cursor or bindings here!
        self._thumb_labels[slot_idx].config(image=tk_thumb, text="")
        self.thumb_images.append(tk_thumb)
        self.thumb_post_map[slot_idx] = post

    def disable_clicks(self):
        for lbl in self._thumb_labels:
            lbl.unbind("<Button-1>")
            # Explicitly set cursor to "watch" on the labels themselves.
            # This forces the OS to show the busy cursor.
            lbl.config(cursor="watch")

    def enable_clicks(self):
        # First, reset cursor on all labels to default
        for lbl in self._thumb_labels:
            lbl.config(cursor="")

        # Then, set pointing hand and bind for clickable ones
        for i, post in enumerate(self.thumb_post_map):
            lbl = self._thumb_labels[i]
            if post:
                lbl.config(cursor="pointinghand")
                lbl.bind("<Button-1>", lambda e, idx=i: self._on_thumb_click(idx))
            else:
                lbl.config(cursor="")

    def reset(self):
        """Full reset: clear candidates, page state, slots, and nav buttons."""
        self._thumb_candidates = []
        self._thumb_page = 0
        self._thumb_load_id += 1
        self._thumb_next_candidate_idx = 0
        self._clear_thumb_slots()
        self._update_thumb_nav()

    def start_load(self, artist: str, exclude_id: int, banned_tags: list):
        """Phase 1: fetch all filtered candidates in background, then kick off page 0."""
        self.reset()
        self.banned_tags = set(banned_tags)
        self._thumb_load_id += 1
        load_id = self._thumb_load_id

        def _thread(a=artist, eid=exclude_id, lid=load_id, b=self.banned_tags):
            candidates: list = []
            page = 1
            max_pages = 5
            per_page = 25
            try:
                while len(candidates) < per_page and page <= max_pages:
                    resp = self.client.api_get(
                        self.client.API_URL,
                        params={"tags": a, "limit": per_page, "page": page})
                    if resp.status_code != 200: break
                    raw = resp.json().get("posts", [])
                    if not raw: break
                    for p in raw:
                        if p.get("id") == eid: continue
                        if p.get("file", {}).get("ext", "") not in self.ALLOWED_EXTENSIONS: continue
                        hit = {t for ts in p.get("tags", {}).values() for t in ts} & b
                        if hit:
                            log.info("Skipping more-by-artist post %s — banned: %s", p.get("id"), ", ".join(hit))
                            continue
                        candidates.append(p)
                    if len(raw) < per_page: break
                    page += 1
            except InterruptedError: pass
            except Exception as ex: log.warning("Thumbnail candidate fetch failed: %s", ex)

            def _on_main():
                if lid != self._thumb_load_id: return
                self._thumb_candidates = candidates
                self._thumb_page = 0
                self._update_thumb_nav()
                self._load_thumb_page(0)
            self.ui_queue.put(_on_main)

        t = threading.Thread(target=_thread, daemon=True)
        self._bg_threads.append(t)
        t.start()

    def _clear_thumb_slots(self):
        self.thumb_images.clear()
        self.thumb_post_map = [None] * self.NUM_THUMBNAILS
        self._thumb_slot_idx = 0
        for lbl in self._thumb_labels:
            lbl.config(image=self.ph_thumb, text="", cursor="")
            lbl.unbind("<Button-1>")

    def _load_thumb_page(self, page: int):
        self._clear_thumb_slots()
        self._thumb_next_candidate_idx = page * self.NUM_THUMBNAILS
        self._fetch_thumb_batch()

    def _fetch_thumb_batch(self):
        needed = self.NUM_THUMBNAILS - self._thumb_slot_idx
        if needed <= 0: return

        start = self._thumb_next_candidate_idx
        posts_to_try = self._thumb_candidates[start : start + needed]

        if not posts_to_try:
            for i in range(self._thumb_slot_idx, self.NUM_THUMBNAILS):
                self._thumb_labels[i].config(image=self.ph_thumb_none, text="")
            return

        self._thumb_next_candidate_idx += len(posts_to_try)
        lid = self._thumb_load_id

        def _thread(posts=posts_to_try, lid=lid):
            for p in posts:
                preview_url = p.get("preview", {}).get("url")
                if not preview_url:
                    self._thumb_q.put((lid, "fail", p, None))
                    continue
                try:
                    r = self.client.download(preview_url, timeout=5)
                    if r.status_code != 200:
                        self._thumb_q.put((lid, "fail", p, None))
                        continue
                    thumb = Image.open(BytesIO(r.content))
                    thumb.thumbnail(self.THUMB_MAX, Image.Resampling.LANCZOS)
                    self._thumb_q.put((lid, "thumb", p, thumb))
                except Exception:
                    self._thumb_q.put((lid, "fail", p, None))
            self._thumb_q.put((lid, "batch_done", None, None))

        t = threading.Thread(target=_thread, daemon=True)
        self._bg_threads.append(t)
        t.start()

    def _update_thumb_nav(self):
        can_prev = self._thumb_page > 0
        can_next = (self._thumb_page + 1) * self.NUM_THUMBNAILS < len(self._thumb_candidates)
        for btn, enabled in ((self._thumb_prev_btn, can_prev), (self._thumb_next_btn, can_next)):
            btn.config(state="normal" if enabled else "disabled", fg="black" if enabled else "grey")

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

    def process_queue_events(self):
        self._bg_threads = [t for t in self._bg_threads if t.is_alive()]
        try:
            for _ in range(10):
                item = self._thumb_q.get_nowait()
                lid, kind = item[0], item[1]
                if lid != self._thumb_load_id: continue

                if kind == "thumb":
                    if self._thumb_slot_idx < self.NUM_THUMBNAILS:
                        slot = self._thumb_slot_idx
                        _, _, p, pil_thumb = item
                        tk_thumb = ImageTk.PhotoImage(pil_thumb)
                        self._thumb_labels[slot].config(image=tk_thumb, text="", cursor="pointinghand")
                        self.thumb_images.append(tk_thumb)
                        self.thumb_post_map[slot] = p
                        self._thumb_labels[slot].bind("<Button-1>", lambda e, idx=slot: self._on_thumb_click(idx))
                        self._thumb_slot_idx += 1
                elif kind == "fail":
                    _, _, p, _ = item
                    if p:
                        try:
                            idx = self._thumb_candidates.index(p)
                            self._thumb_candidates.pop(idx)
                            self._thumb_candidates.append(p)
                            if self._thumb_next_candidate_idx > idx:
                                self._thumb_next_candidate_idx -= 1
                        except ValueError: pass
                elif kind == "batch_done":
                    if self._thumb_slot_idx < self.NUM_THUMBNAILS:
                        self._fetch_thumb_batch()
        except queue.Empty:
            pass
