"""
Genera las graficas comparativas (Heap File vs. Sequential File) para
README.md a partir del JSON que deja file_benchmark.py

Uso:
    python -m benchmark.files.file_benchmark        # genera el JSON
    python -m benchmark.files.plot_file_benchmark   # lee el JSON y grafica
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

ORGANIZACIONES = list(dict.fromkeys(r["nombre"] for r in resultados))
SIZES = sorted(set(r["n"] for r in resultados))

# paleta fija: color = identidad de la organizacion, no cambia de chart a chart
COLOR = {"Heap File": "#2a78d6", "Sequential File": "#1baf7a"}
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


def linea(ax, campo, label_extra=""):
    for nombre in ORGANIZACIONES:
        xs = [r["n"] for r in resultados if r["nombre"] == nombre]
        ys = [r[campo] for r in resultados if r["nombre"] == nombre]
        ax.plot(xs, ys, marker="o", markersize=5, linewidth=2,
                color=COLOR[nombre], label=nombre + label_extra)


def barras(ax, campos, etiquetas):
    """Barras agrupadas por organizacion, una barra por campo (usa el N mayor)."""
    n_max = SIZES[-1]
    ancho = 0.35
    xs = range(len(campos))
    for i, nombre in enumerate(ORGANIZACIONES):
        r = next(r for r in resultados if r["nombre"] == nombre and r["n"] == n_max)
        alturas = [r[c] for c in campos]
        pos = [x + (i - 0.5) * ancho for x in xs]
        ax.bar(pos, alturas, ancho, color=COLOR[nombre], label=nombre, zorder=3)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(etiquetas)


def guardar(fig, nombre_archivo):
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, nombre_archivo), facecolor=SURFACE)
    plt.close(fig)


os.makedirs(OUT_DIR, exist_ok=True)
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["figure.facecolor"] = SURFACE


# --- 1. costo de insercion ---
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
estilo_base(ax)
linea(ax, "us_por_insert")
ax.set_xlabel("N (registros)")
ax.set_ylabel("microsegundos por insert")
ax.set_title("Costo de inserción")
ax.legend(frameon=False)
guardar(fig, "insercion_us.png")


# --- 2. espacio ocupado ---
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
estilo_base(ax)
linea(ax, "espacio_kb")
ax.set_xlabel("N (registros)")
ax.set_ylabel("tamaño del archivo (KB)")
ax.set_title("Espacio ocupado")
ax.legend(frameon=False)
guardar(fig, "espacio_kb.png")


# --- 3. busqueda por clave (log: el heap escala con N, el secuencial no) ---
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
estilo_base(ax)
linea(ax, "us_clave")
ax.set_yscale("log")
ax.set_xlabel("N (registros)")
ax.set_ylabel("microsegundos por búsqueda (escala log)")
ax.set_title("Búsqueda por clave")
ax.legend(frameon=False)
guardar(fig, "busqueda_clave_us.png")


# --- 4. busqueda por rango ---
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
estilo_base(ax)
linea(ax, "us_rango")
ax.set_yscale("log")
ax.set_xlabel("N (registros)")
ax.set_ylabel("microsegundos por rango (escala log)")
ax.set_title("Búsqueda por rango (ancho = 1% de N)")
ax.legend(frameon=False)
guardar(fig, "busqueda_rango_us.png")


# --- 5. recorrido completo en orden de clave ---
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
estilo_base(ax)
linea(ax, "ms_orden")
ax.set_xlabel("N (registros)")
ax.set_ylabel("milisegundos")
ax.set_title("Recorrido completo en orden de clave")
ax.legend(frameon=False)
guardar(fig, "orden_completo_ms.png")


# --- 6. eliminacion por clave ---
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
estilo_base(ax)
linea(ax, "us_delete")
ax.set_yscale("log")
ax.set_xlabel("N (registros)")
ax.set_ylabel("microsegundos por eliminación (escala log)")
ax.set_title("Eliminación por clave")
ax.legend(frameon=False)
guardar(fig, "eliminacion_us.png")


# --- 7. costo de reorganizar ---
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
estilo_base(ax)
linea(ax, "ms_reorganize")
ax.set_xlabel("N (registros)")
ax.set_ylabel("milisegundos")
ax.set_title("Reorganización tras borrar el 30%")
ax.legend(frameon=False)
guardar(fig, "reorganizacion_ms.png")


# --- 8. insercion intercalada vs. al final (N mayor) ---
fig, ax = plt.subplots(figsize=(6, 4), dpi=150)
estilo_base(ax)
barras(ax, ["us_reinsert", "us_append"], ["clave intercalada", "clave mayor que todas"])
ax.set_yscale("log")
ax.set_ylabel("microsegundos por insert (escala log)")
ax.set_title(f"Inserción según la posición de la clave (N = {SIZES[-1]:,})")
ax.legend(frameon=False)
guardar(fig, "insercion_por_posicion_us.png")


print("graficas generadas en", OUT_DIR)
