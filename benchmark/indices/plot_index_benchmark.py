"""
Genera las graficas comparativas (B+ clustered, B+ unclustered, Hash
extensible) para README.md a partir del JSON que deja index_benchmark.py

Uso:
    python -m benchmark.indices.index_benchmark   # genera el JSON
    python -m benchmark.indices.plot_index_benchmark   # lee el JSON y grafica
"""

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTADOS_JSON = os.path.join(OUT_DIR, "resultados.json")

with open(RESULTADOS_JSON, encoding="utf-8") as f:
    resultados = json.load(f)

INDICES = list(dict.fromkeys(r["nombre"] for r in resultados))  # preserva orden de aparicion
SIZES = sorted(set(r["n"] for r in resultados))

# paleta fija (dataviz skill): color = identidad del indice, nunca cambia
# de chart a chart
COLOR = {"B+ clustered": "#2a78d6", "B+ unclustered": "#1baf7a", "Hash extensible": "#eda100"}
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


def linea(ax, resultados, campo, label_extra=""):
    for nombre in INDICES:
        xs = [r["n"] for r in resultados if r["nombre"] == nombre]
        ys = [r[campo] for r in resultados if r["nombre"] == nombre]
        ax.plot(xs, ys, marker="o", markersize=5, linewidth=2,
                 color=COLOR[nombre], label=nombre + label_extra)


os.makedirs(OUT_DIR, exist_ok=True)
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["figure.facecolor"] = SURFACE

# 1. construccion (log Y -- el clustered crece ~O(N^2), en escala lineal
# los valores chicos de N no se verian)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
linea(ax, resultados, "tiempo_construccion_ms")
ax.set_yscale("log")
ax.set_xlabel("N (registros)")
ax.set_ylabel("tiempo de construccion (ms, log)")
ax.set_title("Tiempo de construccion del indice")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "construccion_ms.png"))
plt.close(fig)

# 2. espacio adicional (archivo de indice, no el archivo de datos)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
n_series = len(INDICES)
ancho = 0.8 / n_series
xs = range(len(SIZES))
for i, nombre in enumerate(INDICES):
    ys = [r["espacio_indice_kb"] for r in resultados if r["nombre"] == nombre]
    offset = (i - (n_series - 1) / 2) * ancho
    ax.bar([x + offset for x in xs], ys, width=ancho * 0.92, color=COLOR[nombre], label=nombre)
ax.set_xticks(list(xs))
ax.set_xticklabels([str(n) for n in SIZES])
ax.set_xlabel("N (registros)")
ax.set_ylabel("espacio del archivo de indice (KB)")
ax.set_title("Espacio adicional requerido (solo el .idx)")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "espacio_indice_kb.png"))
plt.close(fig)

# 3a. busqueda exacta -- escala propia (el hash gana pero no por
# ordenes de magnitud, se ve bien en lineal)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
linea(ax, resultados, "us_exacta")
ax.set_xlabel("N (registros)")
ax.set_ylabel("tiempo de busqueda exacta (us)")
ax.set_title("Busqueda por igualdad exacta")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "busqueda_exacta_us.png"))
plt.close(fig)

# 3b. busqueda por rango -- escala log: el hash no tiene rango nativo y
# cae a un scan completo del heap, que es ordenes de magnitud mas caro
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
linea(ax, resultados, "us_rango")
ax.set_yscale("log")
ax.set_xlabel("N (registros)")
ax.set_ylabel("tiempo de busqueda por rango (us, log)")
ax.set_title("Busqueda por rango (el hash cae a un scan completo)")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "busqueda_rango_us.png"))
plt.close(fig)

# 4. ordenamiento: recorrido completo en orden de clave
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
linea(ax, resultados, "ms_orden")
ax.set_xlabel("N (registros)")
ax.set_ylabel("tiempo de recorrido ordenado completo (ms)")
ax.set_title("Ordenamiento (recorrido completo por clave)")
ax.legend(frameon=False)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "orden_completo_ms.png"))
plt.close(fig)

# 5. throughput durante insertciones/eliminaciones frecuentes (churn)
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
linea(ax, resultados, "ops_por_seg")
ax.set_yscale("log")
ax.set_xlabel("N (registros)")
ax.set_ylabel("operaciones/segundo (log)")
ax.set_title("Rendimiento durante inserciones/eliminaciones frecuentes")
ax.legend(frameon=False, loc="lower left")
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "churn_ops_seg.png"))
plt.close(fig)

# 6. degradacion de busqueda exacta antes/despues del churn
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
for nombre in INDICES:
    xs = [r["n"] for r in resultados if r["nombre"] == nombre]
    antes = [r["us_exacta"] for r in resultados if r["nombre"] == nombre]
    despues = [r["us_exacta_post_churn"] for r in resultados if r["nombre"] == nombre]
    ax.plot(xs, antes, marker="o", markersize=5, linewidth=2,
             color=COLOR[nombre], linestyle="-", label=f"{nombre} - antes del churn")
    ax.plot(xs, despues, marker="s", markersize=5, linewidth=2,
             color=COLOR[nombre], linestyle="--", label=f"{nombre} - despues del churn")
ax.set_xlabel("N (registros)")
ax.set_ylabel("us / busqueda exacta")
ax.set_title("Degradacion de busqueda tras insertar/borrar frecuentemente")
ax.legend(frameon=False, fontsize=8)
estilo_base(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "degradacion_post_churn_us.png"))
plt.close(fig)

print(f"\nOK: 6 graficas guardadas en {OUT_DIR}")
