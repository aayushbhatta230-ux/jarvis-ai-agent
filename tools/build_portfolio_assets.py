"""Generate the portfolio favicon set and the Open Graph card from one design."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("portfolio")
BG_TOP = (13, 17, 23)
BG_BOTTOM = (22, 27, 34)
ACCENT = (88, 166, 255)
ACCENT_DEEP = (56, 139, 253)


def load_font(size, bold=True):
    candidates = [
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def vertical_gradient(size, top, bottom):
    width, height = size
    base = Image.new("RGB", (1, height))
    draw = ImageDraw.Draw(base)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        draw.point(
            (0, y),
            tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)),
        )
    return base.resize((width, height), Image.NEAREST)


def rounded(size, radius):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def draw_mark(image, cx, cy, radius, text, text_size):
    """Blue disc with the monogram, matching favicon.svg."""
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=ACCENT,
    )
    # A soft arc for depth.
    draw.arc(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        start=200,
        end=340,
        fill=ACCENT_DEEP,
        width=max(2, radius // 12),
    )
    text_font = load_font(text_size)
    draw.text((cx, cy), text, font=text_font, fill=BG_TOP, anchor="mm")
    return image


def build_favicons():
    for size, name in (
        (16, "favicon-16x16.png"),
        (32, "favicon-32x32.png"),
        (180, "apple-touch-icon.png"),
        (512, "icon-512.png"),
    ):
        canvas = rounded((size, size), 0)
        draw_mark(canvas, size // 2, size // 2, size // 2, "AB", int(size * 0.46))
        canvas.save(OUT / name)
        print(f"wrote {name} ({size}x{size})")


def build_og_image():
    width, height = 1200, 630
    image = vertical_gradient((width, height), BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)

    # Accent rule across the top.
    draw.rectangle([0, 0, width, 6], fill=ACCENT)

    # Mark.
    draw_mark(image, 150, 240, 78, "AB", 72)

    draw.text((270, 205), "Aayush Bhatta", font=load_font(64), fill=(240, 246, 252), anchor="lm")
    draw.text(
        (272, 268),
        "AI Engineer  ·  Full-Stack Developer",
        font=load_font(30, bold=False),
        fill=ACCENT,
        anchor="lm",
    )

    draw.text(
        (90, 400),
        "Building JARVIS — a local-first AI companion",
        font=load_font(40, bold=False),
        fill=(201, 209, 217),
        anchor="lm",
    )
    draw.text(
        (90, 460),
        "with voice, vision, and a web interface.",
        font=load_font(40, bold=False),
        fill=(201, 209, 217),
        anchor="lm",
    )

    # Tech chips.
    x = 90
    for label in ("Python", "LLMs", "Computer Vision", "Ollama", "Full-Stack"):
        text_font = load_font(26, bold=False)
        width_chip = int(draw.textlength(label, font=text_font)) + 40
        draw.rounded_rectangle(
            [x, 520, x + width_chip, 572],
            radius=26,
            fill=(28, 36, 46),
            outline=(48, 61, 73),
            width=2,
        )
        draw.text((x + width_chip // 2, 546), label, font=text_font, fill=(139, 148, 158), anchor="mm")
        x += width_chip + 16

    image.convert("RGB").save(OUT / "og-image.png", quality=92)
    print(f"wrote og-image.png ({width}x{height})")


if __name__ == "__main__":
    build_favicons()
    build_og_image()
