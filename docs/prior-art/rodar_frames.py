"""Roda frames.py nos 7 vídeos, achando os caminhos por glob (o nome tem ’ e acentos)."""
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import frames

RAIZ = Path(r"C:/Users/digom/Documents/solar/databricks  schema/docs-solar")
VIDEOS = {
    "da_1508": "01.Arquitetura/3.DA/*150822*.mp4",
    "da_1608": "01.Arquitetura/3.DA/*160841*.mp4",
    "adf_overview": "02. Conteúdos/01.Interno/Azure data factory/Azure Data Factory - Overview.mp4",
    "migracao_0_1_p1": "02. Conteúdos/01.Interno/Datalake/*Eng. 1_2*.mp4",
    "migracao_0_1_p2": "02. Conteúdos/01.Interno/Datalake/*Eng. 2_2*.mp4",
    "arq1_sharepoint": "02. Conteúdos/01.Interno/Datalake/*ARQ1 Sharepoint*.mp4",
    "arquitetura_1812": "02. Conteúdos/01.Interno/Governança de Dados/Gravações/Estrutura Fabric e Arquitetura de Dados/Arquitetura de Dados Solar-*.mp4",
}


def um(slug):
    v = sorted(RAIZ.glob(VIDEOS[slug]))
    assert len(v) == 1, (slug, v)
    vtt = v[0].with_suffix(".vtt")
    assert vtt.exists(), vtt
    frames.main(slug, str(v[0]), str(vtt))
    return slug


if __name__ == "__main__":
    alvos = sys.argv[1:] or list(VIDEOS)
    with ProcessPoolExecutor(max_workers=3) as ex:
        for s in ex.map(um, alvos):
            print("feito", s, flush=True)
