# modules/engine.py
import logging
import threading
import queue
from io import BytesIO
from PIL import Image
from typing import Callable, Optional


from modules.database import DatabaseManager
from modules.api import E621Client

log = logging.getLogger(__name__)

class DiscoveryEngine:
    ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}
    IMG_MAX = (800, 600)

    def __init__(self, db: DatabaseManager, client: E621Client):
        self.db = db
        self.client = client
        
        # State
        self.followed_artists, self.ignored_artists = self.db.load_artists()
        self.banned_tags = self.db.load_banned_tags()
        self.current_tags = ""
        self.random_order = True
        self.page = 1
        self.post_buffer = []
        
        self.current_post = {}
        self.post_gen = 0  # Removed underscore to make it public to UI
        self._fetch_gen = 0
        self._fetching = False

        # Queues & Threads
        self.ui_q = queue.Queue()
        self.image_q = queue.Queue()
        self.swap_q = queue.Queue()
        self.bg_threads = []

        # Callbacks (Injected by the UI)
        self.on_loading: Optional[Callable[[], None]] = None

    def invalidate_search(self):
        self._fetch_gen += 1
        self._fetching = False
        self.post_buffer.clear()
        self.page = 1

    def _download_image(self, url: str, post: dict, gen: int) -> None:
        try:
            r = self.client.download(url, timeout=30)
            if r.status_code != 200:
                self.image_q.put((gen, post, None))
                return
            pil = Image.open(BytesIO(r.content))
            pil.thumbnail(self.IMG_MAX, Image.Resampling.LANCZOS)
            self.image_q.put((gen, post, pil))
        except Exception as ex:
            log.warning("Image download failed: %s", ex)
            self.image_q.put((gen, post, None))

    def _fetch_batch(self, callback=None):
        if self._fetching: return
        self._fetching = True
        fgen = self._fetch_gen
        tags, page, rand = self.current_tags, self.page, self.random_order
        self.page += 1
 
        def fetch_posts_thread(current_tags, current_page, random_order, fetch_gen, cb):
            try:
                posts = self.client.fetch_posts(tags=current_tags, page=current_page, random_order=random_order)
            except Exception as e:
                log.warning("Connection error while fetching posts: %s", e)
                posts = []

            def _on_main():
                if fetch_gen != self._fetch_gen: return 
                self._fetching = False
                if posts:
                    self.post_buffer.extend(posts)
                if cb: cb()
            self.ui_q.put(_on_main)

        t = threading.Thread(target=fetch_posts_thread, args=(tags, page, rand, fgen, callback), daemon=True)
        self.bg_threads.append(t)
        t.start()

    def follow(self):
        artist = (self.current_post.get("tags", {}).get("artist") or ["Unknown"])[0]
        if artist not in self.followed_artists:
            if self.db.add_followed_artist(artist):
                self.followed_artists.append(artist)
        log.info("Followed artist '%s'", artist)
        self.advance()

    def ignore(self):
        artist = (self.current_post.get("tags", {}).get("artist") or ["Unknown"])[0]
        if artist not in self.ignored_artists:
            if self.db.add_ignored_artist(artist):
                self.ignored_artists.append(artist)
        log.info("Ignored artist '%s'", artist)
        self.advance()

    def skip(self):
        artist = (self.current_post.get("tags", {}).get("artist") or ["Unknown"])[0]
        log.info("Skipped artist '%s'", artist)
        self.advance()
    
    def advance(self):
        while self.post_buffer:
            post = self.post_buffer.pop(0)
            artist = (post.get("tags", {}).get("artist") or ["Unknown"])[0]
            
            if artist in self.followed_artists or artist in self.ignored_artists: continue
            
            post_tags = {t for ts in post.get("tags", {}).values() for t in ts}
            if post_tags & set(self.banned_tags): continue
            
            url = post.get("file", {}).get("url")
            ext = post.get("file", {}).get("ext", "")
            if not url or ext not in self.ALLOWED_EXTENSIONS: continue

            self.post_gen += 1
            self.current_post = post
            
            if self.on_loading:
                self.on_loading() # Tell UI to reset

            if len(self.post_buffer) < 5:
                self._fetch_batch()

            t = threading.Thread(target=self._download_image, args=(url, post, self.post_gen), daemon=True)
            self.bg_threads.append(t)
            t.start()
            return
            
        self._fetch_batch(callback=self.advance)

    def start_swap(self, url: str, clicked_post: dict, prev_post: dict):
        """Handles the background downloading for a thumbnail swap."""
        self.current_post = clicked_post
        gen = self.post_gen

        def swap_thread(u, cp, pp, swap_gen):
            try:
                r = self.client.download(u, timeout=30)
                if r.status_code != 200:
                    self.swap_q.put((swap_gen, None, cp, pp))
                    return
                pil = Image.open(BytesIO(r.content))
                pil.thumbnail(self.IMG_MAX, Image.Resampling.LANCZOS)
                self.swap_q.put((swap_gen, pil, cp, pp))
            except Exception as ex:
                log.warning("Swap failed: %s", ex)
                self.swap_q.put((swap_gen, None, cp, pp))

        t = threading.Thread(target=swap_thread, args=(url, clicked_post, prev_post, gen), daemon=True)
        self.bg_threads.append(t)
        t.start()