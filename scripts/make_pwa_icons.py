"""Generate the PWA icons from the single source logo.

The app ships one logo (`raas-tracker-frontend/public/raas-logo.png`, 384x384
RGBA). iOS draws an apple-touch-icon with transparency over a black square, so
that icon is composited onto white; the manifest icons keep their alpha.

Run from the repository root:

    python scripts/make_pwa_icons.py

Pillow arrives with the backend requirements (pdfplumber depends on it).
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "raas-tracker-frontend" / "public" / "raas-logo.png"
OUTPUT_DIR = ROOT / "raas-tracker-frontend" / "public" / "icons"

# name, pixel size, composite onto an opaque background
TARGETS = (
    ("icon-192.png", 192, False),
    ("icon-512.png", 512, False),
    ("apple-touch-icon.png", 180, True),
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logo = Image.open(SOURCE).convert("RGBA")

    for name, size, opaque in TARGETS:
        icon = logo.resize((size, size), Image.LANCZOS)
        if opaque:
            background = Image.new("RGBA", icon.size, (255, 255, 255, 255))
            background.alpha_composite(icon)
            icon = background
        icon.save(OUTPUT_DIR / name, format="PNG", optimize=True)
        print(f"{name}: {size}x{size}")


if __name__ == "__main__":
    main()
