import os
import json
import logging
from pydantic import BaseModel, Field, ValidationError
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

class AppConfig(BaseModel):
    """Application configuration model."""
    e621_username: str = Field(..., min_length=1)

_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
try:
    with open(_config_path) as _f:
        _config_data = json.load(_f)
    config = AppConfig(**_config_data)
    if config.e621_username == "<your_username>":
        raise ValueError("Username is the default placeholder value.")
except FileNotFoundError:
    raise SystemExit("config.json not found. Copy config.json.example to config.json and fill in your e621 username.")
except (ValidationError, ValueError):
    raise SystemExit("Please set a valid e621_username in config.json and ensure it is not the default placeholder value.")

_e621_username = config.e621_username
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "e621-discovery.sqlite3")