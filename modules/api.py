import requests
import time
import logging

log = logging.getLogger(__name__)


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
        combined = ("order:random " + tags).strip() if random_order and tags else "order:random" if random_order else tags
        log.info("Fetching posts (tags=%r, page=%d, random=%s)", combined, page, random_order)
        resp = self.api_get(self.API_URL, params={"tags": combined, "page": page})
        if resp.status_code == 200:
            posts = resp.json().get("posts", [])
            log.info("Received %d posts", len(posts))
            return posts
        log.error("Error fetching posts: HTTP %d", resp.status_code)
        return []