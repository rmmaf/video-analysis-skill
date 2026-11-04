"""Synthetic videos with ground truth for the frame engine (design-deterministic.md section 9).

Frames are rendered with Pillow/numpy at 1280x720, 30 fps (seeded, deterministic) and piped to
``ffmpeg -f rawvideo ... -c:v libx264 -preset veryfast -crf 28 -g 180 -bf 0``. Variants: CRF 32,
``-bf 2`` (exercises -skip_frame noref) and VFR (frames dropped in an irregular pattern with
``select`` + ``-fps_mode vfr``; timestamps keep the content timeline). Each video gets a
``<name>.truth.json`` and a matching ``<name>.vtt``.

The camera scenario is rendered here too (textured scene with pan, hand jitter, flicker and
sensor noise), instead of lavfi testsrc2, which is mostly static and not camera-like.

    python tests/synth.py OUT_DIR [scenario ...]        # render for manual inspection

Videos are cached per SCENARIO_VERSION under %TEMP%/va-synth-v<N> (or env VA_SYNTH_DIR).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from functools import lru_cache  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

SCENARIO_VERSION = 5
W, H, FPS = 1280, 720, 30
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

FONT_FILES = ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/consola.ttf")
MONO_FILES = ("C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf")


@lru_cache(maxsize=None)
def font(size: int, mono: bool = False) -> ImageFont.ImageFont:
    for f in (MONO_FILES if mono else FONT_FILES):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def synth_dir() -> Path:
    env = os.environ.get("VA_SYNTH_DIR", "").strip()
    base = Path(env) if env else Path(tempfile.gettempdir()) / f"va-synth-v{SCENARIO_VERSION}"
    base.mkdir(parents=True, exist_ok=True)
    return base


# --- drawing helpers -----------------------------------------------------------------------

def canvas(color=(245, 245, 245)) -> Image.Image:
    return Image.new("RGB", (W, H), color)


def window(img: Image.Image, box, title: str = "", bar=(45, 45, 52), body=(255, 255, 255)) -> None:
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    d.rectangle([x0, y0, x1, y1], fill=body, outline=(180, 180, 180))
    d.rectangle([x0, y0, x1, y0 + 28], fill=bar)
    if title:
        d.text((x0 + 10, y0 + 5), title, fill=(230, 230, 230), font=font(15))


def text_lines(img: Image.Image, xy, lines, size=18, fill=(30, 30, 30), gap=8, mono=False) -> None:
    d = ImageDraw.Draw(img)
    x, y = xy
    for ln in lines:
        d.text((x, y), ln, fill=fill, font=font(size, mono))
        y += size + gap


def arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.uint8).copy()


LOREM = ("dados de vendas por regional e periodo com a meta do mes e o realizado ate ontem incluindo "
         "devolucoes cancelamentos e ajustes manuais feitos pelo time comercial na planilha consolidada").split()


def para(n_words: int, seed: int, width: int = 9) -> list[str]:
    rng = np.random.default_rng(seed)
    words = [LOREM[i] for i in rng.integers(0, len(LOREM), n_words)]
    return [" ".join(words[i:i + width]) for i in range(0, n_words, width)]


@lru_cache(maxsize=None)
def pointer_sprite(scale: float = 1.0) -> np.ndarray:
    """Arrow pointer as RGBA (white with black outline), ~14x22 px at 720p."""
    s = scale
    pts = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12)]
    w, h = int(14 * s) + 2, int(22 * s) + 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.polygon([(1 + x * s, 1 + y * s) for x, y in pts], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return np.asarray(im, dtype=np.uint8).copy()


def blit(buf: np.ndarray, sprite: np.ndarray, x: int, y: int) -> None:
    """Alpha-composite an RGBA sprite onto an RGB buffer at (x, y), clipped."""
    h, w = sprite.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(buf.shape[1], x + w), min(buf.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sp = sprite[y0 - y:y1 - y, x0 - x:x1 - x]
    a = sp[..., 3:4].astype(np.uint16)
    dst = buf[y0:y1, x0:x1]
    dst[:] = ((sp[..., :3].astype(np.uint16) * a + dst.astype(np.uint16) * (255 - a)) // 255).astype(np.uint8)


def rect(buf: np.ndarray, x0, y0, x1, y1, color) -> None:
    buf[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = color


def ring(buf: np.ndarray, box, color=(120, 90, 230), t=3) -> None:
    x0, y0, x1, y1 = box
    rect(buf, x0, y0, x1, y0 + t, color)
    rect(buf, x0, y1 - t, x1, y1, color)
    rect(buf, x0, y0, x0 + t, y1, color)
    rect(buf, x1 - t, y0, x1, y1, color)


def disk(buf: np.ndarray, cx, cy, r, color) -> None:
    y0, y1, x0, x1 = int(cy - r - 1), int(cy + r + 2), int(cx - r - 1), int(cx + r + 2)
    y0, x0 = max(0, y0), max(0, x0)
    y1, x1 = min(buf.shape[0], y1), min(buf.shape[1], x1)
    if y1 <= y0 or x1 <= x0:
        return
    yy, xx = np.ogrid[y0:y1, x0:x1]
    m = (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
    buf[y0:y1, x0:x1][m] = color


class Webcam:
    """A presenter-cam tile: gradient room, a head-and-shoulders sprite that moves, sensor noise.
    Updated at 15 fps like a Teams camera."""

    def __init__(self, w: int, h: int, seed: int):
        self.w, self.h, self.seed = w, h, seed
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        bg = np.zeros((h, w, 3), np.float32)
        bg[..., 0] = 90 + 60 * xx / w
        bg[..., 1] = 80 + 40 * yy / h
        bg[..., 2] = 70 + 30 * (1 - xx / w)
        self.bg = bg.astype(np.uint8)
        sw, sh = int(w * 0.7), int(h * 0.95)
        im = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        rx, ry = w * 0.13, h * 0.22
        cx, cy = sw / 2, h * 0.42
        d.ellipse([sw / 2 - w * 0.32, h * 0.63, sw / 2 + w * 0.32, h * 1.47], fill=(40, 60, 110, 255))
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(205, 160, 130, 255))
        for ex in (cx - rx * 0.35, cx + rx * 0.35):
            d.ellipse([ex - rx * 0.1, cy - ry * 0.25, ex + rx * 0.1, cy - ry * 0.05], fill=(30, 30, 30, 255))
        self.sprite = np.asarray(im, dtype=np.uint8).copy()
        rng = np.random.default_rng(seed)
        self.noise = [rng.integers(-9, 10, (h, w, 3), dtype=np.int16) for _ in range(12)]
        self._last: tuple[int, np.ndarray] | None = None

    def frame(self, i: int) -> np.ndarray:
        j = i // 2
        if self._last is not None and self._last[0] == j:
            return self._last[1]
        t = j / 15.0
        w, h = self.w, self.h
        x = int(w * 0.5 - self.sprite.shape[1] / 2 + w * 0.08 * math.sin(t * 1.3) + w * 0.03 * math.sin(t * 3.1))
        y = int(h * 0.05 * math.sin(t * 0.9) + h * 0.02)
        img = self.bg.copy()
        blit(img, self.sprite, x, y)
        out = np.clip(img.astype(np.int16) + self.noise[j % len(self.noise)], 0, 255).astype(np.uint8)
        self._last = (j, out)
        return out


def avatar(img: Image.Image, box, initials: str, color) -> None:
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=(52, 52, 60))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    r = min(x1 - x0, y1 - y0) // 4
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    f = font(max(12, r))
    tw = d.textlength(initials, font=f)
    d.text((cx - tw / 2, cy - r * 0.65), initials, fill=(255, 255, 255), font=f)


# --- scenarios ------------------------------------------------------------------------------
# Each scenario returns (duration_s, render(i) -> RGB uint8 array, truth dict).

def sc_cursor_only():
    dur = 20.0
    img = canvas()
    window(img, (40, 30, 1240, 690), "editor.txt - Bloco de Notas")
    text_lines(img, (70, 80), para(90, 1), size=20)
    base = arr(img)
    caret_x, caret_y = 70 + 610, 80 + 9 * 28
    ptr = pointer_sprite()

    def render(i):
        t = i / FPS
        b = base.copy()
        if int(t * 1000 / 530) % 2 == 0:
            rect(b, caret_x, caret_y, caret_x + 2, caret_y + 22, (0, 0, 0))
        if t < 2:
            px, py = 300, 300
        elif t < 14:
            u = t - 2
            px = 300 + 380 * math.sin(u * 0.55) + 120 * math.sin(u * 1.7)
            py = 300 + 200 * math.sin(u * 0.8 + 0.6)
        else:
            u = 12.0
            px = 300 + 380 * math.sin(u * 0.55) + 120 * math.sin(u * 1.7)
            py = 300 + 200 * math.sin(u * 0.8 + 0.6)
            if t >= 16:
                px += 30
        blit(b, ptr, int(px), int(py))
        return b

    return dur, render, {"kind": "exact", "frames": 1, "events": [0.0], "tol": 1.0, "extra": 0}


def sc_typing():
    dur = 25.0
    img = canvas()
    window(img, (40, 30, 1240, 690), "Formulario - cadastro")
    text_lines(img, (80, 90), ["Nome do cliente:", "", "Observacoes:"], size=20)
    d = ImageDraw.Draw(img)
    d.rectangle([80, 130, 1000, 170], outline=(120, 120, 120), fill=(255, 255, 255))
    base = arr(img)
    text = "Distribuidora Solar Comercial de Bebidas"   # 40 chars
    f = font(20)
    bursts = [(2.0, 10.0), (13.0, 21.0)]
    clear_t = 12.0
    rate = 5.0
    ptr = pointer_sprite()
    cache: dict[int, np.ndarray] = {}

    def typed(n: int) -> np.ndarray:
        if n not in cache:
            im = Image.fromarray(base)
            ImageDraw.Draw(im).text((88, 138), text[:n], fill=(20, 20, 20), font=f)
            cache[n] = arr(im)
        return cache[n]

    def state(t: float) -> tuple[int, bool]:
        """(chars visible, typing now)"""
        if t < bursts[0][0]:
            return 0, False
        if t < bursts[0][1]:
            return min(len(text), int((t - bursts[0][0]) * rate) + 1), True
        if t < clear_t:
            return len(text), False
        if t < bursts[1][0]:
            return 0, False
        if t < bursts[1][1]:
            return min(len(text), int((t - bursts[1][0]) * rate) + 1), True
        return len(text), False

    def render(i):
        t = i / FPS
        n, typing = state(t)
        b = typed(n).copy()
        cx = 88 + int(f.getlength(text[:n])) + 1
        if typing or int(t * 1000 / 530) % 2 == 0:
            rect(b, cx, 136, cx + 2, 164, (0, 0, 0))
        blit(b, ptr, 1100, 600)
        return b

    ref = typed(len(text))
    return dur, render, {"kind": "typing", "bursts": bursts, "events": [0.0], "tol": 1.0,
                         "text_box": [80, 130, 1000, 170], "reference": ref}


def _slide(title: str, bullets: list[str], color=(255, 255, 255)) -> np.ndarray:
    img = canvas(color)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 90], fill=(0, 70, 140))
    d.text((60, 22), title, fill=(255, 255, 255), font=font(40))
    y = 150
    for b in bullets:
        d.ellipse([70, y + 14, 82, y + 26], fill=(0, 70, 140))
        d.text((100, y), b, fill=(30, 30, 30), font=font(30))
        y += 70
    return arr(img)


def sc_slides_cam():
    dur = 90.0
    slides = [
        (0.0, _slide("Arquitetura de dados", ["Fontes: SAP, planilhas, APIs", "Camadas bronze, prata e ouro", "Orquestracao no ADF"])),
        (20.0, _slide("Ingestao", ["Pipelines por fonte", "Parametros por tabela", "Monitoramento diario"])),
        (30.0, _slide("Ingestao", ["Pipelines por fonte", "Parametros por tabela", "Monitoramento diario", "Alertas no Teams"])),
        (45.0, _slide("Transformacao", ["dbt sobre Databricks", "Testes de qualidade", "Documentacao automatica"], (250, 250, 240))),
        (70.0, _slide("Proximos passos", ["Migrar fontes legadas", "Catalogo unico", "Painel de custos"])),
    ]
    cam = Webcam(256, 144, seed=7)
    cx0, cy0 = W - 256, H - 144

    def render(i):
        t = i / FPS
        base = slides[0][1]
        for t0, s in slides:
            if t >= t0:
                base = s
        b = base.copy()
        b[cy0:cy0 + 144, cx0:cx0 + 256] = cam.frame(i)
        if 50.0 <= t < 60.0 or 5.0 <= t < 12.0:
            u = t * 1.4
            disk(b, 640 + 380 * math.sin(u), 380 + 150 * math.sin(u * 1.7), 5, (255, 20, 20))
        return b

    return dur, render, {"kind": "events", "events": [0.0, 20.0, 30.0, 45.0, 70.0], "tol": 1.0, "extra": 1}


def sc_meeting_layouts():
    dur = 120.0
    # screen share layout: doc in [0, 1024), participant column [1024, 1280)
    def share(doc_seed: int, extra_lines: int = 0, page: int = 0) -> Image.Image:
        img = canvas((32, 32, 36))
        window(img, (0, 0, 1023, 719), "Relatorio de vendas.docx")
        lines = para(120, doc_seed + page)
        text_lines(img, (40, 60), lines[:10 + extra_lines], size=19)
        col = [(1024, 150, 1280, 300, "AB", (200, 80, 60)), (1024, 300, 1280, 450, "CS", (60, 140, 90)),
               (1024, 450, 1280, 600, "MR", (70, 90, 200))]
        for x0, y0, x1, y1, ini, c in col:
            avatar(img, (x0 + 4, y0 + 4, x1 - 4, y1 - 4), ini, c)
        return img

    s0 = arr(share(10))
    s1 = arr(share(10, extra_lines=3))
    s2 = arr(share(10, extra_lines=3, page=5))
    gal = canvas((24, 24, 28))
    tiles = [(0, 0, 640, 360), (640, 0, 1280, 360), (0, 360, 640, 720), (640, 360, 1280, 720)]
    for (x0, y0, x1, y1), (ini, c) in zip(tiles[1:], [("AB", (200, 80, 60)), ("CS", (60, 140, 90)), ("MR", (70, 90, 200))]):
        avatar(gal, (x0 + 6, y0 + 6, x1 - 6, y1 - 6), ini, c)
    g0 = arr(gal)
    # third layout: full-width doc, changes at 90 / 105 inside the former webcam tile area (top right)
    def full(n_extra: int) -> np.ndarray:
        img = canvas()
        window(img, (0, 0, 1279, 719), "Planejamento.xlsx")
        text_lines(img, (40, 60), para(60, 33)[:9], size=19)
        notes = ["Meta revisada: 12%", "Responsavel: Carla", "Prazo: 30/11", "Status: aprovado"]
        text_lines(img, (1040, 40), notes[:n_extra], size=19, fill=(160, 20, 20))
        return arr(img)

    f0, f1, f2 = full(0), full(2), full(4)
    cam = Webcam(256, 144, seed=3)
    cam_g = Webcam(628, 348, seed=4)

    def render(i):
        t = i / FPS
        on = int(t // 4) % 2 == 1
        if t < 40.0:
            b = (s0 if t < 10 else s1 if t < 25 else s2).copy()
            b[0:144, 1024:1280] = cam.frame(i)
            if on:
                ring(b, (1026, 302, 1278, 448))
        elif t < 80.0:
            b = g0.copy()
            b[6:354, 6:634] = cam_g.frame(i)
            if on:
                ring(b, (646, 6, 1274, 354))
            elif int(t // 6) % 2 == 0:
                ring(b, (6, 366, 634, 714))
        else:
            b = (f0 if t < 90 else f1 if t < 105 else f2).copy()
        return b

    return dur, render, {"kind": "events", "events": [0.0, 10.0, 25.0, 40.0, 80.0, 90.0, 105.0], "tol": 1.0,
                         "extra": 3, "must": [90.0, 105.0]}


def sc_scroll():
    dur = 40.0
    page = Image.new("RGB", (1200, 4200), (255, 255, 255))
    text_lines(page, (30, 20), para(1400, 21, width=11), size=20, gap=10)
    doc = arr(page)
    frame = canvas()
    window(frame, (40, 0, 1240, 719), "Documento longo - leitura")
    base = arr(frame)
    speed = 60.0

    def render(i):
        t = i / FPS
        off = 0.0 if t < 5 else min(15.0, t - 5.0) * speed
        b = base.copy()
        o = int(off)
        b[30:719, 40:1240] = doc[o:o + 689, 0:1200]
        return b

    return dur, render, {"kind": "scroll", "window": [5.0, 22.0], "max_frames": 5, "settled_at": 21.0,
                         "events": [0.0], "tol": 1.0}


def sc_transient():
    dur = 20.0
    img = canvas()
    window(img, (40, 30, 1240, 690), "Painel de controle")
    text_lines(img, (80, 120), para(60, 5), size=20)
    d = ImageDraw.Draw(img)
    d.rectangle([80, 70, 200, 100], fill=(0, 100, 200))
    d.text((95, 74), "Arquivo", fill=(255, 255, 255), font=font(18))
    base = arr(img)
    tip = Image.fromarray(base.copy())
    dt = ImageDraw.Draw(tip)
    dt.rectangle([210, 100, 470, 130], fill=(255, 255, 225), outline=(90, 90, 90))
    dt.text((218, 104), "Abrir um arquivo existente", fill=(0, 0, 0), font=font(16))
    tip = arr(tip)
    menu = Image.fromarray(base.copy())
    dm = ImageDraw.Draw(menu)
    dm.rectangle([80, 100, 330, 330], fill=(250, 250, 250), outline=(120, 120, 120))
    for k, item in enumerate(["Novo", "Abrir...", "Salvar", "Salvar como...", "Exportar", "Sair"]):
        dm.text((96, 110 + k * 36), item, fill=(20, 20, 20), font=font(19))
    menu = arr(menu)
    clocks: dict[int, np.ndarray] = {}

    def clock(sec: int) -> np.ndarray:
        if sec not in clocks:
            im = Image.new("RGB", (110, 26), (45, 45, 52))
            ImageDraw.Draw(im).text((6, 3), f"14:{(sec // 60) % 60:02d}:{sec % 60:02d}", fill=(230, 230, 230),
                                    font=font(17, mono=True))
            clocks[sec] = arr(im)
        return clocks[sec]

    def render(i):
        t = i / FPS
        if 10.0 <= t < 13.0:
            b = menu.copy()
        elif 5.0 <= t < 5.3:
            b = tip.copy()
        else:
            b = base.copy()
        b[31:57, 1120:1230] = clock(37 * 60 + 50 + int(t))
        return b

    return dur, render, {"kind": "events", "events": [0.0, 10.0, 13.0], "tol": 1.0, "extra": 0}


def sc_small_changes():
    dur = 20.0
    def state(digit: str, words: bool) -> np.ndarray:
        img = canvas()
        window(img, (40, 30, 1240, 690), "Resumo financeiro")
        d = ImageDraw.Draw(img)
        rows = [("Pedidos", "1.284"), ("Clientes ativos", "312"), ("Ticket medio", f"R$ 87,1{digit}")]
        y = 110
        for a, b in rows:
            d.text((90, y), a, fill=(40, 40, 40), font=font(20))
            d.text((420, y), b, fill=(40, 40, 40), font=font(20))
            y += 44
        if words:
            d.text((90, y + 20), "Receita liquida", fill=(0, 90, 30), font=font(20))
        return arr(img)

    s0, s1, s2 = state("4", False), state("9", False), state("9", True)

    def render(i):
        t = i / FPS
        return (s0 if t < 5 else s1 if t < 12 else s2).copy()

    return dur, render, {"kind": "events", "events": [0.0, 12.0], "tol": 1.0, "extra": 0,
                         "absent": [5.0]}


def sc_camera():
    """Handheld camera footage: a detailed scene, slow pan + hand jitter, flicker, sensor noise."""
    dur = 60.0
    rng = np.random.default_rng(99)
    low = rng.integers(0, 255, (90, 150, 3)).astype(np.uint8)
    tex = Image.fromarray(low).resize((1700, 1000), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2))
    det = rng.integers(0, 255, (500, 850, 3)).astype(np.uint8)
    det = Image.fromarray(det).resize((1700, 1000), Image.BILINEAR)
    scene = np.clip(np.asarray(tex, np.float32) * 0.55 + np.asarray(det, np.float32) * 0.45, 0, 255).astype(np.uint8)
    noise = [rng.integers(-8, 9, (H, W, 3), dtype=np.int16) for _ in range(7)]

    def render(i):
        t = i / FPS
        x = int(200 + 150 * math.sin(t * 0.2) + 6 * math.sin(t * 7.1) + 4 * math.sin(t * 13.3))
        y = int(150 + 10 * math.sin(t * 0.5) + 5 * math.sin(t * 9.7))
        b = scene[y:y + H, x:x + W].astype(np.int16)
        b += noise[i % len(noise)]
        b += int(5 * math.sin(t * 2.0))
        return np.clip(b, 0, 255).astype(np.uint8)

    return dur, render, {"kind": "camera", "max_frames": 4, "events": [0.0], "tol": 1.0}


def sc_static_text():
    dur = 30.0
    img = canvas((30, 30, 30))
    window(img, (0, 0, 1279, 719), "pipeline.py - Visual Studio Code", bar=(60, 60, 60), body=(30, 30, 30))
    rng = np.random.default_rng(42)
    d = ImageDraw.Draw(img)
    palette = [(220, 220, 170), (86, 156, 214), (206, 145, 120), (106, 153, 85), (197, 134, 192), (212, 212, 212)]
    words = ["def", "return", "select", "from", "where", "tabela", "coluna", "valor", "=", "(", ")", "df", "spark", "if"]
    y = 40
    for ln in range(34):
        x = 50 + 16 * int(rng.integers(0, 4))
        d.text((8, y), f"{ln + 1:3d}", fill=(110, 110, 110), font=font(14, mono=True))
        for _ in range(int(rng.integers(3, 11))):
            wd = words[int(rng.integers(0, len(words)))]
            d.text((x, y), wd, fill=palette[int(rng.integers(0, len(palette)))], font=font(14, mono=True))
            x += int(d.textlength(wd + " ", font=font(14, mono=True)))
        y += 19
    base = arr(img)
    ptr = pointer_sprite()

    def render(i):
        t = i / FPS
        b = base.copy()
        if int(t * 1000 / 530) % 2 == 0:
            rect(b, 420, 230, 422, 248, (230, 230, 230))
        blit(b, ptr, 900, 400)
        return b

    return dur, render, {"kind": "exact", "frames": 1, "events": [0.0], "tol": 1.0, "extra": 0}


SCENARIOS = {
    "cursor_only": sc_cursor_only,
    "typing": sc_typing,
    "slides_cam": sc_slides_cam,
    "meeting_layouts": sc_meeting_layouts,
    "scroll": sc_scroll,
    "transient": sc_transient,
    "small_changes": sc_small_changes,
    "camera": sc_camera,
    "static_text": sc_static_text,
}


# --- acceptance (design-deterministic.md section 9 table) -------------------------------------

def evaluate(doc: dict, truth: dict) -> tuple[bool, list[str]]:
    """Check a frames.json dict against a scenario's truth. Returns (ok, messages)."""
    fr = doc["frames"]
    msgs: list[str] = []
    ok = True
    pairs = [(f["start"], f["captured_at"]) for f in fr]
    kind = truth["kind"]
    tol = float(truth.get("tol", 1.0))

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        msgs.append(msg)

    def near(t: float) -> bool:
        return any(abs(s - t) <= tol or abs(c - t) <= tol for s, c in pairs)

    if kind == "exact" and len(fr) != truth["frames"]:
        fail(f"expected exactly {truth['frames']} frame(s), got {len(fr)}")
    if kind == "events":
        found, used = [], set()
        for t in truth["events"]:
            hit = next((i for i, (s, c) in enumerate(pairs) if i not in used and (abs(s - t) <= tol or abs(c - t) <= tol)), None)
            if hit is None:
                fail(f"event at {t} s not detected")
            else:
                used.add(hit)
                found.append(t)
        extra = [pairs[i][1] for i in range(len(pairs)) if i not in used]
        if len(extra) > truth.get("extra", 0):
            fail(f"extra frames {extra} > +{truth.get('extra', 0)}")
        for t in truth.get("absent", []):
            if near(t):
                fail(f"a frame near {t} s (must not be kept)")
    if kind == "typing":
        for b0, b1 in truth["bursts"]:
            inb = [f for f in fr if b0 <= f["captured_at"] <= b1 + 1.5]
            if not 1 <= len(inb) <= 4:
                fail(f"burst {b0}-{b1} s: {len(inb)} frames (want 1-4)")
            if not [f for f in inb if b1 - 0.01 <= f["captured_at"] <= b1 + 1.5 and f["kind"] in ("settled", "final")]:
                fail(f"no settled frame within 1.5 s after typing stops at {b1} s")
    if kind == "scroll":
        a, b = truth["window"]
        w = [f for f in fr if a <= f["captured_at"] <= b]
        if not w or len(w) > truth["max_frames"]:
            fail(f"{len(w)} frames in [{a}, {b}] (want 1-{truth['max_frames']})")
        elif any(f["kind"] != "motion" for f in w[:-1]) or w[-1]["kind"] != "settled":
            fail("frames during the scroll must be motion, the last one settled")
        elif abs(w[-1]["captured_at"] - truth["settled_at"]) > tol:
            fail(f"settled frame at {w[-1]['captured_at']} s, want ~{truth['settled_at']}")
    if kind == "camera":
        if len(fr) > truth["max_frames"]:
            fail(f"{len(fr)} frames > {truth['max_frames']}")
        if any(f["kind"] != "heartbeat" for f in fr[1:]):
            fail("camera frames after the first must be heartbeat")
        if not any("camera" in w for w in doc["warnings"]):
            fail("no camera-like warning")
    return ok, msgs


def final_text_diff(doc: dict, truth: dict) -> float:
    """typing: mean abs gray difference between the last kept JPEG and the reference render,
    inside the text box, both at 640x360."""
    last = doc["frames"][-1]
    im = Image.open(Path(doc["frames_dir"]) / last["file"]).convert("L").resize((640, 360), Image.BOX)
    ref = Image.fromarray(truth["reference"]).convert("L").resize((640, 360), Image.BOX)
    x0, y0, x1, y1 = (v // 2 for v in truth["text_box"])
    a = np.asarray(im, np.float32)[y0:y1, x0:x1]
    b = np.asarray(ref, np.float32)[y0:y1, x0:x1]
    return float(np.abs(a - b).mean())


# --- encoding ---------------------------------------------------------------------------------

def _vtt(dur: float, name: str) -> str:
    out = ["WEBVTT", ""]
    t = 0.5
    n = 1
    while t + 2.0 < dur:
        a, b = t, t + 2.0
        out.append(f"{_ts(a)} --> {_ts(b)}")
        out.append(f"SPEAKER_00: cena {name}, fala numero {n}.")
        out.append("")
        t += 6.0
        n += 1
    return "\n".join(out) + "\n"


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def variant_name(name: str, crf: int = 28, bf: int = 0, vfr: bool = False) -> str:
    tag = name
    if crf != 28:
        tag += f"-crf{crf}"
    if bf:
        tag += f"-bf{bf}"
    if vfr:
        tag += "-vfr"
    return tag


def render(name: str, out_dir: str | os.PathLike | None = None, *, crf: int = 28, bf: int = 0, vfr: bool = False,
           force: bool = False) -> tuple[Path, dict]:
    """Render scenario ``name`` (cached) -> (video path, truth dict). The truth dict holds the
    acceptance data of design-deterministic.md section 9 for that scenario."""
    out = Path(out_dir) if out_dir else synth_dir()
    out.mkdir(parents=True, exist_ok=True)
    tag = variant_name(name, crf, bf, vfr)
    mp4, tj, vtt = out / f"{tag}.mp4", out / f"{tag}.truth.json", out / f"{tag}.vtt"
    ref_npy = out / f"{tag}.ref.npy"
    if not force and mp4.exists() and tj.exists():
        truth = json.loads(tj.read_text(encoding="utf-8"))
        if ref_npy.exists():
            truth["reference"] = np.load(ref_npy)
        return mp4, truth
    dur, frame_fn, truth = SCENARIOS[name]()
    ref = truth.pop("reference", None)
    n = int(round(dur * FPS))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if vfr:
        cmd += ["-vf", "select='lt(mod(n,5),3)+between(t,30,40)'", "-fps_mode", "vfr"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-g", "180", "-bf", str(bf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4.with_suffix(".tmp.mp4"))]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)
    try:
        for i in range(n):
            fr = np.ascontiguousarray(frame_fn(i))
            if fr.shape != (H, W, 3) or fr.dtype != np.uint8:
                raise RuntimeError(f"{tag}: frame {i} has shape {fr.shape} {fr.dtype}")
            proc.stdin.write(fr.tobytes())
        proc.stdin.close()
        err = proc.stderr.read()
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if rc != 0:
        raise RuntimeError(f"x264 encode failed for {tag}: {err.decode('utf-8', 'replace')[-800:]}")
    os.replace(mp4.with_suffix(".tmp.mp4"), mp4)
    truth.update({"scenario": name, "variant": tag, "duration": dur, "fps": FPS, "size": [W, H], "crf": crf,
                  "bf": bf, "vfr": vfr, "version": SCENARIO_VERSION})
    tj.write_text(json.dumps(truth, indent=1), encoding="utf-8")
    vtt.write_text(_vtt(dur, name), encoding="utf-8")
    if ref is not None:
        np.save(ref_npy, ref)
        truth["reference"] = ref
    return mp4, truth


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    out = Path(argv[0])
    names = argv[1:] or list(SCENARIOS)
    for nm in names:
        p, _ = render(nm, out, force=True)
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
