import requests
import json
import os
import sys
from PIL import Image
from io import BytesIO
import tkinter as tk
from tkinter import messagebox
from PIL import ImageTk
# Constants
API_URL = "https://e621.net/posts.json"
HEADERS = {
    "User-Agent": "e621 Discovery Script by YourUsername"
}
FOLLOWED_ARTISTS_FILE = "followed_artists.json"
IGNORED_ARTISTS_FILE = "ignored_artists.json"
# Load followed and ignored artists from JSON files
def load_artists():
    if os.path.exists(FOLLOWED_ARTISTS_FILE):
        with open(FOLLOWED_ARTISTS_FILE, "r") as f:
            followed_artists = json.load(f)
    else:
        followed_artists = []
    if os.path.exists(IGNORED_ARTISTS_FILE):
        with open(IGNORED_ARTISTS_FILE, "r") as f:
            ignored_artists = json.load(f)
    else:
        ignored_artists = []
    return followed_artists, ignored_artists
# Save followed and ignored artists to JSON files
def save_artists(followed_artists, ignored_artists):
    with open(FOLLOWED_ARTISTS_FILE, "w") as f:
        json.dump(followed_artists, f)
    with open(IGNORED_ARTISTS_FILE, "w") as f:
        json.dump(ignored_artists, f)
# Fetch posts from e621 API
def fetch_posts(tags="", page=1):
    params = {
        "tags": tags,
        "page": page
    }
    response = requests.get(API_URL, headers=HEADERS, params=params)
    if response.status_code == 200:
        return response.json().get("posts", [])
    else:
        print(f"Error fetching posts: {response.status_code}")
        return []
# Display post and handle user interaction
def display_post(post, followed_artists, ignored_artists):
    artist_list = post.get("tags", {}).get("artist", [])
    artist = artist_list[0] if artist_list else "Unknown"
    if artist in followed_artists or artist in ignored_artists:
        return
    file_info = post.get("file", {})
    image_url = file_info.get("url")
    file_ext = file_info.get("ext", "")
    if not image_url:
        return
    # Skip non-image file types (videos, flash, etc.)
    if file_ext not in ("jpg", "jpeg", "png", "gif", "bmp", "webp"):
        return
    response = requests.get(image_url)
    if response.status_code == 200:
        img_data = response.content
        try:
            img = Image.open(BytesIO(img_data))
        except Exception:
            print(f"Skipping post: could not decode image from {image_url}")
            return
        # Scale image to fit within a reasonable window size
        max_size = (800, 800)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        root = tk.Tk()
        root.title("e621 Discovery")
        root.geometry("+0+0")
        # Left column: artist label and buttons
        btn_frame = tk.Frame(root)
        btn_frame.grid(row=0, column=0, sticky="nw", padx=10, pady=10)
        tk.Label(btn_frame, text=f"Artist: {artist}").pack(anchor="w")
        tk.Button(btn_frame, text="Follow", width=10, command=lambda: follow_artist(artist, followed_artists, ignored_artists, root)).pack(anchor="w", pady=2)
        tk.Button(btn_frame, text="Ignore", width=10, command=lambda: ignore_artist(artist, followed_artists, ignored_artists, root)).pack(anchor="w", pady=2)
        tk.Button(btn_frame, text="Skip", width=10, command=root.destroy).pack(anchor="w", pady=2)
        tk.Button(btn_frame, text="Quit", width=10, command=lambda: sys.exit(0)).pack(anchor="w", pady=2)
        # Right column: image
        tk_img = ImageTk.PhotoImage(img)
        img_label = tk.Label(root, image=tk_img)
        img_label.grid(row=0, column=1, sticky="nw", padx=10, pady=10)
        root.mainloop()
    else:
        print(f"Error fetching image: {response.status_code}")
# Follow artist
def follow_artist(artist, followed_artists, ignored_artists, root):
    if artist not in followed_artists:
        followed_artists.append(artist)
        save_artists(followed_artists, ignored_artists)
    root.destroy()
# Ignore artist
def ignore_artist(artist, followed_artists, ignored_artists, root):
    if artist not in ignored_artists:
        ignored_artists.append(artist)
        save_artists(followed_artists, ignored_artists)
    root.destroy()
# Main function
def main():
    followed_artists, ignored_artists = load_artists()
    page = 1
    while True:
        posts = fetch_posts(page=page)
        if not posts:
            print("No more posts available.")
            break
        for post in posts:
            display_post(post, followed_artists, ignored_artists)
        page += 1

if __name__ == "__main__":
    main()
