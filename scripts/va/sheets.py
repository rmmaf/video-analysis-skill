"""Contact sheets (sheet_NNN.jpg) and the per-batch overview (overview.jpg); spec section 6, WP2.

Layout: columns by tile count (5 up to 20, 6 up to 30, else 8); tile width =
floor((sheet_width - gap*(cols+1)) / cols); tile height follows the frames' aspect; a label band
under each tile (consola.ttf 14 px, fallback ImageFont.load_default(size=14)); 4 px gaps. The
canvas is as wide as the columns in use. Thumbnails: Image.draft at about 2x the tile, then
LANCZOS, decoded by up to 4 threads (each one independent, so the result does not depend on
scheduling). Saved as JPEG (quality 80, optimize=True) with no metadata, written atomically, so
equal inputs give byte-identical files.
"""
from __future__ import annotations

import functools
import io
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .batching import image_tokens
from .common import as_posix, atomic_write_bytes, fmt_hms

__all__ = ["load_font", "make_contact_sheet", "make_overview", "sample_evenly", "sheet_columns",
           "sheet_layout", "short_time"]

BACKGROUND = (24, 24, 24)
BAND = (0, 0, 0)
TEXT = (255, 255, 255)
MISSING = (96, 96, 96)
_THREADS = max(1, min(4, os.cpu_count() or 1))   # thumbnail decoding threads


def short_time(ms: int) -> str:
    """Tile label time: "02:54.5" below one hour, "1:02:54.5" above."""
    hours, rest = fmt_hms(ms / 1000.0, 1).split(":", 1)
    return rest if int(hours) == 0 else f"{int(hours)}:{rest}"


def sheet_columns(n: int, params: dict) -> int:
    if n <= 20:
        return params["sheet_cols_upto20"]
    if n <= 30:
        return params["sheet_cols_upto30"]
    return params["sheet_cols_more"]


def sheet_layout(n: int, aspect: Sequence[int], params: dict) -> dict:
    """Geometry of a sheet with n tiles: cols, rows, tile size, canvas size, image tokens."""
    n = max(1, n)
    cols = sheet_columns(n, params)
    gap, band = params["sheet_gap_px"], params["sheet_label_px"]
    tile_w = max(1, (params["sheet_width"] - gap * (cols + 1)) // cols)
    aw, ah = (int(aspect[0]), int(aspect[1])) if aspect and aspect[0] > 0 and aspect[1] > 0 else (16, 9)
    tile_h = max(1, (tile_w * ah + aw // 2) // aw)
    used = min(cols, n)
    rows = -(-n // cols)
    width = used * tile_w + (used + 1) * gap
    height = rows * (tile_h + band) + (rows + 1) * gap
    return {"cols": cols, "rows": rows, "tile_w": tile_w, "tile_h": tile_h, "width": width, "height": height,
            "tokens": image_tokens(width, height, params["image_token_px"])}


@functools.lru_cache(maxsize=8)
def load_font(path: str, size: int):
    """TrueType font at ``path`` (consola.ttf), else Pillow's default font at the same size."""
    if path and os.path.isfile(path):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _thumbnail(path: str, tile_w: int, tile_h: int) -> Image.Image | None:
    try:
        with Image.open(path) as im:
            im.draft("RGB", (2 * tile_w, 2 * tile_h))
            img = im.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    w, h = img.size
    if w > tile_w or h > tile_h:
        img.thumbnail((tile_w, tile_h), Image.Resampling.LANCZOS)
    else:  # tiny sources (tests): scale up to fit the tile
        if w * tile_h >= h * tile_w:
            size = (tile_w, max(1, (h * tile_w + w // 2) // w))
        else:
            size = (max(1, (w * tile_h + h // 2) // h), tile_h)
        img = img.resize(size, Image.Resampling.LANCZOS)
    return img


def _fit_label(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    while text and draw.textlength(text, font=font) > max_w:
        text = text[:-1]
    return text


def _thumbnails(paths: list[str], tile_w: int, tile_h: int) -> dict[str, Image.Image | None]:
    """Decode and shrink each distinct image once. Pillow releases the GIL while decoding and
    resampling, so a few threads cut the time ~3x; each result is independent, so the output
    stays byte-identical."""
    unique = list(dict.fromkeys(paths))
    workers = min(_THREADS, len(unique))
    if workers <= 1:
        return {p: _thumbnail(p, tile_w, tile_h) for p in unique}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(unique, pool.map(lambda p: _thumbnail(p, tile_w, tile_h), unique)))


def _render(tiles: Sequence[dict], out_path: Path, layout: dict, params: dict) -> dict:
    gap, band, font_px = params["sheet_gap_px"], params["sheet_label_px"], params["sheet_font_px"]
    tw, th, cols = layout["tile_w"], layout["tile_h"], layout["cols"]
    canvas = Image.new("RGB", (layout["width"], layout["height"]), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    font = load_font(params["sheet_font"], font_px)
    cache = _thumbnails([tile["path"] for tile in tiles], tw, th)
    missing: list[str] = []
    for k, tile in enumerate(tiles):
        r, c = divmod(k, cols)
        x = gap + c * (tw + gap)
        y = gap + r * (th + band + gap)
        thumb = cache[tile["path"]]
        if thumb is None:
            draw.rectangle([x, y, x + tw - 1, y + th - 1], fill=MISSING)
            missing.append(tile.get("id") or tile["path"])
        else:
            canvas.paste(thumb, (x + (tw - thumb.width) // 2, y + (th - thumb.height) // 2))
        draw.rectangle([x, y + th, x + tw - 1, y + th + band - 1], fill=BAND)
        label = _fit_label(draw, tile.get("label", ""), font, tw - 8)
        draw.text((x + 4, y + th + max(0, (band - font_px) // 2 - 1)), label, font=font, fill=TEXT)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=params["sheet_jpeg_quality"], optimize=True)
    atomic_write_bytes(out_path, buf.getvalue())
    return {"path": as_posix(os.path.abspath(out_path)), "size": [layout["width"], layout["height"]],
            "tokens": layout["tokens"], "n_tiles": len(tiles), "missing": missing}


def make_contact_sheet(tiles: Sequence[dict], out_path: str | os.PathLike, aspect: Sequence[int], params: dict) -> dict:
    """One tile per frame of a batch. ``tiles``: [{"path": jpg, "label": "F0041 02:54.5 M", "id": "F0041"}].
    -> {"path", "size": [w, h], "tokens", "n_tiles", "missing": [ids of unreadable images]}."""
    if not tiles:
        raise ValueError("contact sheet needs at least one tile")
    return _render(tiles, Path(out_path), sheet_layout(len(tiles), aspect, params), params)


def sample_evenly(n: int, m: int) -> list[int]:
    """m indexes spread evenly over range(n), first and last included (all of them if n <= m)."""
    if n <= m:
        return list(range(n))
    if m <= 1:
        return [0]
    return [(2 * k * (n - 1) + (m - 1)) // (2 * (m - 1)) for k in range(m)]


def make_overview(tiles: Sequence[dict], out_path: str | os.PathLike, aspect: Sequence[int], params: dict) -> dict:
    """One tile per batch ("B003 · 00:02:54"), sampled evenly above overview_max_batches."""
    if not tiles:
        raise ValueError("overview needs at least one tile")
    picked = [tiles[i] for i in sample_evenly(len(tiles), params["overview_max_batches"])]
    return _render(picked, Path(out_path), sheet_layout(len(picked), aspect, params), params)
