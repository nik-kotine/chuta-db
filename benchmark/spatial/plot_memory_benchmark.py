"""Genera graficas comparativas de memoria para Secuencial, R-Tree y GiST."""
import json
import os

import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(HERE, "resultados_memoria.json")
POSTGIS_FILE = os.path.join(HERE, "resultados_memoria_postgis.json")


def cargar(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def por_tecnica(rows, tecnica):
    return sorted(
        [r for r in rows if r["tecnica"] == tecnica],
        key=lambda r: r["n"],
    )


def main():
    memoria = cargar(MEMORY_FILE)
    postgis = cargar(POSTGIS_FILE)

    sec = por_tecnica(memoria, "Secuencial")
    rtree = por_tecnica(memoria, "R-Tree")
    gist = por_tecnica(postgis, "PostgreSQL/GiST")

    ns = [r["n"] for r in sec]
    ns_gist = [r["n"] for r in gist]

    # 1) Comparacion de RAM observable. Para GiST es la porcion del indice
    # que esta actualmente en shared_buffers, no el RSS completo de Postgres.
    plt.figure(figsize=(8, 5))
    plt.plot(ns, [r["residente_kb"] for r in sec], marker="o", label="Secuencial")
    plt.plot(ns, [r["residente_kb"] for r in rtree], marker="o", label="R-Tree")
    plt.plot(ns_gist, [r["cache_gist_kb"] for r in gist], marker="o", label="PostgreSQL/GiST")
    plt.xlabel("Numero de puntos (N)")
    plt.ylabel("RAM / cache observable (KB)")
    plt.title("Uso de memoria: Secuencial vs R-Tree vs PostgreSQL/GiST")
    plt.xscale("log")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    out1 = os.path.join(HERE, "memoria_comparacion_kb.png")
    plt.savefig(out1, dpi=160)
    plt.close()

    # 2) Costo por punto para las dos mediciones directamente comparables
    # del proyecto: secuencial/R-Tree con tracemalloc. GiST no se inventa
    # como bytes/punto porque su cache es compartida por PostgreSQL.
    plt.figure(figsize=(8, 5))
    plt.plot(ns, [r["bytes_por_punto"] for r in sec], marker="o", label="Secuencial")
    plt.plot(ns, [r["bytes_por_punto"] for r in rtree], marker="o", label="R-Tree")
    plt.xlabel("Numero de puntos (N)")
    plt.ylabel("Bytes por punto")
    plt.title("Costo de RAM por punto: Secuencial vs R-Tree")
    plt.xscale("log")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    out2 = os.path.join(HERE, "bytes_por_punto_memoria.png")
    plt.savefig(out2, dpi=160)
    plt.close()

    # 3) Huella del indice en disco: aqui las tres tecnicas no tienen la
    # misma definicion de almacenamiento, asi que la secuencial representa
    # el arreglo de puntos medido por tracemalloc, solo como referencia.
    plt.figure(figsize=(8, 5))
    plt.plot(ns, [r["residente_kb"] for r in sec], marker="o", label="Secuencial (datos en RAM)")
    plt.plot(ns, [r["disco_kb"] for r in rtree], marker="o", label="R-Tree (indice en disco)")
    plt.plot(ns_gist, [r["indice_disco_kb"] for r in gist], marker="o", label="PostgreSQL/GiST (indice en disco)")
    plt.xlabel("Numero de puntos (N)")
    plt.ylabel("Huella (KB)")
    plt.title("Huella de almacenamiento: Secuencial vs R-Tree vs GiST")
    plt.xscale("log")
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    out3 = os.path.join(HERE, "huella_secuencial_rtree_gist.png")
    plt.savefig(out3, dpi=160)
    plt.close()

    print("Graficas generadas:")
    print(out1)
    print(out2)
    print(out3)
    print("\nNota: GiST se mide como paginas del indice presentes en shared_buffers; no es el RSS total del servidor PostgreSQL.")


if __name__ == "__main__":
    main()
