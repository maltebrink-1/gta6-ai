"""On-screen text: word-by-word captions, hook title, fact cards, source credits
and a progress bar — all generated as one ASS subtitle file that FFmpeg's
libass renderer burns into the video (crisp at any resolution, very fast).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .facts import FactBase
from .script import Card, Script
from .styles import SMALL_TEXT_FONT, Theme, ass_color, ass_size_for_cap, safe_text, text_width
from .tts import Narration, Word

W, H = 1080, 1920
CAPTION_MAX_W = 820
CARD_W = 860
CARD_Y = 680
SOURCE_Y = 318
BAR_Y, BAR_X0, BAR_X1, BAR_H = 262, 70, 1010, 12


def ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def rounded_rect(w: float, h: float, r: float) -> str:
    k = 0.5523 * r
    return (
        f"m {r:.0f} 0 l {w - r:.0f} 0 b {w - r + k:.0f} 0 {w:.0f} {r - k:.0f} {w:.0f} {r:.0f} "
        f"l {w:.0f} {h - r:.0f} b {w:.0f} {h - r + k:.0f} {w - r + k:.0f} {h:.0f} {w - r:.0f} {h:.0f} "
        f"l {r:.0f} {h:.0f} b {r - k:.0f} {h:.0f} 0 {h - r + k:.0f} 0 {h - r:.0f} "
        f"l 0 {r:.0f} b 0 {r - k:.0f} {r - k:.0f} 0 {r:.0f} 0"
    )


_STRIP_PUNCT = re.compile(r"^[\"'“”‘’(]+|[\"'“”‘’),.;:]+$")


def caption_word(token: str, upper: bool) -> str:
    word = _STRIP_PUNCT.sub("", token)
    return word.upper() if upper else word


@dataclass
class Event:
    layer: int
    start: float
    end: float
    style: str
    text: str

    def line(self) -> str:
        return f"Dialogue: {self.layer},{ts(self.start)},{ts(self.end)},{self.style},,0,0,0,,{self.text}"


class CaptionBuilder:
    def __init__(self, theme: Theme, fonts_dir: Path, cfg):
        self.theme, self.fonts_dir, self.cfg = theme, fonts_dir, cfg
        self.font = theme.font
        self.upper = bool(cfg.get("captions.uppercase", True))
        self.caption_y = int(cfg.get("captions.y", 1185))
        self.max_words = int(cfg.get("captions.max_words_per_chunk", 3))
        self.max_chars = int(cfg.get("captions.max_chars_per_chunk", 18))
        self.size_caption = ass_size_for_cap(fonts_dir, self.font, float(cfg.get("captions.cap_height", 98)))
        self.size_hook = ass_size_for_cap(fonts_dir, self.font, 88)
        self.size_big = ass_size_for_cap(fonts_dir, self.font, 205)
        self.size_card_text = ass_size_for_cap(fonts_dir, self.font, 118)
        self.size_small = ass_size_for_cap(fonts_dir, self.font, 44)
        self.size_source = ass_size_for_cap(fonts_dir, SMALL_TEXT_FONT, 26)
        self.events: list[Event] = []

    # ── helpers ──────────────────────────────────────────────────────────────
    def width(self, text: str, size: float, family: str | None = None) -> float:
        return text_width(self.fonts_dir, family or self.font, text, size)

    def fit_size(self, text: str, size: int, max_w: float, family: str | None = None) -> int:
        w = self.width(text, size, family)
        return int(size * max_w / w) if w > max_w else size

    def safe(self, text: str, family: str | None = None) -> str:
        return esc(safe_text(self.fonts_dir, family or self.font, text))

    def add(self, layer: int, start: float, end: float, style: str, text: str) -> None:
        if end - start >= 0.02:
            self.events.append(Event(layer, start, end, style, text))

    # ── header ───────────────────────────────────────────────────────────────
    def header(self) -> str:
        white, black = "&H00FFFFFF", "&H00000000"
        shadow = "&H78000000"
        f, sf = self.font, SMALL_TEXT_FONT
        styles = [
            f"Style: Caption,{f},{self.size_caption},{white},{white},{black},{shadow},0,0,0,0,100,100,1,0,1,7,4,5,40,40,0,1",
            f"Style: Hook,{f},{self.size_hook},{white},{white},{black},{shadow},0,0,0,0,100,100,1,0,1,0,0,5,40,40,0,1",
            f"Style: Big,{f},{self.size_big},{white},{white},{black},{shadow},0,0,0,0,100,100,2,0,1,6,5,5,40,40,0,1",
            f"Style: Small,{f},{self.size_small},{white},{white},{black},{shadow},0,0,0,0,100,100,2,0,1,4,2,5,40,40,0,1",
            f"Style: Source,{sf},{self.size_source},&H33FFFFFF,{white},&H50000000,&H00000000,0,0,0,0,100,100,1,0,1,3,0,8,60,60,0,1",
            f"Style: Shape,{f},20,{white},{white},{black},{black},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        ]
        return "\n".join([
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {W}",
            f"PlayResY: {H}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding",
            *styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ])

    # ── elements ─────────────────────────────────────────────────────────────
    def progress_bar(self, duration: float) -> None:
        width = BAR_X1 - BAR_X0
        track = rounded_rect(width, BAR_H, BAR_H / 2)
        accent = ass_color(self.theme.color("accent"))
        self.add(0, 0, duration, "Shape", f"{{\\an7\\pos({BAR_X0},{BAR_Y})\\bord0\\shad0\\1c&HFFFFFF&\\1a&HB4&\\p1}}{track}")
        ms = int(duration * 1000)
        self.add(1, 0, duration, "Shape",
                 f"{{\\an7\\pos({BAR_X0},{BAR_Y})\\bord0\\shad0\\1c{accent}\\fscx0\\t(0,{ms},\\fscx100)\\p1}}{track}")

    def hook(self, text: str, start: float, end: float) -> None:
        text = text.upper()
        size = self.size_hook
        lines = self._wrap(text, size, 820)
        if len(lines) > 2:
            size = int(size * 0.82)
            lines = self._wrap(text, size, 820)
        line_h = size * 1.02
        box_w = max(self.width(line, size) for line in lines) + 110
        box_h = line_h * len(lines) + 70
        cy = 560
        dark = ass_color(self.theme.color("card"))
        accent = ass_color(self.theme.color("accent"))
        pop = "\\fscx30\\fscy30\\t(0,140,\\fscx112\\fscy112)\\t(140,240,\\fscx100\\fscy100)"
        out = "\\fad(0,160)"
        self.add(3, start, end, "Shape",
                 f"{{\\an5\\pos(540,{cy})\\frz-2.5\\bord0\\shad0\\1c{accent}{pop}{out}\\p1}}{rounded_rect(box_w, box_h, 34)}")
        body = "\\N".join(self.safe(line) for line in lines)
        self.add(4, start, end, "Hook",
                 f"{{\\an5\\pos(540,{cy})\\frz-2.5\\fs{size}\\1c{dark}{pop}{out}}}{body}")

    def _wrap(self, text: str, size: float, max_w: float) -> list[str]:
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if cur and self.width(trial, size) > max_w:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines or [text]

    def card(self, card: Card, start: float, end: float, source: str | None = None) -> None:
        accent = ass_color(self.theme.color("accent"))
        accent2 = ass_color(self.theme.color("accent2"))
        fill = ass_color(self.theme.color("card"), alpha=0x40)
        big_text = card.big.upper() if self.upper else card.big
        small_text = card.small.upper() if self.upper else card.small
        big_size = self.size_big if card.type == "stat" else self.size_card_text
        big_size = self.fit_size(big_text, big_size, CARD_W - 90)
        small_size = self.fit_size(small_text, self.size_small, CARD_W - 90)
        content_w = max(self.width(big_text, big_size), self.width(small_text, small_size))
        card_w = min(CARD_W, max(560, content_w + 150))
        box_h = big_size * 0.95 + small_size * 1.1 + 110
        top = CARD_Y - box_h / 2
        slide = f"\\move(540,{CARD_Y + 60},540,{CARD_Y},0,220)"
        fade = "\\fad(180,140)"
        self.add(2, start, end, "Shape",
                 f"{{\\an5{slide}{fade}\\bord6\\3c{accent2}\\shad0\\1c&H000000&\\1a&H40&\\p1}}{rounded_rect(card_w, box_h, 40)}")
        self.add(2, start, end, "Shape",
                 f"{{\\an5{slide}{fade}\\bord0\\shad0\\1c{fill}\\p1}}{rounded_rect(card_w - 12, box_h - 12, 34)}")
        big_y = top + 50 + big_size * 0.48
        small_y = big_y + big_size * 0.48 + small_size * 0.75 + 16
        pop = "\\fscx40\\fscy40\\t(60,200,\\fscx108\\fscy108)\\t(200,300,\\fscx100\\fscy100)"
        self.add(3, start, end, "Big",
                 f"{{\\an5\\move(540,{big_y + 60:.0f},540,{big_y:.0f},0,220){fade}\\fs{big_size}\\1c{accent}{pop}}}{self.safe(big_text)}")
        self.add(3, start, end, "Small",
                 f"{{\\an5\\move(540,{small_y + 60:.0f},540,{small_y:.0f},0,220){fade}\\fs{small_size}}}{self.safe(small_text)}")

    def source(self, text: str, start: float, end: float) -> None:
        label = self.safe(f"SOURCE: {text}".upper(), SMALL_TEXT_FONT)
        size = self.fit_size(label, self.size_source, 900, SMALL_TEXT_FONT)
        self.add(1, start, end, "Source", f"{{\\an8\\pos(540,{SOURCE_Y})\\fs{size}\\fad(150,150)}}{label}")

    def captions(self, words: list[Word]) -> None:
        accent = ass_color(self.theme.color("accent"))
        chunks = self.chunk(words)
        for ci, chunk in enumerate(chunks):
            nxt = chunks[ci + 1][0].start if ci + 1 < len(chunks) else None
            c_end = chunk[-1].end + 0.25
            if nxt is not None:
                c_end = nxt if nxt - chunk[-1].end < 0.6 else min(c_end, nxt)
            texts = [caption_word(w.text, self.upper) for w in chunk]
            plain = " ".join(texts)
            size = self.fit_size(plain, self.size_caption, CAPTION_MAX_W)
            for j, w in enumerate(chunk):
                s = chunk[0].start if j == 0 else w.start
                e = chunk[j + 1].start if j + 1 < len(chunk) else c_end
                parts = []
                for k, t in enumerate(texts):
                    t = self.safe(t)
                    parts.append(f"{{\\1c{accent}\\fscx110\\fscy110}}{t}{{\\1c&HFFFFFF&\\fscx100\\fscy100}}" if k == j else t)
                pop = "\\fscx70\\fscy70\\t(0,90,\\fscx105\\fscy105)\\t(90,150,\\fscx100\\fscy100)" if j == 0 else ""
                self.add(5, s, e, "Caption", f"{{\\an5\\pos(540,{self.caption_y})\\fs{size}{pop}}}" + " ".join(parts))

    def chunk(self, words: list[Word]) -> list[list[Word]]:
        chunks: list[list[Word]] = []
        cur: list[Word] = []
        for w in words:
            text = caption_word(w.text, self.upper)
            if not text:
                continue
            if cur:
                trial = " ".join(caption_word(x.text, self.upper) for x in cur + [w])
                prev = cur[-1]
                # keep a short number with the word before it ("REDEMPTION 2", "GTA 6")
                glue = text.isdigit() and len(text) <= 2 and not prev.text.endswith((".", ",", "!", "?", ";", ":"))
                breaks = (
                    (len(cur) >= self.max_words and not glue)
                    or (len(trial) > self.max_chars and not glue)
                    or prev.line != w.line
                    or prev.text.rstrip("\"'”’").endswith((".", ",", "!", "?", ";", ":"))
                    or w.start - prev.end > 0.35
                    or self.width(trial, self.size_caption) > CAPTION_MAX_W * (1.25 if glue else 1.0)
                )
                if breaks:
                    chunks.append(cur)
                    cur = []
            cur.append(w)
        if cur:
            chunks.append(cur)
        return chunks

    def render(self) -> str:
        body = [e.line() for e in sorted(self.events, key=lambda e: (e.start, e.layer))]
        return self.header() + "\n" + "\n".join(body) + "\n"


def build_ass(script: Script, narration: Narration, theme: Theme, fb: FactBase, cfg, fonts_dir: Path, duration: float) -> str:
    b = CaptionBuilder(theme, fonts_dir, cfg)
    b.progress_bar(duration)
    first_end = narration.lines[0][1] if narration.lines else 2.5
    hook_end = min(max(first_end + 0.15, 2.2), 3.6)
    b.hook(script.hook_text or script.title, 0.0, hook_end)
    for i, line in enumerate(script.lines):
        if i >= len(narration.lines):
            break
        start, end = narration.lines[i]
        nxt = narration.lines[i + 1][0] if i + 1 < len(narration.lines) else duration
        card = line.card
        if card is None and line.kind == "cta":
            card = Card("text", "FOLLOW", "FOR MORE GTA 6")
        if card and i > 0:
            b.card(card, start + 0.05, min(nxt, end + 0.35) if i + 1 < len(script.lines) else duration)
        if cfg.get("captions.show_sources", True) and line.facts and i > 0:
            fact = fb.get(line.facts[0])
            src = fact.primary_source if fact else None
            if src and src.publisher:
                b.source(src.publisher.split("(")[0].strip(), start, nxt)
    b.captions(narration.words)
    return b.render()


def build_srt(words: list[Word], max_chars: int = 42) -> str:
    """Standard subtitles file (for platforms that accept a captions track)."""
    cues: list[tuple[float, float, str]] = []
    cur: list[Word] = []
    for w in words:
        text = " ".join(x.text for x in cur + [w])
        if cur and (len(text) > max_chars or w.line != cur[-1].line or w.start - cur[-1].end > 0.6):
            cues.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        cues.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))

    def srt_ts(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    return "\n".join(f"{i}\n{srt_ts(a)} --> {srt_ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1))
