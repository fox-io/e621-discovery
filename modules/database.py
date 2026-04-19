import sqlite3
import logging
from contextlib import closing
from datetime import datetime, timezone

log = logging.getLogger(__name__)


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

    def remove_followed_artist(self, artist) -> bool:
        try:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute("DELETE FROM followed_artists WHERE tag = ?", (artist,))
            log.info("DB write: removed '%s' from followed_artists", artist)
            return True
        except Exception as e:
            log.error("DB error removing followed artist '%s': %s", artist, e)
            return False

    def remove_ignored_artist(self, artist) -> bool:
        try:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute("DELETE FROM ignored_artists WHERE tag = ?", (artist,))
            log.info("DB write: removed '%s' from ignored_artists", artist)
            return True
        except Exception as e:
            log.error("DB error removing ignored artist '%s': %s", artist, e)
            return False

    def get_followed_since(self, since: str) -> list:
        with closing(self._connect()) as conn:
            return [row[0] for row in conn.execute(
                "SELECT tag FROM followed_artists WHERE timestamp >= ? ORDER BY timestamp",
                (since,)
            ).fetchall()]