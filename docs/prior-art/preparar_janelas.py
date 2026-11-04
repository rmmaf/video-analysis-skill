"""Merges the short windows at the end and writes one window per file, for the video workflow."""
import json
from pathlib import Path

SCR = Path(__file__).resolve().parent
V = SCR / "videos"
indice = []
for resumo in sorted(V.glob("*/resumo.json")):
    base = resumo.parent; slug = base.name
    jan = json.loads((base / "janelas.json").read_text(encoding="utf8"))
    while len(jan) > 1 and len(jan[-1]["frames"]) < 12:  # the leftover window goes into the previous one
        ult = jan.pop(); jan[-1]["frames"] += ult["frames"]; jan[-1]["fim"] = ult["fim"]
        vistos = set(jan[-1]["transcricao"])
        jan[-1]["transcricao"] += [t for t in ult["transcricao"] if t not in vistos]
    for k, j in enumerate(jan, 1):
        j["janela"] = k
        j["frames"] = [str(base / "frames" / f).replace("\\", "/") for f in j["frames"]]
        (base / f"janela_{k:02d}.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf8")
        indice.append({"slug": slug, "janela": k, "inicio": j["inicio"], "fim": j["fim"], "frames": len(j["frames"])})
(V / "indice_janelas.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf8")
por = {}
for i in indice:
    por.setdefault(i["slug"], [0, 0]); por[i["slug"]][0] += 1; por[i["slug"]][1] += i["frames"]
print(json.dumps(por, ensure_ascii=False), "total windows:", len(indice), "frames:", sum(i["frames"] for i in indice))
