import tkinter as tk
from PIL.ImageTk import PhotoImage


class MainImage(tk.Label):
    """A widget to display the main post image."""

    def __init__(self, master, placeholder_image: PhotoImage):
        """
        Initializes the MainImage widget.

        Args:
            master: The parent tkinter widget.
            placeholder_image (PhotoImage): The image to display while loading.
        """
        super().__init__(master, width=800, height=600)
        self.placeholder = placeholder_image
        self._image: PhotoImage | None = None  # To keep the image reference
        self.set_loading()

    def set_loading(self):
        """Displays the placeholder loading image."""
        self.config(image=self.placeholder, text="")
        self._image = self.placeholder

    def set_image(self, image: PhotoImage):
        """Displays the given image."""
        self.config(image=image, text="")
        self._image = image
