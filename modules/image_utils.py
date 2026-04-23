from PIL import Image, ImageDraw, ImageTk


def fit_image(pil: Image.Image, img_max_size: tuple[int, int], bg_color: tuple[int, int, int]) -> Image.Image:
    """Scale pil to fit within img_max_size maintaining aspect ratio, centered on bg canvas."""
    pil = pil.copy()
    pil.thumbnail(img_max_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", img_max_size, bg_color)
    canvas.paste(pil, ((img_max_size[0] - pil.width) // 2,
                       (img_max_size[1] - pil.height) // 2))
    return canvas


def make_placeholders(bg_color: tuple[int, int, int], main_size: tuple[int, int], thumb_size: tuple[int, int]):
    """Create and return (main_placeholder, thumb_placeholder, thumb_none_placeholder) as PhotoImages."""
    border = (150, 150, 150)
    text_color = (80, 80, 80)

    # main placeholder
    main = Image.new("RGB", main_size, bg_color)
    d = ImageDraw.Draw(main)
    d.rectangle([0, 0, main_size[0] - 1, main_size[1] - 1], outline=border)
    text = "Loading..."
    bb = d.textbbox((0, 0), text)
    d.text(((main_size[0] - (bb[2] - bb[0])) // 2, (main_size[1] - (bb[3] - bb[1])) // 2),
           text, fill=text_color)

    # thumbnail placeholders
    def _thumb_img(label_text):
        img = Image.new("RGB", thumb_size, bg_color)
        d2 = ImageDraw.Draw(img)
        d2.rectangle([0, 0, thumb_size[0] - 1, thumb_size[1] - 1], outline=border)
        bb2 = d2.textbbox((0, 0), label_text)
        d2.text(((thumb_size[0] - (bb2[2] - bb2[0])) // 2, (thumb_size[1] - (bb2[3] - bb2[1])) // 2),
                label_text, fill=text_color)
        return ImageTk.PhotoImage(img)

    return ImageTk.PhotoImage(main), _thumb_img("Loading..."), _thumb_img("None")