"""
Genera las graficas comparativas (R-Tree vs busqueda secuencial vs
PostGIS/GiST) para README.md a partir de los JSON que dejan
rtree_benchmark.py y postgis_benchmark.py

Uso:
    PYTHONPATH=. python benchmark/spatial/rtree_benchmark.py 1000 10000 100000
    PGPASSWORD=... python benchmark/spatial/postgis_benchmark.py 1000 10000 100000
    python benchmark/spatial/plot_rtree_benchmark.py
"""

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(OUT_DIR, "resultados.json"), encoding="utf-8") as f:
    r_py = json.load(f)
with open(os.path.join(OUT_DIR, "resultados_postgis.json"), encoding="utf-8") as f:
    r_pg = json.load(f)

ESCALAMIENTO = r_py["escalamiento"]
RANGE_QUERIES = r_py["range_queries"]
KNN_QUERIES = r_py["knn_queries"]
ESCALAMIENTO_PG = r_pg["escalamiento"]
RANGE_QUERIES_PG = r_pg["range_queries"]
KNN_QUERIES_PG = r_pg["knn_queries"]

SIZES = sorted(r["n"] for r in ESCALAMIENTO)
N_FOCO = max(SIZES)  # dataset mas grande, el mas representativo para sensibilidad a radio/k
RADIO_FOCO = sorted(set(r["radio_km"] for r in RANGE_QUERIES))[1]  # radio intermedio (5km)
K_FOCO = sorted(set(r["k"] for r in KNN_QUERIES))[1]  # k intermedio (50)

# paleta validada (dataviz skill, scripts/validate_palette.js) -- mismos
# azul/naranja que benchmark/indices/plot_index_benchmark.py para que la
# identidad de "estructura propia vs. scan simple" no cambie de chart a
# chart en todo el repo; verde para PostGIS (tercer miembro de esa misma
# paleta categorica de 3, ya validada ahi).
COLOR_RTREE = "#2a78d6"
COLOR_SECUENCIAL = "#eda100"
COLOR_POSTGIS = "#1baf7a"
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"


def estilo_base(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(MUTED)
    ax.tick_params(colors=MUTED)
    ax.xaxis.label.set_color(INK)
    ax.yaxis.label.set_color(INK)
    ax.title.set_color(INK)


def linea(ax, xs, ys, color, label):
    ax.plot(xs, ys, marker="o", markersize=6, linewidth=2, color=color, label=label)


os.makedirs(OUT_DIR, exist_ok=True)
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["figure.facecolor"] = SURFACE

# 1. construccion del indice -- R-Tree vs PostGIS (la secuencial no
# mantiene ninguna estructura propia, no hay nada que "construir")
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
linea(ax, [r["n"] for r in ESCALAMIENTO], [r["ms_construccion"] for r in ESCALAMIENTO], COLOR_RTREE, "R-Tree")
linea(ax, [r["n"] for r in ESCALAMIENTO_PG], [r["ms_construccion"] for r in ESCALAMIENTO_PG], COLOR_POSTGIS, "PostGIS/GiST")
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel("N (puntos)")
ax.set_ylabel("tiempo de construccion (ms, log)")
ax.set_title("Construccion del indice")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "construccion_ms.png"))
plt.close(fig)

# 2. espacio en disco del indice -- R-Tree vs PostGIS (barras agrupadas)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
xs = range(len(SIZES))
ancho = 0.35
ys_rtree = [r["disco_kb"] for r in sorted(ESCALAMIENTO, key=lambda r: r["n"])]
ys_pg = [r["indice_kb"] for r in sorted(ESCALAMIENTO_PG, key=lambda r: r["n"])]
ax.bar([x - ancho / 2 for x in xs], ys_rtree, width=ancho * 0.92, color=COLOR_RTREE, label="R-Tree")
ax.bar([x + ancho / 2 for x in xs], ys_pg, width=ancho * 0.92, color=COLOR_POSTGIS, label="PostGIS/GiST")
ax.set_xticks(list(xs))
ax.set_xticklabels([str(n) for n in SIZES])
ax.set_xlabel("N (puntos)")
ax.set_ylabel("espacio del indice (KB)")
ax.set_title("Espacio en disco del indice")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "espacio_kb.png"))
plt.close(fig)

# 3. escalamiento con N -- range query a radio fijo (3 tecnicas)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
subset = sorted([r for r in RANGE_QUERIES if r["radio_km"] == RADIO_FOCO], key=lambda r: r["n"])
subset_pg = sorted([r for r in RANGE_QUERIES_PG if r["radio_km"] == RADIO_FOCO], key=lambda r: r["n"])
linea(ax, [r["n"] for r in subset], [r["us_rtree"] for r in subset], COLOR_RTREE, "R-Tree")
linea(ax, [r["n"] for r in subset], [r["us_secuencial"] for r in subset], COLOR_SECUENCIAL, "Secuencial")
linea(ax, [r["n"] for r in subset_pg], [r["us_postgis"] for r in subset_pg], COLOR_POSTGIS, "PostGIS/GiST")
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel("N (puntos)")
ax.set_ylabel("tiempo de consulta (us, log)")
ax.set_title(f"Consulta por radio = {RADIO_FOCO}km, segun N")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "range_escalamiento_us.png"))
plt.close(fig)

# 4. escalamiento con N -- knn a k fijo (3 tecnicas)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
subset = sorted([r for r in KNN_QUERIES if r["k"] == K_FOCO], key=lambda r: r["n"])
subset_pg = sorted([r for r in KNN_QUERIES_PG if r["k"] == K_FOCO], key=lambda r: r["n"])
linea(ax, [r["n"] for r in subset], [r["us_rtree"] for r in subset], COLOR_RTREE, "R-Tree")
linea(ax, [r["n"] for r in subset], [r["us_secuencial"] for r in subset], COLOR_SECUENCIAL, "Secuencial")
linea(ax, [r["n"] for r in subset_pg], [r["us_postgis"] for r in subset_pg], COLOR_POSTGIS, "PostGIS/GiST")
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel("N (puntos)")
ax.set_ylabel("tiempo de consulta (us, log)")
ax.set_title(f"k-NN con k = {K_FOCO}, segun N")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "knn_escalamiento_us.png"))
plt.close(fig)

# 5. sensibilidad al radio, a N fijo (el mas grande medido, 3 tecnicas)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
subset = sorted([r for r in RANGE_QUERIES if r["n"] == N_FOCO], key=lambda r: r["radio_km"])
subset_pg = sorted([r for r in RANGE_QUERIES_PG if r["n"] == N_FOCO], key=lambda r: r["radio_km"])
linea(ax, [r["radio_km"] for r in subset], [r["us_rtree"] for r in subset], COLOR_RTREE, "R-Tree")
linea(ax, [r["radio_km"] for r in subset], [r["us_secuencial"] for r in subset], COLOR_SECUENCIAL, "Secuencial")
linea(ax, [r["radio_km"] for r in subset_pg], [r["us_postgis"] for r in subset_pg], COLOR_POSTGIS, "PostGIS/GiST")
ax.set_yscale("log")
ax.set_xlabel("radio de busqueda (km)")
ax.set_ylabel("tiempo de consulta (us, log)")
ax.set_title(f"Sensibilidad al radio (N={N_FOCO:,})")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "range_radio_us.png"))
plt.close(fig)

# 6. sensibilidad a k, a N fijo (el mas grande medido, 3 tecnicas)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
subset = sorted([r for r in KNN_QUERIES if r["n"] == N_FOCO], key=lambda r: r["k"])
subset_pg = sorted([r for r in KNN_QUERIES_PG if r["n"] == N_FOCO], key=lambda r: r["k"])
linea(ax, [r["k"] for r in subset], [r["us_rtree"] for r in subset], COLOR_RTREE, "R-Tree")
linea(ax, [r["k"] for r in subset], [r["us_secuencial"] for r in subset], COLOR_SECUENCIAL, "Secuencial")
linea(ax, [r["k"] for r in subset_pg], [r["us_postgis"] for r in subset_pg], COLOR_POSTGIS, "PostGIS/GiST")
ax.set_yscale("log")
ax.set_xlabel("k (vecinos pedidos)")
ax.set_ylabel("tiempo de consulta (us, log)")
ax.set_title(f"Sensibilidad a k (N={N_FOCO:,})")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "knn_k_us.png"))
plt.close(fig)

print(f"\nOK: 6 graficas guardadas en {OUT_DIR}")
