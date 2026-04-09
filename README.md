# e621-discovery

A desktop tool for discovering new artists on [e621.net](https://e621.net). It fetches posts from the e621 API, displays them one at a time, and lets you curate artists and tags you care about — all persisted to a local SQLite database.

## Features

- Browse posts filtered by one or more space-separated tags (negation with `-tag` supported)
- **Follow** ❤️, **Ignore** 🚫, or **Skip** ⏭️ each artist
- Followed and ignored artists are filtered out in future sessions
- Ban specific tags — posts containing banned tags are silently skipped
- View all tags on the current post with one-click shortcuts to add them to the search or ban them
- See up to 3 thumbnails of the artist's other posts, loaded in the background
- Random or sequential post ordering

## Requirements

- Python 3.8+
- Dependencies listed in `requirements.txt`

## Setup

```bash
git clone https://github.com/youruser/e621-discovery.git
cd e621-discovery
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Before running, update the `User-Agent` string near the top of `e621-discovery.py` with your e621 username, as required by the e621 API rules:

```python
HEADERS = {
    "User-Agent": "e621 Discovery Script by YourUsername"
}
```

## Usage

```bash
source venv/bin/activate
python e621-discovery.py
```

### Controls

| Control | Action |
|---|---|
| ❤️ | Follow the artist (persisted, filtered in future runs) |
| 🚫 | Ignore the artist (persisted, filtered in future runs) |
| ⏭️ | Skip this post |
| 🔍 / Enter | Search by tag(s) |
| `+` next to tag | Add tag to search and trigger search |
| `-` next to tag | Ban tag (posts with this tag will be skipped) |
| Quit | Exit the application |

### Tag Search

Enter one or more space-separated tags in the search box. Prefix a tag with `-` to exclude it (e.g. `wolf -anthro`). Each tag is validated against the e621 API before the search is submitted.

### Data

All data is stored in `e621-discovery.sqlite3` in the project directory:

- `followed_artists` — artists you have followed
- `ignored_artists` — artists you have ignored
- `banned_tags` — tags whose posts will be silently skipped

## License

MIT — see [LICENSE](LICENSE).
