---
name: slide-video
description: "Gen presentation MP4 videos via matplotlib + ffmpeg."
version: 1.0.0
platforms: [linux, macos, windows]
---

# Slide Video Generator

## When to use

Use when users request: presentation videos, hackathon demo videos, product explainer videos, slide-based MP4, animated explainer without recording screen, or any MP4 that explains a system/product/concept through sequential slides with text, diagrams, and terminal-style demos.

## What this produces

A self-contained Python script that generates a 1920x1080 H264 MP4 video composed of N slides — each slide is a matplotlib-rendered frame with text, boxes, arrows, and diagram elements, connected by smooth fade-in/fade-out transitions. The video is encoded by ffmpeg from the rendered frames.

## Requirements

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.10+ | Frame rendering | preinstalled |
| matplotlib | Drawing text/shapes/diagrams | `pip install matplotlib` |
| numpy | Array math for fade transitions | comes with matplotlib |
| Pillow | Frame image handling | `pip install Pillow` |
| ffmpeg | Frame to MP4 encoding | `apt install ffmpeg` |

Verify:
```bash
python3 -c "import matplotlib, numpy; print('ok')"
ffmpeg -version | head -1
```

## Creative Standard

- **One slide, one idea.** Don't pack multiple concepts per slide.
- **Color palette is cohesive.** Pick one palette and stick to it.
- **Fade transitions are 8 frames (~0.27s).** Gentle, not jarring.
- **2.5s per slide minimum.** Viewer needs time to read.
- **Terminal demos are the killer feature.** Show real curl/CLI output.
- **Font: always monospace.** Proportional fonts look bad in matplotlib.

## Color Palettes

| Palette | BG | Primary | Secondary | Accent | Warn |
|---------|---|---------|-----------|--------|------|
| **Neon tech** | `#0A0A1A` | `#00F5FF` | `#FF00AA` | `#39FF14` | `#FFD93D` |
| **Classic blue** | `#1C1C1C` | `#58C4DD` | `#83C167` | `#FFFF00` | `#FF6B6B` |
| **Warm** | `#2D2B55` | `#FF6B6B` | `#FFD93D` | `#6BCB77` | `#FF9F45` |

## Pipeline

```
PLAN -> CODE -> RENDER -> ENCODE -> VERIFY
```

1. **PLAN** — Decide slide list: title, problem, solution, architecture, features, live demo, tech stack, closing.
2. **CODE** — Write `gen-video.py` using the template below.
3. **RENDER** — `python3 gen-video.py` generates PNG frames to a temp dir.
4. **ENCODE** — ffmpeg stitches frames into MP4 (H264, yuv420p, CRF 18).
5. **VERIFY** — `ffprobe` confirms duration, resolution, codec.

## Script Template

```python
#!/usr/bin/env python3
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from PIL import Image
import subprocess, os, tempfile, glob

BG = "#0A0A1A"; FG = "#EAEAFF"; PRIMARY = "#00F5FF"
SECONDARY = "#FF00AA"; ACCENT = "#39FF14"; WARN = "#FFD93D"
MUTED = "#555577"
W, H = 1920, 1080; FPS = 30; DPI = 100
FRAMES_PER_SLIDE = 75; FADE_FRAMES = 8
OUT_DIR = tempfile.mkdtemp(prefix="slides_")

def new_fig():
    fig = plt.figure(figsize=(W//DPI, H//DPI), dpi=DPI, facecolor=BG)
    ax = fig.add_axes([0,0,1,1])
    ax.set_xlim(0, W); ax.set_ylim(0, H)
    ax.set_facecolor(BG); ax.axis("off"); ax.invert_yaxis()
    return fig, ax

def text(ax, x, y, s, size=36, color=FG, weight="normal", ha="center"):
    ax.text(x, y, s, fontsize=size, color=color, fontweight=weight,
           ha=ha, va="center", family="monospace")

def box(ax, x, y, w, h, label, fc, tc=FG, ec=None, fs=24, lw=2):
    ec = ec or fc
    ax.add_patch(FancyBboxPatch((x-w/2, y-h/2), w, h,
        boxstyle="round,pad=10,rounding_size=20",
        facecolor=fc, edgecolor=ec, linewidth=lw, alpha=0.85))
    text(ax, x, y, label, size=fs, color=tc)

def arrow(ax, x1, y1, x2, y2, color=PRIMARY, lw=2.5):
    ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),
        arrowstyle="-|>", mutation_scale=25, color=color, linewidth=lw))

# ── Slides ──
SLIDES = []

def slide_title(ax):
    text(ax, W//2, H//3, "Title", size=96, color=PRIMARY, weight="bold")
    text(ax, W//2, H//3+120, "Subtitle", size=40, color=FG)
SLIDES.append(slide_title)

def slide_problem(ax):
    text(ax, W//2, 120, "THE PROBLEM", size=52, color=WARN, weight="bold")
    items = [("Item 1", "desc 1"), ("Item 2", "desc 2")]
    for i, (t, d) in enumerate(items):
        y = 300 + i * 140
        box(ax, 500, y, 700, 100, t, "#1A1A2E", tc=WARN, ec=WARN)
        text(ax, 1100, y, d, size=22, color=MUTED, ha="left")
SLIDES.append(slide_problem)

def slide_arch(ax):
    box(ax, 300, 400, 300, 120, "A", "#1A1A2E", tc=PRIMARY, ec=PRIMARY)
    box(ax, 960, 400, 300, 120, "B", "#1A1A2E", tc=SECONDARY, ec=SECONDARY)
    arrow(ax, 450, 400, 810, 400)
SLIDES.append(slide_arch)

def slide_demo(ax):
    box(ax, W//2, H//2, W-200, H//2, "", "#0D0D1A", ec="#1A1A2E", lw=1)
    lines = [("$ command", ACCENT), ("output", MUTED)]
    for i, (l, c) in enumerate(lines):
        text(ax, 180, 260 + i*40, l, size=20, color=c, ha="left")
SLIDES.append(slide_demo)

def slide_end(ax):
    text(ax, W//2, H//2, "Thank You", size=96, color=PRIMARY, weight="bold")
SLIDES.append(slide_end)

# ── Render with fades ──
frame_idx = 0
for slide_num, draw_fn in enumerate(SLIDES):
    fig, ax = new_fig()
    draw_fn(ax)
    static_path = os.path.join(OUT_DIR, f"static_{slide_num}.png")
    fig.savefig(static_path, facecolor=BG, dpi=DPI)
    plt.close(fig)
    static_arr = np.array(Image.open(static_path).convert("RGB"))

    for f in range(FRAMES_PER_SLIDE):
        fig, ax = new_fig()
        if f < FADE_FRAMES:
            a = f / FADE_FRAMES
            blend = (static_arr * a + np.array([10,10,26]) * (1-a)).astype(np.uint8)
        elif f > FRAMES_PER_SLIDE - FADE_FRAMES:
            a = (FRAMES_PER_SLIDE - f) / FADE_FRAMES
            blend = (static_arr * a + np.array([10,10,26]) * (1-a)).astype(np.uint8)
        else:
            blend = static_arr
        ax.imshow(blend, extent=[0, W, H, 0], aspect="auto")
        fig.savefig(os.path.join(OUT_DIR, f"f{frame_idx:05d}.png"),
                    facecolor=BG, dpi=DPI)
        plt.close(fig)
        frame_idx += 1
    print(f"  Slide {slide_num+1}/{len(SLIDES)}")

# ── Encode ──
output = "output.mp4"
subprocess.run(["ffmpeg", "-y", "-framerate", str(FPS),
    "-i", os.path.join(OUT_DIR, "f%05d.png"),
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
    "-preset", "fast", output], capture_output=True, text=True, timeout=300)

for f in glob.glob(os.path.join(OUT_DIR, "*.png")): os.unlink(f)
os.rmdir(OUT_DIR)
print(f"Done: {output} | {frame_idx/FPS:.1f}s | {W}x{H}")
```

## Slide Patterns

### Title
```python
text(ax, W//2, H//3, "Title", size=96, color=PRIMARY, weight="bold")
text(ax, W//2, H//3+120, "Tagline", size=40, color=FG)
```

### Problem list
```python
text(ax, W//2, 120, "THE PROBLEM", size=52, color=WARN, weight="bold")
for i, (t, d) in enumerate(items):
    y = 300 + i * 140
    box(ax, 500, y, 700, 100, t, "#1A1A2E", tc=WARN, ec=WARN)
    text(ax, 1100, y, d, size=22, color=MUTED, ha="left")
```

### Architecture diagram
```python
box(ax, 300, 400, 300, 120, "A", "#1A1A2E", tc=PRIMARY, ec=PRIMARY)
box(ax, 960, 400, 300, 120, "B", "#1A1A2E", tc=SECONDARY, ec=SECONDARY)
arrow(ax, 450, 400, 810, 400)
```

### Terminal demo
```python
box(ax, W//2, H//2, W-200, H//2, "", "#0D0D1A", ec="#1A1A2E", lw=1)
for i, (l, c) in enumerate(lines):
    text(ax, 180, 260 + i*40, l, size=20, color=c, ha="left")
```

## Timing

| Element | Duration | Frames |
|---------|----------|--------|
| Title | 2.5s | 75 |
| Content slide | 2.5s | 75 |
| Terminal demo | 3.0s | 90 |
| Fade | 0.27s | 8 frames |

## Pitfalls

- **Emoji missing** — DejaVu Sans Mono lacks emoji. Use `[PKG]` not packing chars.
- **fig.savefig facecolor** — always pass `facecolor=BG` or white borders appear.
- **ax.invert_yaxis()** — essential for screen coordinates.
- **ax.add_axes([0,0,1,1])** — full-figure axes, no default padding.
- **Nested quotes** — JSON examples cause Python syntax errors. Escape carefully.
- **Cleanup** — delete frame PNGs after encoding.

## Verification

```bash
ffprobe -v quiet -print_format json -show_format -show_streams output.mp4
```

## Advantages

| Approach | Dependencies | Disk |
|---------|-------------|------|
| **This skill** | matplotlib + ffmpeg | ~50MB |
| Manim | LaTeX + Manim + ffmpeg | ~2GB |
| Puppeteer + HTML | Chromium | ~400MB |
