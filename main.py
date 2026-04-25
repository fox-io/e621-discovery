import atexit
import tkinter as tk
from datetime import datetime, timezone

from modules.config import DB_PATH, _e621_username, log
from modules.database import DatabaseManager
from modules.api import E621Client
from modules.engine import DiscoveryEngine  # <-- Add this import
from modules.ui import E621DiscoveryApp


def _shutdown(db: DatabaseManager, session_start: str):
    log.info("Shutting down e621 Discovery")
    rows = db.get_followed_since(session_start)
    if rows:
        log.info("Artists followed this session:")
        print("\n" + "\n".join(rows))
    else:
        log.info("No artists followed this session.")


def main():
    log.info("Starting e621 Discovery")
    session_start = datetime.now(timezone.utc).isoformat()
    
    db = DatabaseManager(DB_PATH)
    db.init()
    client = E621Client(_e621_username)
    
    # 1. Initialize the new Engine with the DB and Client
    engine = DiscoveryEngine(db, client)
    
    atexit.register(_shutdown, db, session_start)
    
    root = tk.Tk()
    root.withdraw()  # Hide window until it's built and sized to prevent jumping
    
    # 2. Pass the Engine into the UI!
    E621DiscoveryApp(root, engine)
    
    # Set a fixed window size and center it on the screen.
    width = 1230
    height = 660
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f'{width}x{height}+{x}+{y}')
    
    # Prevent the user from resizing the window, as the layout is fixed.
    root.resizable(False, False)
    
    root.deiconify()  # Show the window now that it's sized and positioned.

    root.mainloop()


if __name__ == "__main__":
    main()