# e621-discovery

A desktop tool for discovering new artists on [e621.net](https://e621.net). It fetches posts from the e621 API, displays them one at a time in a persistent window, and lets you curate artists and tags — all persisted to a local SQLite database.

## Features

- Persistent single window — updates in place as you browse; no per-post window creation
- Browse posts filtered by one or more space-separated tags (negation with `-tag` supported)
- **Follow** ❤️, **Ignore** 🚫, or **Skip** ⏭️ each artist
- Followed and ignored artists are filtered out in future sessions
- Toggle tag bans with 🚫 next to any tag — banned tags are shown with strikethrough in the list; posts and thumbnails containing them are skipped
- Post buffer pre-fetched in the background so the next image loads without waiting for an API call
- Main image scaled to fit 800×600, letterboxed with the window background colour
- Scrollable tag list for the current post with per-tag 🔍 (add to search) and 🚫 (toggle ban) controls
- Up to 25 of the artist's other posts shown as 100×100 thumbnails, loaded asynchronously across up to 5 API pages to fill slots despite banned-tag filtering; paginated with `<<` / `>>` if more than 5 are available
- Thumbnails also filtered against banned tags
- Clicking a thumbnail swaps it with the main image
- Random or sequential post ordering
- On exit, a clean list of artists followed during the session is printed to the terminal

## Requirements

- Python 3.10+
- Dependencies listed in `requirements.txt`

## Setup

```bash
git clone https://github.com/youruser/e621-discovery.git
cd e621-discovery
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy the example config and fill in your e621 username (required by the e621 API rules):

```bash
cp config.json.example config.json
# edit config.json and replace <your_username>
```

## Usage

```bash
source venv/bin/activate
python main.py
```

### Controls

| Control | Action |
|---|---|
| ❤️ / `F` | Follow the artist (persisted, filtered in future runs) |
| 🚫 / `I` | Ignore the artist (persisted, filtered in future runs) |
| ⏭️ / `S` | Skip this post |
| 🔍 / Enter | Search by tag(s) |
| 🔍 next to tag | Add tag to search and immediately search |
| 🚫 next to tag | Toggle tag ban — banned tags show strikethrough; click again to unban |
| `Prev` / `Next` | Page through the artist's other posts in the thumbnail column |
| Thumbnail click | Swap thumbnail with the main image |
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
