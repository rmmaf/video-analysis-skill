"""Junta janela e verificador de cada janela de vídeo a partir do journal do workflow."""
import json, sys
from pathlib import Path

SCR = Path(__file__).resolve().parent
JOURNAL = Path(sys.argv[1])
lab, res = {}, {}
for l in JOURNAL.read_text(encoding="utf8").splitlines():
    j = json.loads(l)
    if j["type"] == "started":
        lab[j["key"]] = j["label"]
    elif j["type"] == "result" and j.get("result"):
        res[lab.get(j["key"], j["key"])] = j["result"]


def mescla(a, b):
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            mescla(a[k], v)
        else:
            a[k] = v
    return a


itens, linhas, artefatos, faltando, stats = [], [], [], [], {}
indice = json.loads((SCR / "videos" / "indice_janelas.json").read_text(encoding="utf8"))
for w in indice:
    chave = f"{w['slug']}#{w['janela']}"
    r = res.get(chave); v = res.get("verif:" + chave)
    if not r:
        faltando.append(chave); continue
    ved = {x["idx"]: x for x in (v or {}).get("vereditos", [])}
    for i, it in enumerate(r["itens"]):
        x = ved.get(i, {"veredito": "sem_veredito" if r["itens"] else "-"})
        if x["veredito"] == "descartado":
            continue
        it = dict(it)
        if x["veredito"] == "corrigido":
            mescla(it, x.get("correcoes") or {})
        it.update(lote=chave, origem_item="analista", veredito=x["veredito"], trecho_confere="video")
        itens.append(it)
    for it in (v or {}).get("itens_novos", []):
        it = dict(it); it.update(lote=chave, origem_item="verificador", veredito="novo", trecho_confere="video")
        itens.append(it)
    for t in r["linha_do_tempo"]:
        linhas.append({"video": w["slug"], "janela": w["janela"], **t})
    for a in r.get("artefatos_vistos", []):
        artefatos.append({"video": w["slug"], **a})
    s = stats.setdefault(w["slug"], {"janelas": 0, "frames": 0, "vistos": 0, "itens": 0, "verificadas": 0})
    s["janelas"] += 1; s["frames"] += w["frames"]; s["vistos"] += r.get("frames_vistos", 0)
    s["itens"] += len(r["itens"]); s["verificadas"] += 1 if v else 0
(SCR / "videos" / "itens_videos.json").write_text(json.dumps(itens, ensure_ascii=False, indent=1), encoding="utf8")
(SCR / "videos" / "linha_do_tempo.json").write_text(json.dumps(linhas, ensure_ascii=False, indent=1), encoding="utf8")
(SCR / "videos" / "artefatos_vistos.json").write_text(json.dumps(artefatos, ensure_ascii=False, indent=1), encoding="utf8")
for k, s in stats.items():
    print(k, s)
print("itens:", len(itens), "| janelas sem resultado:", faltando)
