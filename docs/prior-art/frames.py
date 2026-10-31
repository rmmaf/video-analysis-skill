"""Extrai os frames distintos de um vídeo a 2 fps e alinha cada um à transcrição .vtt.

Uso: python frames.py <slug> <video> <vtt>
Grava em scratchpad/videos/<slug>/: frames/*.jpg (resolução cheia), alinhamento.jsonl, janelas.json, resumo.json
"""
import json, re, subprocess, sys, time
from pathlib import Path
import numpy as np

SCR = Path(__file__).resolve().parent
FPS = 2
W, H = 480, 270            # escala de decisão; os frames salvos ficam em 1920x1080
LIMIAR_PIXEL = 18          # diferença de cinza que conta como mudança
FRACAO_MUDANCA = float(__import__("os").environ.get("FRACAO", "0.008"))  # fração dos pixels fora da máscara que precisa mudar
FREQ_QUENTE = 0.15         # pixel que muda em >15% dos pares de frames (webcam, animação) é ignorado
JANELA_FRAMES, JANELA_SEG, SOBREPOSICAO = 50, 600, 30


def ts(seg):
    h, r = divmod(int(seg), 3600); m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def ler_vtt(p):
    txt = Path(p).read_text(encoding="utf8", errors="ignore")
    cues = []
    for m in re.finditer(r"(\d+):(\d\d):(\d\d)[.,](\d+) --> (\d+):(\d\d):(\d\d)[.,](\d+)[^\n]*\n(.*?)(?:\n\n|\Z)", txt, re.S):
        g = m.groups()
        ini = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
        fim = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000
        corpo = " ".join(l.strip() for l in g[8].strip().splitlines())
        sp = re.match(r"(SPEAKER_\d+|[^:]{2,60}):\s*(.*)", corpo)
        cues.append({"ini": round(ini, 2), "fim": round(fim, 2),
                     "falante": sp.group(1) if sp else "?", "texto": sp.group(2) if sp else corpo})
    return cues


def main(slug, video, vtt):
    base = SCR / "videos" / slug; (base / "frames").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "default=nw=1:nk=1", video]).decode().strip())
    n_max = int(dur * FPS) + 8
    low = np.lib.format.open_memmap(base / "low.npy", mode="w+", dtype=np.uint8, shape=(n_max, H, W))
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video, "-vf",
                             f"fps={FPS},scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            stdout=subprocess.PIPE)
    n, tam = 0, W * H
    while True:
        b = proc.stdout.read(tam)
        if len(b) < tam:
            break
        low[n] = np.frombuffer(b, np.uint8).reshape(H, W); n += 1
    proc.wait()
    low = low[:n]
    # máscara do que muda o tempo todo
    freq = np.zeros((H, W), np.float32)
    for i in range(1, n, 256):
        a = low[i - 1:min(i + 256, n)].astype(np.int16)
        freq += (np.abs(np.diff(a, axis=0)) > LIMIAR_PIXEL).sum(axis=0)
    freq /= max(n - 1, 1)
    quente = freq > FREQ_QUENTE
    for _ in range(3):  # dilata a máscara
        quente = quente | np.roll(quente, 1, 0) | np.roll(quente, -1, 0) | np.roll(quente, 1, 1) | np.roll(quente, -1, 1)
    frio = ~quente; n_frio = max(int(frio.sum()), 1)
    manter, ultimo = [0], low[0].astype(np.int16)
    for i in range(1, n):
        atual = low[i].astype(np.int16)
        if ((np.abs(atual - ultimo) > LIMIAR_PIXEL) & frio).sum() / n_frio > FRACAO_MUDANCA:
            manter.append(i); ultimo = atual
    # segunda decodificação, em resolução cheia e JPEG; guarda só os frames mantidos.
    # (select= com milhares de termos estoura o parser de expressão do ffmpeg)
    for f in (base / "frames").glob("*.jpg"):
        f.unlink()
    nomes = {i: f"f{idx:04d}_{ts(i / FPS).replace(':', '')}.jpg" for idx, i in enumerate(manter)}
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video, "-vf", f"fps={FPS}",
                             "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "-"], stdout=subprocess.PIPE)
    buf, k, gravados = b"", 0, 0
    while True:
        bloco = proc.stdout.read(1 << 22)
        if not bloco:
            break
        buf += bloco
        while True:
            fim = buf.find(b"\xff\xd9\xff\xd8")
            if fim < 0:
                break
            if k in nomes:
                (base / "frames" / nomes[k]).write_bytes(buf[:fim + 2]); gravados += 1
            buf = buf[fim + 2:]; k += 1
    if buf.startswith(b"\xff\xd8") and k in nomes:
        (base / "frames" / nomes[k]).write_bytes(buf); gravados += 1
    proc.wait()
    if gravados != len(manter):
        print(f"AVISO {slug}: {gravados} jpg para {len(manter)} frames mantidos ({k + 1} decodificados)", flush=True)
    frames = [{"frame": nomes[i], "t": i / FPS} for i in manter if (base / "frames" / nomes[i]).exists()]
    for k, fr in enumerate(frames):
        fr["t_fim"] = frames[k + 1]["t"] if k + 1 < len(frames) else dur
    # alinhamento
    cues = ler_vtt(vtt); j = 0
    for c in cues:
        while j + 1 < len(frames) and frames[j + 1]["t"] <= c["ini"]:
            j += 1
        c["frame_na_tela"] = frames[j]["frame"]
    with open(base / "alinhamento.jsonl", "w", encoding="utf8") as fh:
        for fr in frames:
            falas = [c for c in cues if c["fim"] >= fr["t"] - 3 and c["ini"] <= fr["t_fim"] + 3]
            fh.write(json.dumps({**fr, "inicio": ts(fr["t"]), "fim": ts(fr["t_fim"]),
                                 "falas": [f"[{ts(c['ini'])}] {c['falante']}: {c['texto']}" for c in falas]},
                                ensure_ascii=False) + "\n")
    # janelas para os agentes
    janelas, atual = [], []
    for fr in frames:
        if atual and (len(atual) >= JANELA_FRAMES or fr["t"] - atual[0]["t"] >= JANELA_SEG):
            janelas.append(atual); atual = []
        atual.append(fr)
    if atual:
        janelas.append(atual)
    saida = []
    for k, jan in enumerate(janelas):
        ini = jan[0]["t"]; fim = janelas[k + 1][0]["t"] if k + 1 < len(janelas) else dur
        cj = [c for c in cues if c["fim"] >= ini - SOBREPOSICAO and c["ini"] <= fim + SOBREPOSICAO]
        saida.append({"janela": k + 1, "inicio": ts(ini), "fim": ts(fim), "frames": [f["frame"] for f in jan],
                      "transcricao": [f"[{ts(c['ini'])}] {c['falante']}: {c['texto']}  (tela: {c['frame_na_tela']})" for c in cj]})
    (base / "janelas.json").write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf8")
    resumo = {"slug": slug, "video": video, "vtt": vtt, "duracao_s": dur, "frames_2fps": n,
              "frames_distintos": len(frames), "janelas": len(saida), "falas": len(cues),
              "pixels_mascarados_pct": round(100 * float(quente.mean()), 2), "segundos": round(time.time() - t0)}
    (base / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf8")
    del low
    try:
        (base / "low.npy").unlink()
    except OSError:
        pass
    print(json.dumps(resumo, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
