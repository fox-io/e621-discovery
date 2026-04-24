import tkinter as tk
from PIL.ImageTk import PhotoImage
from PIL import Image, ImageDraw, ImageFont


class MainImage(tk.Label):
    """A widget to display the main post image."""

    def __init__(self, master):
        """
        Initializes the MainImage widget.
        It is theme-aware and creates its own placeholder.
        """
        super().__init__(master, width=800, height=600)
        self._image: PhotoImage | None = None  # To keep the image reference
        self.placeholder = self._create_placeholder()
        self.set_loading()

    def _is_dark_theme(self) -> bool:
        """Checks if the root window background is dark."""
        try:
            # Get the root widget and its background color
            root = self.winfo_toplevel()
            bg_color = root.cget("bg")
            # Convert color to RGB values
            r, g, b = root.winfo_rgb(bg_color)
            # Calculate luminance (values are 0-65535)
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 65535
            return luminance < 0.5
        except (tk.TclError, AttributeError):
            # Fallback for errors during widget inspection
            return False

    def _create_placeholder(self) -> PhotoImage:
        """Creates a placeholder image with a theme-aware border and text."""
        width = 800
        height = 600

        root = self.winfo_toplevel()
        try:
            # On some systems, cget("bg") returns a name, not a hex code.
            # winfo_rgb can resolve this to a tuple of 16-bit values (0-65535),
            # which we scale down to 8-bit (0-255) for Pillow.
            r, g, b = root.winfo_rgb(root.cget("bg"))
            bg_color = (r // 256, g // 256, b // 256)
        except (tk.TclError, AttributeError):
            bg_color = (240, 240, 240)  # Fallback color

        if self._is_dark_theme():
            border_color = "#4a4a4a"  # A subtle light grey
            text_color = "#cccccc"
        else:
            border_color = "#dcdcdc"  # A subtle dark grey
            text_color = "#333333"

        image = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(image)

        # Draw 1px border inside the image area
        draw.rectangle([(0, 0), (width - 1, height - 1)], outline=border_color, width=1)

        # Draw text
        try:
            # Using a common sans-serif font is a good default
            font = ImageFont.truetype("tahoma.ttf", 20)
        except IOError:
            try:
                font = ImageFont.truetype("arial.ttf", 20)
            except IOError:
                font = ImageFont.load_default()

        draw.text(
            (width / 2, height / 2), "Loading...", fill=text_color, anchor="mm", font=font
        )

        return PhotoImage(image)

    def set_loading(self):
        """Displays the placeholder loading image."""
        self.config(image=self.placeholder, text="")
        self._image = self.placeholder

    def set_image(self, image: PhotoImage):
        """Displays the given image."""
        self.config(image=image, text="")
        self._image = image
