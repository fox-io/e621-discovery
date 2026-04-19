import os
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
try:
    with open(_config_path) as _f:
        _config = json.load(_f)
except FileNotFoundError:
    raise SystemExit("config.json not found. Copy config.json.example to config.json and fill in your e621 username.")

_e621_username = _config.get("e621_username", "")
if not _e621_username or _e621_username == "<your_username>":
    raise SystemExit("Set e621_username in config.json before running.")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "e621-discovery.sqlite3")