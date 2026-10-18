"""Generate Windows multi-resolution icon.ico from icon.png."""
from pathlib import Path
from PIL import Image


def generate_ico(png_path: Path | str = "icon.png", ico_path: Path | str = "icon.ico") -> Path:
    png_file = Path(png_path).resolve()
    ico_file = Path(ico_path).resolve()

    if not png_file.is_file():
        raise FileNotFoundError(f"Source image not found: {png_file}")

    with Image.open(png_file) as img:
        img = img.convert("RGBA")
        # Standard Windows multi-size layer resolutions
        icon_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(ico_file, format="ICO", sizes=icon_sizes)
        print(f"[OK] Generated {ico_file} with sizes: {icon_sizes} from {png_file.name}")

    return ico_file


if __name__ == "__main__":
    generate_ico()
