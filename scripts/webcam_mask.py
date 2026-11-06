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
linha do tempo cobria 92% das webcams. Com 0,85 cobriu 98%, e nenhuma regiao caiu sobre a tela
compartilhada. Em mais 4 gravacoes (2 h 18 min, 1080p e 720p), as 94 regioes de 0,85 vieram todas
de rostos de verdade: webcams, fotos de perfil e a camera de uma sala. A faixa de 0,85 a 0,9 tambem
pega o que nao e rosto (selos de porcentagem num slide, ate 0,89), mas numa amostra so: o que segura
isso e a regra do trecho (3 amostras no mesmo lugar, com movimento), nao a nota. A variavel aceita
de 0,75 ate menos de 1, escrita com ponto (0.9): abaixo disso entram mais falsos rostos, e com 1 a
YuNet nao acha rosto nenhum. Com outro valor, o script sai com erro antes de carregar a DeepFace.

Com --timeline, escreve tambem a linha do tempo: cada janela de camera so durante os
trechos em que o rosto esta nela, para o --ignore-timeline da skill (prepare.py). Assim a
mascara acompanha as trocas de layout (so camera -> tela compartilhada -> galeria). Um
trecho e uma sequencia de amostras com o rosto no mesmo lugar (ate 3 falhas seguidas nao
o cortam), com pelo menos 3 delas e com o rosto se mexendo; ele vai de 1 passo (2 s) antes
da primeira ate 1 passo depois da ultima. Nao precisa da presenca minima do modo fixo. O tempo
de cada amostra e o carimbo de tempo do quadro (pts), que acerta tambem em video de taxa
variavel (VFR); a conta quadro / taxa media poe o trecho em outro lugar do video.

Com --probe N, antes da varredura faz uma sondagem: o video em N partes iguais e uma amostra
de cada uma. Se nenhum rosto aparece no mesmo lugar em pelo menos 2 amostras, nao ha webcam:
a linha do tempo sai vazia, com "scan": "probe", e a varredura de 2 em 2 s nem comeca. Com
rosto, segue a varredura, e a linha do tempo sai com "scan": "full". As amostras vem de uma
busca (seek) no meio de cada parte, conferida pelo carimbo do quadro lido: o OpenCV busca pela
contagem de quadros, que em video VFR cai em outra parte ou passa do fim. As partes que ficam
sem amostra saem de uma passada do comeco ao fim, sem busca. Se nem assim as amostras cobrem
o video (ele nao abre, ou a passada para antes do fim), a sondagem nao vale: segue a varredura.
Com N menor que 2 (o prepare.py pede 1 num video de ate 10 s), a sondagem nem roda: uma amostra
nao mostra o rosto em 2, e a varredura de um video curto custa pouco.

A skill roda este script sozinha: o prepare.py o chama com o Python do ambiente da DeepFace
(%USERPROFILE%\\deepface-env, ou a variavel VA_DEEPFACE_ENV), com --probe e --watch-stdin, e guarda
a linha do tempo no cache do video. O ambiente e criado pelo setup da skill (/video-analysis setup, que
roda check_environment.py --install-deepface): um ambiente Python separado, com
deepface==0.0.104 e os pesos da YuNet (para outra pasta: DEEPFACE_HOME). A mao:
  %USERPROFILE%\\deepface-env\\Scripts\\python webcam_mask.py VIDEO --timeline SAIDA.json

Uso: python webcam_mask.py VIDEO [--tiles overlap|grid|frame] [--samples N] [--probe N]
                                 [--json SAIDA] [--timeline SAIDA.json] [--debug PASTA]
                                 [--watch-stdin]
  --tiles overlap  pedacos de 640x540 sobrepostos pela metade: 15 num quadro 1080p (padrao)
  --tiles grid     pedacos de 640x540 lado a lado: 6 num quadro 1080p
  --tiles frame    o quadro inteiro numa chamada; a DeepFace o reduz a 640 px de lado,
                   e rostos pequenos deixam de ser achados
  --samples N      so N amostras espalhadas pelo video, com busca (seek), em vez de
                   uma a cada 2 s do comeco ao fim (para a linha do tempo, use o padrao:
                   com amostras espacadas, o inicio e o fim dos trechos ficam imprecisos)
  --probe N        sondagem de N amostras antes; sem rosto, para sem a varredura (acima)
  --timeline SAIDA.json  a linha do tempo, no formato do --ignore-timeline da skill
  --debug PASTA    salva, para cada rosto, a mediana com o rosto (amarelo), a janela
                   achada (verde) e a regiao pelas proporcoes do rosto (vermelho)
  --watch-stdin    sai (codigo 3) quando a entrada padrao fecha: o prepare.py o passa, com um
                   cano aberto nela, para o script nao seguir sozinho se o prepare.py morrer
VIDEO e o caminho do arquivo; se nao houver arquivo com esse nome, vale como curinga (* ?),
para nomes com acentos decompostos.
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
import threading  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# a DeepFace escreve emojis no log, e o console do Windows (cp1252) nao aceita
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

STEP_S = 2.0       # uma amostra a cada 2 s
SCORE = 0.85       # nota minima da YuNet (a DeepFace usa 0,9; ver o topo)
SCORE_MIN = 0.75   # menor nota aceita em yunet_score_threshold (ver o topo e min_score)
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
PROBE_HITS = 2     # sondagem: um rosto no mesmo lugar em 2 amostras ja pede a varredura completa


def min_score(raw):
    """A nota minima da YuNet: a da variavel yunet_score_threshold (raw) ou, sem ela, SCORE. Sai com
    erro se raw nao for um numero de SCORE_MIN ate menos de 1: com nota baixa a YuNet ve rosto no que
    nao e, e cada falso rosto que se mexe vira regiao ignorada, que pode esconder a tela compartilhada;
    com 1, nao acha rosto nenhum (a nota dela vai ate 1)."""
    if raw is None:
        return SCORE
    try:
        v = float(raw)
    except ValueError:
        v = math.nan
    if not SCORE_MIN <= v < 1:   # NaN e infinitos tambem caem aqui
        sys.exit(f"yunet_score_threshold={raw!r}: use um numero com ponto, de {SCORE_MIN} ate menos de 1"
                 f" (sem a variavel, {SCORE})")
    return v


def watch_stdin():
    """--watch-stdin: sai (codigo 3) quando a entrada padrao chega ao fim. O prepare.py deixa um cano
    aberto nela enquanto espera o script. Se ele morre de vez (pelo Gerenciador de Tarefas, por exemplo),
    nao leva o processo filho junto, mas o cano fecha, e o script sai em vez de seguir sozinho numa
    varredura que ninguem vai ler. Comeca antes de carregar a DeepFace, a parte mais lenta da partida.
    Le o descritor 0 com os.read, e nao o sys.stdin: parada dentro do leitor do sys.stdin, a thread
    seguraria a trava dele, e o Python, ao fecha-lo no fim, pararia com erro fatal, com a linha do tempo
    ja escrita mas o codigo de saida diferente de 0, e o prepare.py a descartaria."""
    def wait():
        try:
            while os.read(0, 4096):
                pass
        except OSError:
            pass
        print("a entrada padrao fechou (o prepare.py parou): saindo", file=sys.stderr, flush=True)
        os._exit(3)

    threading.Thread(target=wait, name="watch-stdin", daemon=True).start()


if "--watch-stdin" in sys.argv[1:] and sys.stdin is not None:
    watch_stdin()

# a DeepFace le a nota desta variavel a cada chamada; com nota errada, o script sai antes de carrega-la
os.environ["yunet_score_threshold"] = str(min_score(os.environ.get("yunet_score_threshold")))
from deepface import DeepFace  # noqa: E402


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


def best_place(sample_faces):
    """Sondagem: (maior numero de amostras com um rosto no mesmo lugar, lugares com rosto). Os rostos
    (x, y, w, h) de cada amostra vao para o grupo do primeiro rosto com IoU >= 0,5, como na varredura."""
    groups = []   # [rosto, amostras com ele]
    for k, boxes in enumerate(sample_faces):
        for box in boxes:
            g = next((g for g in groups if iou(g[0], box) >= 0.5), None)
            if g is None:
                groups.append([box, {k}])
            else:
                g[1].add(k)
    return max((len(g[1]) for g in groups), default=0), len(groups)


def frame_s(cap, i, fps):
    """Tempo, em segundos, do quadro que o cap acabou de ler (o i-esimo dele): o carimbo de tempo do
    quadro (pts), que acerta tambem em video de taxa variavel (VFR); sem carimbo (o OpenCV da 0 depois
    do primeiro quadro), a conta pela taxa media, i / fps."""
    t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
    return t if t > 0 or i == 0 or fps <= 0 else i / fps


def probe(path, duration, n, mode, fps):
    """Sondagem: o video em n partes iguais e uma amostra de cada parte. Devolve (achou, info): achou
    quando um rosto aparece no mesmo lugar em pelo menos PROBE_HITS amostras; info["complete"] diz se
    as amostras cobrem o video. Sem cobertura a sondagem nao vale, e quem chama faz a varredura.

    Primeiro, uma busca (seek) no meio de cada parte, conferida pelo carimbo do quadro lido: o OpenCV
    busca pela contagem de quadros, que em video de taxa variavel (VFR) nao bate com o tempo, e a
    leitura cai em outra parte ou passa do fim. O quadro lido fica com a parte em que caiu. As partes
    que ficam sem amostra saem de uma passada do comeco ao fim, sem busca: o primeiro quadro de cada
    uma. Uma parte sem quadro nenhum (a tela parada num video VFR) nao tem o que mostrar alem do quadro
    de antes e conta como coberta, inclusive no fim, se ele ocupa no maximo as 2 ultimas partes. Nao
    cobre quando o video nao abre, quando um quadro achado nao se le, ou quando a passada para antes."""
    t0 = time.perf_counter()
    cell = duration / n
    taken = {}           # parte -> rostos do quadro dela

    def part(t):
        return min(n - 1, max(0, int(t / cell)))

    cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
    try:
        for k in range(n if cap.isOpened() else 0):
            cap.set(cv2.CAP_PROP_POS_MSEC, (k + 0.5) * cell * 1000)
            ok, frame = cap.read()
            if ok:
                j = part(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000)
                if j not in taken:
                    taken[j] = list(faces(frame, mode))
    finally:
        cap.release()
    walked, framed, last = len(taken) < n, set(), None   # framed: partes com algum quadro na passada
    if walked:
        cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
        try:
            i = 0
            while cap.isOpened() and len(taken) < n and cap.grab():
                last = frame_s(cap, i, fps)
                j = part(last)
                framed.add(j)
                if j not in taken:
                    ok, frame = cap.retrieve()
                    if ok:
                        taken[j] = list(faces(frame, mode))
                i += 1
        finally:
            cap.release()
    reached = part(last) if last is not None else -1
    tail = last is not None and last >= duration - 2 * cell
    covered = sum(1 for j in range(n) if j in taken or (j < reached and j not in framed) or (j > reached and tail))
    best, places = best_place([taken[j] for j in sorted(taken)])
    return best >= PROBE_HITS, {"targets": n, "samples": len(taken), "covered": covered,
                                "complete": covered == n and bool(taken), "walked": walked, "places": places,
                                "best_hits": best, "seconds": round(time.perf_counter() - t0, 1)}


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
    ap.add_argument("--probe", type=int, default=0)
    ap.add_argument("--json")
    ap.add_argument("--timeline")
    ap.add_argument("--debug")
    ap.add_argument("--watch-stdin", action="store_true")   # o watch_stdin() ja comecou, la em cima
    a = ap.parse_args()
    score = float(os.environ["yunet_score_threshold"])

    # o caminho como esta, antes do curinga: "[1]" num nome de arquivo e literal, nao uma classe do glob
    found = [a.video] if os.path.isfile(a.video) else glob.glob(a.video)
    if len(found) != 1:
        sys.exit(f"esperava um video, achei {len(found)}: {a.video}")
    cap = cv2.VideoCapture(found[0], cv2.CAP_FFMPEG)
    if not cap.isOpened():
        sys.exit("o OpenCV nao abriu o video")
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps > 0 else 0.0

    def write_timeline(scan, probe_info, step_s, timeline):
        doc = {"video": os.path.abspath(found[0]), "made_by": "webcam_mask.py", "scan": scan, "probe": probe_info,
               "step_s": step_s, "tiles": a.tiles, "min_score": score, "duration_s": round(duration, 3),
               "ignore_timeline": [{"start": t["start"], "end": t["end"], "region": t["region"]} for t in timeline],
               "spans_info": timeline}
        with open(a.timeline, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
        print(f"linha do tempo: {len(timeline)} trecho(s) em {a.timeline}", file=sys.stderr)

    probe_info = None
    if a.probe > 0 and duration <= 0:
        print("sondagem: o video nao informa a duracao; segue a varredura completa", file=sys.stderr)
    elif 0 < a.probe < PROBE_HITS:
        print(f"sondagem: {a.probe} amostra(s) nao mostram um rosto em {PROBE_HITS}, entao nao dizem que nao ha"
              " webcam; segue a varredura completa (video curto)", file=sys.stderr)
    elif a.probe > 0:
        cap.release()
        seen, probe_info = probe(found[0], duration, a.probe, a.tiles, fps)
        walk = ", com passada do comeco ao fim (a busca nao acertou o tempo)" if probe_info["walked"] else ""
        print(f"sondagem: {probe_info['samples']} de {probe_info['targets']} amostras{walk}, em"
              f" {probe_info['seconds']:.1f} s (nota minima {score}); {probe_info['places']} lugar(es) com rosto,"
              f" o mais visto em {probe_info['best_hits']} amostra(s)", file=sys.stderr)
        if not probe_info["complete"]:
            print(f"sondagem incompleta: {probe_info['covered']} de {probe_info['targets']} partes do video cobertas;"
                  " segue a varredura completa", file=sys.stderr)
        elif not seen:
            print("nenhuma webcam achada")
            print(f"sem rosto no mesmo lugar em {PROBE_HITS} amostras: sem a varredura completa"
                  f" | total {time.perf_counter() - T_START:.1f} s | video {duration / 60:.1f} min", file=sys.stderr)
            if a.timeline:
                write_timeline("probe", probe_info, None, [])
            if a.json:
                with open(a.json, "w", encoding="utf-8") as fh:
                    json.dump({"video": found[0], "tiles": a.tiles, "min_score": score, "duration_s": duration,
                               "probe": probe_info, "regions": [], "groups": [], "timeline": []}, fh, indent=1)
            return
        cap = cv2.VideoCapture(found[0], cv2.CAP_FFMPEG)   # de novo do comeco, para a varredura
        if not cap.isOpened():
            sys.exit("o OpenCV nao abriu o video")

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
        step_ms = STEP_S * 1000
        i, next_ms, t_ms = 0, 0.0, 0.0
        while True:
            t0 = time.perf_counter()
            ok, frame = cap.grab(), None
            if ok:
                t_ms = frame_s(cap, i, fps) * 1000
                if t_ms >= next_ms:
                    ok, frame = cap.retrieve()
                    # a proxima marca de 2 s depois deste quadro: num video VFR, a tela parada nao tem
                    # quadros, e as marcas que ficaram para tras nao viram uma rajada de amostras
                    next_ms = (math.floor(t_ms / step_ms) + 1) * step_ms
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
        write_timeline("full", probe_info, step_s, timeline)
        for t in timeline:
            print(f"  {hms(t['start'])}-{hms(t['end'])} {t['region']} ({t['hits']} amostras com o rosto,"
                  f" {t['how']})", file=sys.stderr)
    if a.json:
        out = {"video": found[0], "tiles": a.tiles, "min_score": score, "samples_arg": a.samples, "fps": fps,
               "duration_s": duration, "probe": probe_info, "size": [W, H], "samples": n, "calls": st["calls"],
               "decode_s": st["decode"], "detect_s": st["detect"], "window_s": win_s,
               "loop_s": loop_s, "total_s": total_s, "min_per_hour": per_h,
               "regions": regions, "groups": report, "timeline": timeline}
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
