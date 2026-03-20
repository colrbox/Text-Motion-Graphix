#!/usr/bin/env python3
"""
SRT2Motion — Convert SRT subtitles into stunning motion graphics.
Renders animated caption overlays using FFmpeg filter graphs.

Usage:
  python srt2motion.py input.srt --style neon --output output.mp4
  python srt2motion.py input.srt --bg-video source.mp4 --style minimal
  python srt2motion.py input.srt --list-styles
"""

import argparse
import re
import sys
import os
import subprocess
import tempfile
import json
from dataclasses import dataclass, field
from typing import Optional
from datetime import timedelta

# ─────────────────────────────────────────
#  SRT PARSER
# ─────────────────────────────────────────

@dataclass
class Subtitle:
    index: int
    start: float   # seconds
    end: float     # seconds
    text: str      # plain text (HTML stripped)


def parse_srt(path: str) -> list[Subtitle]:
    """Parse an .srt file into a list of Subtitle objects."""
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    blocks = re.split(r"\n{2,}", content.strip())
    subs = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue

        ts = lines[1]
        m = re.match(
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
            ts,
        )
        if not m:
            continue

        def to_sec(h, mi, s, ms):
            return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000.0

        start = to_sec(*m.group(1, 2, 3, 4))
        end   = to_sec(*m.group(5, 6, 7, 8))
        raw   = " ".join(lines[2:])
        text  = re.sub(r"<[^>]+>", "", raw).strip()  # strip HTML tags
        subs.append(Subtitle(idx, start, end, text))

    return subs


# ─────────────────────────────────────────
#  STYLE DEFINITIONS
# ─────────────────────────────────────────

@dataclass
class Style:
    name: str
    description: str
    # Font
    font: str = "DejaVuSans-Bold"
    fontsize: int = 52
    font_color: str = "white"
    # Box / background
    box: int = 1
    box_color: str = "black@0.55"
    box_border: int = 16
    box_border_w: int = 0          # outline around box
    # Text outline / shadow
    border_w: int = 3
    border_color: str = "black@0.8"
    shadow_x: int = 2
    shadow_y: int = 2
    shadow_color: str = "black@0.5"
    # Layout
    x_expr: str = "(w-text_w)/2"
    y_expr: str = "h-th-80"
    # Animation (fade)
    fade_in: float = 0.15          # seconds
    fade_out: float = 0.15
    # Extra FFmpeg filter chain (appended after drawtext)
    extra_filter: str = ""
    # Word-by-word highlight color (empty = disabled)
    highlight_color: str = ""


STYLES: dict[str, Style] = {

    "minimal": Style(
        name="minimal",
        description="Clean white text, thin shadow, no box — editorial look",
        font="DejaVuSans",
        fontsize=48,
        font_color="white",
        box=0,
        border_w=2,
        border_color="black@0.9",
        shadow_x=2, shadow_y=2,
        shadow_color="black@0.6",
        fade_in=0.2, fade_out=0.2,
    ),

    "bold": Style(
        name="bold",
        description="Chunky black outline, large type — social-media ready",
        font="DejaVuSans-Bold",
        fontsize=62,
        font_color="white",
        box=0,
        border_w=6,
        border_color="black",
        shadow_x=0, shadow_y=0,
        fade_in=0.1, fade_out=0.1,
    ),

    "neon": Style(
        name="neon",
        description="Cyan glow on dark semi-transparent pill — cyberpunk vibes",
        font="DejaVuSans-Bold",
        fontsize=54,
        font_color="#00FFEA",
        box=1,
        box_color="#0D0D1A@0.82",
        box_border=20,
        border_w=2,
        border_color="#00FFEA@0.5",
        shadow_x=0, shadow_y=0,
        fade_in=0.18, fade_out=0.18,
    ),

    "cinema": Style(
        name="cinema",
        description="Letterbox-style yellow on black bar — classic film look",
        font="DejaVuSans",
        fontsize=46,
        font_color="#FFE566",
        box=1,
        box_color="#000000@0.75",
        box_border=22,
        border_w=0,
        border_color="black",
        shadow_x=1, shadow_y=1,
        shadow_color="#FFE566@0.3",
        x_expr="(w-text_w)/2",
        y_expr="h-th-60",
        fade_in=0.25, fade_out=0.25,
    ),

    "gradient": Style(
        name="gradient",
        description="White text on a gradient-bar backdrop — modern streaming style",
        font="DejaVuSans-Bold",
        fontsize=52,
        font_color="white",
        box=1,
        box_color="#1A1A2E@0.78",
        box_border=24,
        border_w=1,
        border_color="#ffffff@0.15",
        shadow_x=2, shadow_y=2,
        shadow_color="#000@0.7",
        fade_in=0.2, fade_out=0.2,
    ),

    "karaoke": Style(
        name="karaoke",
        description="Bold white text on dark pill with bright magenta highlight",
        font="DejaVuSans-Bold",
        fontsize=58,
        font_color="white",
        box=1,
        box_color="#1a0a2e@0.88",
        box_border=20,
        border_w=3,
        border_color="#FF2D78@0.9",
        shadow_x=0, shadow_y=0,
        highlight_color="#FF2D78",
        fade_in=0.12, fade_out=0.12,
    ),

    "typewriter": Style(
        name="typewriter",
        description="Monospace retro terminal look with blinking cursor feel",
        font="DejaVuSansMono-Bold",
        fontsize=46,
        font_color="#39FF14",
        box=1,
        box_color="#0a0a0a@0.92",
        box_border=18,
        border_w=1,
        border_color="#39FF14@0.4",
        shadow_x=0, shadow_y=0,
        fade_in=0.05, fade_out=0.08,
    ),

    "luxury": Style(
        name="luxury",
        description="Serif-style gold text, refined spacing, no box — high-end brand",
        font="DejaVuSerif-Bold",
        fontsize=50,
        font_color="#D4AF37",
        box=0,
        border_w=2,
        border_color="#1a0a00@0.8",
        shadow_x=3, shadow_y=3,
        shadow_color="#000@0.5",
        fade_in=0.3, fade_out=0.3,
    ),

    "pill": Style(
        name="pill",
        description="Modern rounded pill with white text — TikTok / Reels style",
        font="DejaVuSans-Bold",
        fontsize=54,
        font_color="white",
        box=1,
        box_color="#222222@0.90",
        box_border=26,
        border_w=0,
        border_color="black",
        shadow_x=0, shadow_y=0,
        fade_in=0.15, fade_out=0.15,
    ),
}


# ─────────────────────────────────────────
#  FFMPEG FILTER BUILDER
# ─────────────────────────────────────────

def escape_ffmpeg_text(text: str) -> str:
    """Escape special characters for FFmpeg drawtext."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'",  "\u2019")   # smart apostrophe avoids shell quoting hell
    text = text.replace(":",  "\\:")
    text = text.replace("[",  "\\[")
    text = text.replace("]",  "\\]")
    text = text.replace(",",  "\\,")
    text = text.replace(";",  "\\;")
    text = text.replace("%",  "\\%")
    return text


def find_font(name: str) -> str:
    """Resolve font name to a file path FFmpeg can use."""
    # Common font directories
    search_dirs = [
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype",
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
    ]
    # Map shorthand names to filenames
    aliases = {
        "DejaVuSans":         "DejaVuSans.ttf",
        "DejaVuSans-Bold":    "DejaVuSans-Bold.ttf",
        "DejaVuSerif-Bold":   "DejaVuSerif-Bold.ttf",
        "DejaVuSansMono-Bold":"DejaVuSansMono-Bold.ttf",
    }
    filename = aliases.get(name, name)
    for d in search_dirs:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    # Fallback: let FFmpeg resolve by name
    return name


def build_drawtext(sub: Subtitle, style: Style, total_duration: float) -> str:
    """Build a single drawtext filter segment for one subtitle."""
    text   = escape_ffmpeg_text(sub.text)
    font   = find_font(style.font)
    start  = sub.start
    end    = sub.end
    dur    = max(end - start, 0.05)
    fi     = min(style.fade_in,  dur / 3)
    fo     = min(style.fade_out, dur / 3)

    # Alpha expression: fade in → hold → fade out
    alpha = (
        f"if(lt(t-{start:.3f},{fi}),"
        f"  (t-{start:.3f})/{fi},"
        f"  if(lt(t-{start:.3f},{dur-fo:.3f}),"
        f"    1,"
        f"    ({end:.3f}-t)/{fo}"
        f"  )"
        f")"
    )
    # Only draw between start/end
    enable = f"between(t,{start:.3f},{end:.3f})"

    params = [
        f"fontfile='{font}'",
        f"text='{text}'",
        f"fontsize={style.fontsize}",
        f"fontcolor={style.font_color}@1.0",
        f"alpha='{alpha}'",
        f"x={style.x_expr}",
        f"y={style.y_expr}",
        f"enable='{enable}'",
        f"borderw={style.border_w}",
        f"bordercolor={style.border_color}",
        f"shadowx={style.shadow_x}",
        f"shadowy={style.shadow_y}",
        f"shadowcolor={style.shadow_color}",
        f"line_spacing=8",
    ]
    if style.box:
        params += [
            "box=1",
            f"boxcolor={style.box_color}",
            f"boxborderw={style.box_border}",
        ]

    return "drawtext=" + ":".join(params)


def build_filter_chain(subs: list[Subtitle], style: Style,
                        total_duration: float, res: tuple[int,int]) -> str:
    """Compose all subtitle drawtext calls into one FFmpeg filtergraph."""
    w, h = res
    parts = [f"scale={w}:{h}"]   # ensure consistent resolution
    for sub in subs:
        parts.append(build_drawtext(sub, style, total_duration))
    if style.extra_filter:
        parts.append(style.extra_filter)
    return ",".join(parts)


# ─────────────────────────────────────────
#  BACKGROUND VIDEO / IMAGE / COLOR
# ─────────────────────────────────────────

def prepare_background(bg: Optional[str], duration: float,
                        res: tuple[int,int], fps: int,
                        tmp_dir: str) -> tuple[str, list[str]]:
    """
    Returns (input_arg, ffmpeg_flags) describing how to specify the background.
    If bg is None, generate a dark gradient background.
    """
    w, h = res
    if bg is None:
        # Generate a beautiful dark background with lavender-to-navy gradient
        bg_path = os.path.join(tmp_dir, "bg.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=#0D0D1A:size={w}x{h}:rate={fps}:duration={duration:.3f}",
            "-vf",
            (
                f"gradients=size={w}x{h}:x0=0:y0=0:x1={w}:y1={h}"
                f":c0=#0D0D2E:c1=#1A0A2E:speed=0.3:type=linear,"
                f"noise=alls=8:allf=t+u"
            ),
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            bg_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True
        )
        if result.returncode != 0:
            # Simple fallback: solid color
            return (f"color=c=#0D0D1A:size={w}x{h}:rate={fps}:duration={duration:.3f}",
                    ["-f", "lavfi"])
        return bg_path, []

    ext = os.path.splitext(bg)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        # Loop static image
        return bg, ["-loop", "1", "-t", f"{duration:.3f}"]

    # Assume video
    return bg, []


# ─────────────────────────────────────────
#  RENDER ENGINE
# ─────────────────────────────────────────

def render(
    subs: list[Subtitle],
    style: Style,
    output: str,
    bg: Optional[str] = None,
    res: tuple[int,int] = (1920, 1080),
    fps: int = 30,
    audio: Optional[str] = None,
    verbose: bool = False,
):
    if not subs:
        print("⚠  No subtitles found — nothing to render.")
        return

    duration = max(s.end for s in subs) + 0.5

    with tempfile.TemporaryDirectory() as tmp:
        bg_src, bg_flags = prepare_background(bg, duration, res, fps, tmp)
        filter_chain     = build_filter_chain(subs, style, duration, res)

        cmd = ["ffmpeg", "-y"]

        # Background input
        if bg_flags:
            cmd += bg_flags
        cmd += ["-i", bg_src]

        # Optional audio
        if audio:
            cmd += ["-i", audio]

        # Video filter
        cmd += ["-vf", filter_chain]

        # Output settings
        cmd += [
            "-t", f"{duration:.3f}",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
        ]

        if audio:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-map", "0:v", "-map", "1:a"]
        else:
            cmd += ["-an"]

        cmd.append(output)

        if verbose:
            print("\n▶ FFmpeg command:")
            print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
            print()

        print(f"⏳ Rendering with style: '{style.name}' …")
        result = subprocess.run(cmd,
                                capture_output=not verbose,
                                text=True)

        if result.returncode != 0:
            print("❌ FFmpeg error:")
            print(result.stderr[-3000:] if result.stderr else "(no output)")
            sys.exit(1)

    size_mb = os.path.getsize(output) / 1_048_576
    print(f"✅ Done → {output}  ({size_mb:.1f} MB, {duration:.1f}s @ {fps}fps)")


# ─────────────────────────────────────────
#  RESOLUTION PRESETS
# ─────────────────────────────────────────

RESOLUTIONS = {
    "1080p":   (1920, 1080),
    "720p":    (1280, 720),
    "4k":      (3840, 2160),
    "square":  (1080, 1080),
    "portrait":(1080, 1920),
    "reel":    (1080, 1920),
    "stories": (1080, 1920),
}


# ─────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────

def cli():
    parser = argparse.ArgumentParser(
        prog="srt2motion",
        description="🎬  SRT → Motion Graphics — convert subtitles into stunning video captions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  # Basic: generate 1080p video from SRT
  python srt2motion.py captions.srt

  # TikTok portrait with neon style
  python srt2motion.py captions.srt --style neon --res portrait --output tiktok.mp4

  # Overlay on existing video
  python srt2motion.py captions.srt --bg-video myvideo.mp4 --style bold

  # List all available styles
  python srt2motion.py --list-styles
        """,
    )

    parser.add_argument("srt", nargs="?", help="Input .srt file")
    parser.add_argument("-o", "--output",    default="output.mp4",    help="Output video path (default: output.mp4)")
    parser.add_argument("-s", "--style",     default="pill",          choices=list(STYLES.keys()), help="Caption style preset")
    parser.add_argument("--bg-video",        metavar="FILE",          help="Background video or image to overlay captions on")
    parser.add_argument("--audio",           metavar="FILE",          help="Audio track to embed")
    parser.add_argument("--res",             default="1080p",         choices=list(RESOLUTIONS.keys()), help="Output resolution preset")
    parser.add_argument("--fps",             default=30, type=int,    help="Frames per second (default: 30)")
    parser.add_argument("--fontsize",        type=int,                help="Override font size")
    parser.add_argument("--font-color",      metavar="COLOR",         help="Override font color (e.g. white, #FF0000)")
    parser.add_argument("-v", "--verbose",   action="store_true",     help="Print FFmpeg command")
    parser.add_argument("--list-styles",     action="store_true",     help="List all available styles and exit")
    parser.add_argument("--preview-style",   metavar="STYLE",         help="Render a 5-second test clip for a given style")

    args = parser.parse_args()

    if args.list_styles:
        print("\n🎨  Available styles:\n")
        for name, s in STYLES.items():
            print(f"  {name:<14}  {s.description}")
        print()
        return

    if args.preview_style:
        # Generate preview with sample subtitle
        sample = [
            Subtitle(1, 0.5, 2.5, "Style Preview — SRT2Motion"),
            Subtitle(2, 2.8, 5.0, "Stunning captions made easy ✨"),
        ]
        st = STYLES.get(args.preview_style)
        if not st:
            print(f"Unknown style: {args.preview_style}")
            sys.exit(1)
        out = f"preview_{args.preview_style}.mp4"
        render(sample, st, out, bg=None, res=(1280, 720), fps=30, verbose=args.verbose)
        return

    if not args.srt:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.srt):
        print(f"❌  File not found: {args.srt}")
        sys.exit(1)

    subs  = parse_srt(args.srt)
    style = STYLES[args.style]
    res   = RESOLUTIONS[args.res]

    # Apply overrides
    if args.fontsize:
        style.fontsize = args.fontsize
    if args.font_color:
        style.font_color = args.font_color

    print(f"📄  Parsed {len(subs)} subtitles from {args.srt}")

    render(
        subs,
        style,
        output=args.output,
        bg=args.bg_video,
        res=res,
        fps=args.fps,
        audio=args.audio,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    cli()
