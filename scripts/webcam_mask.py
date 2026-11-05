"""Mascara da webcam com a DeepFace (detector YuNet), para a skill video-analysis.

Procura rostos que se mexem (uma foto nao se mexe) e ficam no mesmo lugar em pelo
menos metade das amostras. Em volta de cada um, acha a janela da camera: na mediana
dos quadros em que ele aparece, o retangulo 16:9 (ou 4:3) com borda nos quatro lados e
fundo liso por fora. Sem janela clara, usa as proporcoes do rosto. Um rosto presente
em 30% a 50% das amostras (camera que some quando o layout muda) so entra com a
janela achada. Imprime as regioes no formato do ignore_region.

A YuNet fica com os rostos de nota 0,85 ou mais; a DeepFace usa 0,9, e a variavel de ambiente
yunet_score_threshold troca o valor. Numa chamada do Teams de 28 min, conferida a olho em 60
quadros, com 0,9 ficaram de fora 16 de 303 rostos, 15 deles com nota entre 0,86 e 0,89, e a
linha do tempo cobria 92% das webcams. Com 0,85 cobriu 98%, nenhuma regiao caiu sobre a tela
compartilhada, e nada que nao era rosto passou de 0,73.

Com --timeline, escreve tambem a linha do tempo: cada janela de camera so durante os
trechos em que o rosto esta nela, para o --ignore-timeline da skill (prepare.py). Assim a
mascara acompanha as trocas de layout (so camera -> tela compartilhada -> galeria). Um
trecho e uma sequencia de amostras com o rosto no mesmo lugar (ate 3 falhas seguidas nao
o cortam), com pelo menos 3 delas e com o rosto se mexendo; ele vai de 1 passo (2 s) antes
da primeira ate 1 passo depois da ultima. Nao precisa da presenca minima do modo fixo.

Instalacao, fora do setup da skill, num ambiente Python separado:
  python -m venv %USERPROFILE%\\deepface-env
  %USERPROFILE%\\deepface-env\\Scripts\\python -m pip install deepface==0.0.104
  %USERPROFILE%\\deepface-env\\Scripts\\python webcam_mask.py VIDEO --timeline SAIDA.json
Os pesos da YuNet sao baixados no primeiro uso (para outra pasta: DEEPFACE_HOME).

Uso: python webcam_mask.py VIDEO [--tiles overlap|grid|frame] [--samples N] [--json SAIDA]
                                 [--timeline SAIDA.json] [--debug PASTA]
  --tiles overlap  pedacos de 640x540 sobrepostos pela metade: 15 num quadro 1080p (padrao)
  --tiles grid     pedacos de 640x540 lado a lado: 6 num quadro 1080p
  --tiles frame    o quadro inteiro numa chamada; a DeepFace o reduz a 640 px de lado,
                   e rostos pequenos deixam de ser achados
  --samples N      so N amostras espalhadas pelo video, com busca (seek), em vez de
                   uma a cada 2 s do comeco ao fim (para a linha do tempo, use o padrao:
                   com amostras espacadas, o inicio e o fim dos trechos ficam imprecisos)
  --timeline SAIDA.json  a linha do tempo, no formato do --ignore-timeline da skill
  --debug PASTA    salva, para cada rosto, a mediana com o rosto (amarelo), a janela
                   achada (verde) e a regiao pelas proporcoes do rosto (vermelho)
VIDEO aceita curingas (* ?), para nomes com acentos decompostos.
Os tempos e os grupos de rostos vao para a saida de erro.
"""
import time

T_START = time.perf_counter()

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# a DeepFace escreve emojis no log, e o console do Windows (cp1252) nao aceita
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from deepface import DeepFace  # noqa: E402

STEP_S = 2.0       # uma amostra a cada 2 s
SCORE = 0.85       # nota minima da YuNet (a DeepFace usa 0,9; ver o topo)
os.environ.setdefault("yunet_score_threshold", str(SCORE))   # a DeepFace le daqui a cada chamada
PRESENCE = 0.5     # rosto no mesmo lugar em pelo menos metade das amostras,
PRESENCE_WINDOW = 0.3  # ou em pelo menos 30% delas, se a janela da camera for achada,
LIVE = 2.0         # e que se mexe (diferenca media acima de 2 tons de cinza): foto nao mexe
TW, TH = 640, 540  # pedacos do quadro: a YuNet da DeepFace reduz o que passa de 640 px

KEEP = 64          # quadros guardados, em cinza e reduzidos, para achar a janela da camera
SMALL_H = 540      # altura desses quadros
EDGE = 16          # diferenca de tons de cinza, a 2 px de distancia, que conta como borda
FLAT = 8           # abaixo disso o pixel e liso, como o fundo em volta das janelas
CAND = 0.3         # fracao de borda, na altura do rosto, para uma coluna ser lado candidato
ACCEPT = 0.6       # nota minima da janela; abaixo dela, ficam as proporcoes do rosto
ASPECTS = (("16:9", 16 / 9), ("4:3", 4 / 3))  # 4:3 so ganha se a nota for 0,1 maior
REACH = (6, 3, 6, 7)   # ate quantos rostos de distancia procurar: esquerda, cima, direita, baixo
MAX_AREA = 0.25    # janela maior que isso (fracao do quadro) nao e janela de camera
MARGIN = 0.006     # folga em volta da janela, fracao da largura: pega a moldura de quem fala
MAX_GAP = 3        # linha do tempo: ate 3 amostras seguidas sem o rosto ainda e o mesmo trecho
MIN_HITS = 3       # trecho com menos amostras com o rosto que isso e passageiro: fica de fora
KEEP_G = 16        # quadros guardados por rosto (a partir da MIN_HITS-esima amostra com ele)


def starts(length, size, step):
    """Inicios dos pedacos num lado do quadro; o ultimo encosta na borda."""
    if length <= size:
        return [0]
    s = list(range(0, length - size + 1, step))
    if s[-1] != length - size:
        s.append(length - size)
    return s


def tiles(frame, mode):
    H, W = frame.shape[:2]
    if mode == "frame":
        return [(0, 0, frame)]
    sx, sy = (TW // 2, TH // 2) if mode == "overlap" else (TW, TH)
    return [(x, y, frame[y:y + TH, x:x + TW])
            for y in starts(H, TH, sy) for x in starts(W, TW, sx)]


def faces(frame, mode):
    for tx, ty, tile in tiles(frame, mode):
        for o in DeepFace.extract_faces(img_path=tile, detector_backend="yunet",
                                        enforce_detection=False, align=False):
            if o["confidence"] > 0:   # sem rosto, ela devolve o pedaco inteiro com confianca 0
                a = o["facial_area"]
                yield (tx + a["x"], ty + a["y"], a["w"], a["h"])


def iou(a, b):
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    union = a[2] * a[3] + b[2] * b[3] - ix * iy
    return ix * iy / union if union > 0 else 0.0


def ratio_box(x, y, w, h):
    """Do rosto ao quadro da webcam pelas proporcoes do video_to_slides (MIT)."""
    return x - 1.3 * w, y - 0.4 * h, x + 2.3 * w, y + 1.8 * h


def find_window(stack, face):
    """Acha a janela da camera em volta do rosto, em pixels dos quadros de stack (k, H, W).

    Na mediana dos quadros em que o rosto aparece, testa os retangulos em volta dele com
    formato de janela de video (16:9; 4:3 so se for bem melhor) cujos lados caem em linhas
    retas ou na borda do quadro. A nota de um lado e a fracao dele que e borda, pesada pelo
    quanto a faixa logo fora dele e lisa, como o fundo em volta das janelas; a nota da
    janela e a media dos quatro lados. Devolve um dict com box (x0, y0, x1, y1) ou None,
    score, aspect e, para o --debug, med e off.
    """
    _, H, W = stack.shape
    fx, fy, fw, fh = face
    X0 = max(0, int(fx - REACH[0] * fw))
    X1 = min(W, int(math.ceil(fx + fw + REACH[2] * fw)))
    Y0 = max(0, int(fy - REACH[1] * fh))
    Y1 = min(H, int(math.ceil(fy + fh + REACH[3] * fh)))
    edges = (X0 == 0, Y0 == 0, X1 == W, Y1 == H)   # o lado do recorte e a borda do quadro
    med = np.median(stack[:, Y0:Y1, X0:X1], axis=0).astype(np.float32)
    out = {"box": None, "score": 0.0, "aspect": "", "med": med, "off": (X0, Y0)}
    h, w = med.shape
    a0, a1 = int(fx) - X0, int(math.ceil(fx + fw)) - X0   # colunas do rosto no recorte
    b0, b1 = int(fy) - Y0, int(math.ceil(fy + fh)) - Y0   # linhas do rosto no recorte
    dx = np.zeros((h, w), np.float32)
    dx[:, 1:-1] = np.abs(med[:, 2:] - med[:, :-2])
    dy = np.zeros((h, w), np.float32)
    dy[1:-1] = np.abs(med[2:] - med[:-2])
    ex, ey = dx >= EDGE, dy >= EDGE
    vx = ex.copy()                 # tolera 1 px de serrilhado na linha
    vx[:, 1:] |= ex[:, :-1]
    vx[:, :-1] |= ex[:, 1:]
    vy = ey.copy()
    vy[1:] |= ey[:-1]
    vy[:-1] |= ey[1:]
    CX = np.vstack([np.zeros((1, w)), np.cumsum(vx, axis=0)])   # somas acumuladas: suporte
    CY = np.hstack([np.zeros((h, 1)), np.cumsum(vy, axis=1)])   # de um lado em O(1)
    F = np.zeros((h + 1, w + 1))
    F[1:, 1:] = np.cumsum(np.cumsum(np.maximum(dx, dy) < FLAT, axis=0), axis=1)

    def flat(y0, y1, x0, x1, frame_outside):
        """Fracao lisa da faixa; vazia, conta como lisa se ali fora ja e fora do quadro."""
        y0, y1, x0, x1 = np.clip(y0, 0, h), np.clip(y1, 0, h), np.clip(x0, 0, w), np.clip(x1, 0, w)
        area = (y1 - y0) * (x1 - x0)
        s = F[y1, x1] - F[y0, x1] - F[y1, x0] + F[y0, x0]
        return np.where(area > 0, s / np.maximum(area, 1), 1.0 if frame_outside else 0.5)

    def candidates(cols, sup):   # colunas com borda na altura do rosto e maximo local em +-2 px
        return [c for c in cols if sup[c] >= CAND and sup[c] == sup[max(0, c - 2):c + 3].max()]

    sup = vx[b0:min(h, b1 + int(0.5 * fh))].mean(axis=0)   # rosto e pescoco
    lefts = candidates(range(a0), sup) + ([0] if edges[0] else [])
    rights = [c + 1 for c in candidates(range(a1, w), sup)] + ([w] if edges[2] else [])
    best = {}
    for L in lefts:
        for R in rights:
            ww = R - L
            if not 0.08 <= fw / ww <= 0.5:
                continue
            for name, ratio in ASPECTS:
                hh = int(round(ww / ratio))
                if not 0.08 <= fh / hh <= 0.7:
                    continue
                Ts = np.arange(max(0, b1 - hh), min(b0, h - hh) + 1)   # o rosto fica dentro
                if len(Ts) == 0:
                    continue
                Bs = Ts + hh
                d, d2 = int(0.06 * hh), int(0.06 * ww)   # sem as pontas: cantos arredondados
                ti, bi, li, ri = Ts + d, Bs - d, L + d2, R - d2
                n = np.maximum(bi - ti, 1)
                one = np.ones(len(Ts))
                sides = [
                    one if L == 0 and edges[0] else (CX[bi, L] - CX[ti, L]) / n,
                    np.where((Ts == 0) & edges[1], 1.0, (CY[Ts, ri] - CY[Ts, li]) / (ri - li)),
                    one if R == w and edges[2] else (CX[bi, R - 1] - CX[ti, R - 1]) / n,
                    np.where((Bs == h) & edges[3], 1.0, (CY[Bs - 1, ri] - CY[Bs - 1, li]) / (ri - li)),
                ]
                outside = [flat(ti, bi, L - 5, L - 2, edges[0] and L == 0),
                           flat(Ts - 5, Ts - 2, li, ri, edges[1]),
                           flat(ti, bi, R + 2, R + 5, edges[2] and R == w),
                           flat(Bs + 2, Bs + 5, li, ri, edges[3])]
                score = sum(s * (0.5 + 0.5 * f) for s, f in zip(sides, outside)) / 4
                k = int(np.argmax(score))
                if name not in best or score[k] > best[name][0]:
                    best[name] = (float(score[k]), (L, int(Ts[k]), R, int(Bs[k])),
                                  sum(float(s[k]) >= 0.5 for s in sides))
    if not best:
        return out
    name = "16:9"
    if name not in best or ("4:3" in best and best["4:3"][0] >= best[name][0] + 0.1):
        name = "4:3"
    score, (L, T, R, B), strong = best[name]
    out["score"], out["aspect"] = score, name
    if score >= ACCEPT and strong >= 3 and (R - L) * (B - T) <= MAX_AREA * W * H:
        out["box"] = (X0 + L, Y0 + T, X0 + R, Y0 + B)
    return out


def live_runs(samples, crops):
    """Trechos de um rosto para a linha do tempo: (i0, i1, movimento) com os indices [i0, i1) das
    amostras com ele. Ate MAX_GAP amostras seguidas sem o rosto nao cortam o trecho; fica o
    trecho com pelo menos MIN_HITS amostras e com o rosto se mexendo (foto nao se mexe)."""
    out, i0 = [], 0
    for j in range(1, len(samples) + 1):
        if j < len(samples) and samples[j] - samples[j - 1] <= MAX_GAP + 1:
            continue
        if j - i0 >= MIN_HITS:
            mv = float(np.mean([np.abs(p - q).mean() for p, q in zip(crops[i0:j], crops[i0 + 1:j])]))
            if mv >= LIVE:
                out.append((i0, j, mv))
        i0 = j
    return out


def merge_spans(spans):
    """Junta as regioes quase iguais (a mesma janela achada a partir de rostos vizinhos, com 1 ou
    2 px de diferenca: IoU >= 0,8), pela uniao delas, e depois os trechos da mesma regiao que se
    tocam; em ordem de inicio."""
    clusters = []   # [uniao (x, y, w, h), regioes dela]
    for t in sorted(spans, key=lambda t: -t["hits"]):
        b = tuple(float(v) for v in t["region"].split(","))
        c = next((c for c in clusters if iou(c[0], b) >= 0.8), None)
        if c is None:
            clusters.append([b, [t["region"]]])
        else:
            x0, y0 = min(c[0][0], b[0]), min(c[0][1], b[1])
            x1, y1 = max(c[0][0] + c[0][2], b[0] + b[2]), max(c[0][1] + c[0][3], b[1] + b[3])
            c[0] = (x0, y0, x1 - x0, y1 - y0)
            c[1].append(t["region"])
    name = {r: "{:.3f},{:.3f},{:.3f},{:.3f}".format(*b) for b, members in clusters for r in members}
    out = []
    for t in sorted(spans, key=lambda t: (name[t["region"]], t["start"])):
        last = out[-1] if out else None
        if last and last["region"] == name[t["region"]] and t["start"] <= last["end"]:
            last["end"] = max(last["end"], t["end"])
            last["hits"] += t["hits"]
        else:
            out.append({**t, "region": name[t["region"]]})
    return sorted(out, key=lambda t: (t["start"], t["region"]))


def hms(t):
    t = int(round(t))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def save_debug(path, win, face, ratio):
    """Mediana do recorte com o rosto (amarelo), a janela (verde) e as proporcoes (vermelho)."""
    img = cv2.cvtColor(win["med"].astype(np.uint8), cv2.COLOR_GRAY2BGR)
    ox, oy = win["off"]

    def rect(b, color):
        cv2.rectangle(img, (int(b[0] - ox), int(b[1] - oy)), (int(b[2] - ox) - 1, int(b[3] - oy) - 1),
                      color, 1)

    rect(ratio, (0, 0, 255))
    rect((face[0], face[1], face[0] + face[2], face[1] + face[3]), (0, 220, 255))
    if win["box"]:
        rect(win["box"], (0, 255, 0))
    cv2.imwrite(path, cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))


def main():
    ap = argparse.ArgumentParser(description="Mascara da webcam com a DeepFace (YuNet).")
    ap.add_argument("video")
    ap.add_argument("--tiles", choices=["overlap", "grid", "frame"], default="overlap")
    ap.add_argument("--samples", type=int, default=0)
    ap.add_argument("--json")
    ap.add_argument("--timeline")
    ap.add_argument("--debug")
    a = ap.parse_args()
    score = float(os.environ["yunet_score_threshold"])

    found = glob.glob(a.video)
    if len(found) != 1:
        sys.exit(f"esperava um video, achei {len(found)}: {a.video}")
    cap = cv2.VideoCapture(found[0], cv2.CAP_FFMPEG)
    if not cap.isOpened():
        sys.exit("o OpenCV nao abriu o video")
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps > 0 else 0.0

    groups = []   # um grupo por rosto que fica no mesmo lugar
    store = []    # (amostra, quadro em cinza reduzido), espalhados pelo video
    st = {"n": 0, "calls": 0, "W": 0, "H": 0, "decode": 0.0, "detect": 0.0, "every": 1, "times": []}

    def add(frame, t):
        H, W = frame.shape[:2]
        st["W"], st["H"] = W, H
        st["calls"] += len(tiles(frame, a.tiles))
        st["times"].append(t)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        n = st["n"]
        small = []

        def reduced():   # o quadro em cinza reduzido, feito uma vez so por amostra
            if not small:
                s = min(1.0, SMALL_H / H)
                small.append(cv2.resize(gray, (round(W * s), round(H * s)), interpolation=cv2.INTER_AREA))
            return small[0]

        if n % st["every"] == 0:
            store.append((n, reduced()))
            if len(store) > KEEP:   # fica com metade, ainda espalhada pelo video
                del store[1::2]
                st["every"] *= 2
        for box in faces(frame, a.tiles):
            g = next((g for g in groups if iou(g["box"], box) >= 0.5), None)
            if g is None:
                g = {"box": box, "hits": set(), "samples": [], "boxes": [], "crops": [], "store": [], "every": 1}
                groups.append(g)
            if n in g["hits"]:
                continue          # o mesmo rosto achado em dois pedacos
            x, y, w, h = g["box"]
            g["hits"].add(n)
            g["samples"].append(n)
            g["boxes"].append(box)
            g["crops"].append(gray[y:y + h, x:x + w].astype(np.int16))
            # quadros com este rosto, para a janela de uma camera que aparece pouco no video
            k = len(g["samples"]) - MIN_HITS
            if k >= 0 and k % g["every"] == 0:
                g["store"].append((n, reduced()))
                if len(g["store"]) > KEEP_G:
                    del g["store"][1::2]
                    g["every"] *= 2
        st["n"] += 1

    t_loop = time.perf_counter()
    if a.samples > 0:
        if duration <= 0:
            sys.exit("o video nao informa a duracao; rode sem --samples")
        for k in range(a.samples):
            t0 = time.perf_counter()
            cap.set(cv2.CAP_PROP_POS_MSEC, (k + 0.5) * duration / a.samples * 1000)
            ok, frame = cap.read()
            st["decode"] += time.perf_counter() - t0
            if ok:
                t0 = time.perf_counter()
                add(frame, (k + 0.5) * duration / a.samples)
                st["detect"] += time.perf_counter() - t0
    else:
        i, next_ms, t_ms = 0, 0.0, 0.0
        while True:
            t0 = time.perf_counter()
            ok, frame = cap.grab(), None
            if ok:
                t_ms = i * 1000 / fps if fps > 0 else cap.get(cv2.CAP_PROP_POS_MSEC)
                if t_ms >= next_ms:
                    ok, frame = cap.retrieve()
                    next_ms += STEP_S * 1000
            st["decode"] += time.perf_counter() - t0
            if not ok:
                break
            if frame is not None:
                t0 = time.perf_counter()
                add(frame, t_ms / 1000)
                st["detect"] += time.perf_counter() - t0
            i += 1
        if duration <= 0:
            duration = t_ms / 1000
    cap.release()
    loop_s = time.perf_counter() - t_loop

    n, W, H = st["n"], st["W"], st["H"]
    if n == 0:
        sys.exit("nenhum quadro lido")
    s = min(1.0, SMALL_H / H)
    m = MARGIN * W
    if a.debug:
        os.makedirs(a.debug, exist_ok=True)
    t_win = time.perf_counter()
    down = lambda v: math.floor(v * 1000) / 1000  # noqa: E731
    up = lambda v: min(1.0, math.ceil(v * 1000) / 1000)  # noqa: E731
    step_s = duration / a.samples if a.samples > 0 else STEP_S
    found_faces = []
    for i, g in enumerate(groups):
        c = g["crops"]
        moves = [float(np.abs(p - q).mean()) for p, q in zip(c, c[1:])]
        live = float(np.mean(moves)) if moves else 0.0
        presence = len(g["hits"]) / n
        x, y, w, h = (float(v) for v in np.median(g["boxes"], axis=0))
        runs = live_runs(g["samples"], c) if a.timeline else []
        box, how, win = ratio_box(x, y, w, h), "proporcoes", None
        if (live >= LIVE and presence >= PRESENCE_WINDOW) or runs or (a.debug and presence >= 0.1):
            # os quadros com o rosto: os guardados do video todo ou, se poucos, os do rosto
            frames = [f for k, f in store if k in g["hits"]]
            if len(frames) < 8:
                frames = [f for _, f in g["store"]] or frames or [f for _, f in store]
            win = find_window(np.stack(frames), (x * s, y * s, w * s, h * s))
            if win["box"]:
                x0, y0, x1, y1 = (v / s for v in win["box"])
                box, how = (x0 - m, y0 - m, x1 + m, y1 + m), f"janela {win['aspect']}, nota {win['score']:.2f}"
            else:
                how += f", janela sem nota (melhor {win['score']:.2f})"
        # metade das amostras basta; de 30% a 50%, so com a janela da camera achada
        keep = live >= LIVE and (presence >= PRESENCE or (
            presence >= PRESENCE_WINDOW and win is not None and win["box"] is not None))
        if a.debug and win is not None:
            save_debug(os.path.join(a.debug, f"rosto_{i:02d}_{presence:.0%}_"
                                             f"{'mascara' if keep else 'descartado'}{'_linha' if runs else ''}.png"),
                       win, (x * s, y * s, w * s, h * s),
                       tuple(v * s for v in ratio_box(x, y, w, h)))
        found_faces.append({"i": i, "g": g, "face": (x, y, w, h), "presence": presence, "live": live,
                            "runs": runs, "keep": keep, "box": box, "how": how,
                            "found": win is not None and win["box"] is not None})
    # um rosto sem janela achada (a pessoa se mexeu e virou outro grupo, ou a mediana misturou
    # layouts) que cabe inteiro na janela achada de outro rosto esta na mesma camera: usa essa
    # janela, a do rosto mais parecido com ele
    windows = [f for f in found_faces if f["found"]]
    for f in found_faces:
        if f["found"] or not (f["keep"] or f["runs"]):
            continue
        fx, fy, fw, fh = f["face"]
        tx, ty = 0.1 * fw, 0.1 * fh   # o rosto pode passar um pouco da borda (quem senta baixo)
        inside = [o for o in windows if o["box"][0] - tx <= fx and o["box"][1] - ty <= fy
                  and fx + fw <= o["box"][2] + tx and fy + fh <= o["box"][3] + ty]
        if inside:
            o = max(inside, key=lambda o: iou(o["face"], f["face"]))
            f["box"], f["how"] = o["box"], f"janela do rosto {o['i']} ({o['how']})"
    regions, report, spans = [], [], []
    for f in found_faces:
        (x, y, w, h), box, g = f["face"], f["box"], f["g"]
        x0, y0 = down(max(0.0, box[0]) / W), down(max(0.0, box[1]) / H)
        x1, y1 = up(min(W, box[2]) / W), up(min(H, box[3]) / H)
        region = f"{x0:.3f},{y0:.3f},{x1 - x0:.3f},{y1 - y0:.3f}"
        report.append({"face": [round(x / W, 3), round(y / H, 3), round(w / W, 3), round(h / H, 3)],
                       "presence": round(f["presence"], 3), "motion": round(f["live"], 2), "kept": f["keep"],
                       "region": region, "how": f["how"], "spans": len(f["runs"])})
        if f["keep"] and region not in regions:   # dois rostos na mesma janela dao a mesma regiao
            regions.append(region)
        for i0, i1, mv in f["runs"]:   # de 1 passo antes da primeira amostra com o rosto a 1 depois da ultima
            t0 = max(0.0, st["times"][g["samples"][i0]] - step_s)
            t1 = st["times"][g["samples"][i1 - 1]] + step_s
            spans.append({"start": round(t0, 3), "end": round(min(t1, duration) if duration > 0 else t1, 3),
                          "region": region, "hits": i1 - i0, "motion": round(mv, 2), "how": f["how"]})
    timeline = merge_spans(spans)
    win_s = time.perf_counter() - t_win
    total_s = time.perf_counter() - T_START

    print(("ignore_region: " + "; ".join(regions)) if regions else "nenhuma webcam achada")
    per_h = total_s / duration * 60 if duration else 0.0
    print(f"amostras {n} | chamadas da YuNet {st['calls']} (nota minima {score})"
          f" | decodificacao {st['decode']:.1f} s | deteccao {st['detect']:.1f} s | janelas {win_s:.1f} s | total {total_s:.1f} s"
          f" | video {duration / 60:.1f} min | {per_h:.1f} min por hora de video", file=sys.stderr)
    for r in sorted(report, key=lambda r: -r["presence"]):
        if r["presence"] >= 0.1 or r["spans"]:
            print(f"  rosto {r['face']} presenca {r['presence']:.0%} movimento {r['motion']}"
                  f" {'MASCARA' if r['kept'] else 'descartado'} {r['region']} ({r['how']})"
                  + (f", {r['spans']} trecho(s) na linha do tempo" if r["spans"] else ""), file=sys.stderr)
    if a.timeline:
        doc = {"video": os.path.abspath(found[0]), "made_by": "webcam_mask.py", "step_s": step_s,
               "tiles": a.tiles, "min_score": score, "duration_s": round(duration, 3),
               "ignore_timeline": [{"start": t["start"], "end": t["end"], "region": t["region"]} for t in timeline],
               "spans_info": timeline}
        with open(a.timeline, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
        print(f"linha do tempo: {len(timeline)} trecho(s) em {a.timeline}", file=sys.stderr)
        for t in timeline:
            print(f"  {hms(t['start'])}-{hms(t['end'])} {t['region']} ({t['hits']} amostras com o rosto,"
                  f" {t['how']})", file=sys.stderr)
    if a.json:
        out = {"video": found[0], "tiles": a.tiles, "min_score": score, "samples_arg": a.samples, "fps": fps,
               "duration_s": duration, "size": [W, H], "samples": n, "calls": st["calls"],
               "decode_s": st["decode"], "detect_s": st["detect"], "window_s": win_s,
               "loop_s": loop_s, "total_s": total_s, "min_per_hour": per_h,
               "regions": regions, "groups": report, "timeline": timeline}
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
