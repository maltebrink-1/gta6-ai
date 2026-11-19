"""Visual themes and fonts."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_FILES = {
    "Anton": "Anton-Regular.ttf",
    "Archivo Black": "ArchivoBlack-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Luckiest Guy": "LuckiestGuy-Regular.ttf",
    "Bangers": "Bangers-Regular.ttf",
}
SMALL_TEXT_FONT = "Archivo Black"


@dataclass
class Theme:
    name: str
    palette: dict[str, str]
    backgrounds: list[str]
    font: str
    transitions: list[str]

    def color(self, key: str) -> str:
        return self.palette.get(key, "#ffffff")


def stable_int(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


def pick_theme(styles: dict, script_id: str, requested: str | None = None, previous: str | None = None) -> Theme:
    themes = styles.get("themes", {})
    if not themes:
        raise ValueError("config/styles.yaml has no themes")
    if requested and requested in themes:
        name = requested
    else:
        names = sorted(themes)
        if previous in names and len(names) > 1:
            names.remove(previous)  # never the same look twice in a row
        name = names[stable_int(script_id) % len(names)]
    t = themes[name]
    return Theme(name, dict(t["palette"]), list(t["backgrounds"]), t.get("font", "Anton"), list(t.get("transitions", ["fade"])))


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def ass_color(color: str, alpha: int = 0) -> str:
    """'#rrggbb' → ASS '&HAABBGGRR' (alpha 0 = opaque, 255 = invisible)."""
    r, g, b = hex_to_rgb(color)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def font_path(fonts_dir: Path, family: str) -> Path:
    return fonts_dir / FONT_FILES.get(family, FONT_FILES["Anton"])


@lru_cache(maxsize=32)
def _pil_font(path: str, size: int = 100):
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=32)
def font_units(path: str) -> tuple[int, int]:
    """(unitsPerEm, libass line height in font units).

    libass sizes text so that usWinAscent + usWinDescent (OS/2 table) equals the
    ASS font size, which can differ a lot from the metrics PIL reports. We read
    those values straight from the TrueType tables.
    """
    import struct

    data = Path(path).read_bytes()
    count = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for i in range(count):
        tag, _checksum, offset, _length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        tables[tag.decode("latin-1")] = offset
    head = tables["head"]
    upm = struct.unpack(">H", data[head + 18: head + 20])[0]
    if "OS/2" in tables:
        os2 = tables["OS/2"]
        win_ascent, win_descent = struct.unpack(">HH", data[os2 + 74: os2 + 78])
        if win_ascent + win_descent:
            return upm, win_ascent + win_descent
    ascent, descent = _pil_font(path, upm).getmetrics()
    return upm, ascent + descent


def text_width(fonts_dir: Path, family: str, text: str, ass_size: float) -> float:
    """Rendered width in pixels of ``text`` at ASS font size ``ass_size`` (libass scaling)."""
    path = str(font_path(fonts_dir, family))
    upm, line_units = font_units(path)
    width_units = _pil_font(path, upm).getlength(text)  # at size=upm, 1 px == 1 font unit
    return width_units * ass_size / max(1, line_units)


@lru_cache(maxsize=64)
def ass_size_for_cap(fonts_dir: Path, family: str, cap_px: float) -> int:
    """ASS font size that gives capital letters a height of ``cap_px`` pixels."""
    path = str(font_path(fonts_dir, family))
    upm, line_units = font_units(path)
    left, top, right, bottom = _pil_font(path, upm).getbbox("H")
    cap_units = max(1, bottom - top)
    return int(round(cap_px * line_units / cap_units))


@lru_cache(maxsize=256)
def has_glyph(path: str, char: str) -> bool:
    """True if the font really contains ``char`` (not the .notdef box)."""
    from PIL import Image, ImageDraw

    font = _pil_font(path, 64)

    def render(ch: str) -> bytes:
        img = Image.new("L", (96, 96), 0)
        ImageDraw.Draw(img).text((8, 0), ch, font=font, fill=255)
        return img.tobytes()

    return render(char) != render("\U0010FFFD")


def safe_text(fonts_dir: Path, family: str, text: str) -> str:
    """Replace characters the font cannot draw with plain equivalents."""
    replacements = {"→": "-", "·": "|", "—": "-", "–": "-", "’": "'", "“": '"', "”": '"', "…": "...", "★": "*"}
    path = str(font_path(fonts_dir, family))
    out = []
    for ch in text:
        if ch.isspace() or ch.isascii() or has_glyph(path, ch):
            out.append(ch)
        else:
            out.append(replacements.get(ch, ""))
    return "".join(out)
