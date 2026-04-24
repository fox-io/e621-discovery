from PIL import Image, ImageTk


def fit_image(pil: Image.Image, img_max_size: tuple[int, int], bg_color: tuple[int, int, int]) -> Image.Image:
    """Scale pil to fit within img_max_size maintaining aspect ratio, centered on bg canvas."""
    pil = pil.copy()
    pil.thumbnail(img_max_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", img_max_size, bg_color)
    canvas.paste(pil, ((img_max_size[0] - pil.width) // 2,
                       (img_max_size[1] - pil.height) // 2))
    return canvas
