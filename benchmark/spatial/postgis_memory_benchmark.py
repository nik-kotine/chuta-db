"""Mide la huella de RAM/cache observable del indice GiST de PostGIS.

No intenta usar tracemalloc sobre el cliente Python: la memoria del indice
vive en el proceso de PostgreSQL. Se reporta la cantidad de paginas del
GiST actualmente presentes en shared_buffers mediante pg_buffercache, y,
como referencia, el tamano total del indice en disco.

Requiere PostgreSQL + PostGIS y la extension pg_buffercache. Si pg_buffercache
no esta disponible, el script falla de forma explicita en vez de inventar una
medicion de RAM.
"""
import json
import os
import random
import sys

import psycopg2
import psycopg2.extras

SIZES = [int(a) for a in sys.argv[1:]] or [1_000, 10_000, 100_000]
LON_RANGE = (-0.25, 0.25)
LAT_RANGE = (-0.25, 0.25)
SEED = 42
BENCH_DB = "chuta_spatial_bench"
INDEX_NAME = "idx_points_gist"


def connect(dbname):
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=dbname,
    )


def asegurar_base():
    conn = connect("postgres")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (BENCH_DB,))
    if cur.fetchone() is None:
        cur.execute(f"CREATE DATABASE {BENCH_DB}")
    cur.close()
    conn.close()

    conn = connect(BENCH_DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_buffercache")
    except psycopg2.Error as exc:
        conn.close()
        raise RuntimeError(
            "No se pudo habilitar pg_buffercache. Ejecuta este benchmark con "
            "un usuario con permisos para CREATE EXTENSION o habilita "
            "pg_buffercache en PostgreSQL."
        ) from exc
    cur.close()
    conn.close()


def construir(conn, n):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS points")
    cur.execute("CREATE TABLE points (id serial PRIMARY KEY, geog geography(Point, 4326))")
    conn.commit()

    random.seed(SEED)
    points = [
        (random.uniform(*LON_RANGE), random.uniform(*LAT_RANGE))
        for _ in range(n)
    ]
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO points (geog) VALUES %s",
        points,
        template="(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)",
        page_size=2000,
    )
    conn.commit()

    cur.execute(f"CREATE INDEX {INDEX_NAME} ON points USING GIST (geog)")
    conn.commit()
    cur.execute("ANALYZE points")
    conn.commit()

    # pg_buffercache muestra paginas del indice que estan actualmente en
    # shared_buffers. Es una medida de cache RAM del servidor, no del RSS
    # completo de PostgreSQL.
    cur.execute("SELECT current_setting('block_size')::bigint")
    block_size = cur.fetchone()[0]
    cur.execute(
        """
        SELECT count(*)
        FROM pg_buffercache b
        WHERE b.reldatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND b.relfilenode = pg_relation_filenode(%s::regclass)
        """,
        (INDEX_NAME,),
    )
    buffers = cur.fetchone()[0]

    cur.execute("SELECT pg_relation_size(%s::regclass)", (INDEX_NAME,))
    indice_bytes = cur.fetchone()[0]
    cur.execute("SELECT pg_relation_size('points')")
    tabla_bytes = cur.fetchone()[0]

    cur.close()
    return {
        "tecnica": "PostgreSQL/GiST",
        "n": n,
        "cache_gist_kb": buffers * block_size / 1024,
        "indice_disco_kb": indice_bytes / 1024,
        "tabla_disco_kb": tabla_bytes / 1024,
        "paginas_gist_en_buffer": buffers,
    }


def main():
    asegurar_base()
    conn = connect(BENCH_DB)
    resultados = []

    for n in SIZES:
        print(f"midiendo GiST N={n:,}...", end=" ", flush=True)
        resultado = construir(conn, n)
        resultados.append(resultado)
        print("OK")

    conn.close()

    print("\n--- PostgreSQL/GiST: RAM cache vs disco ---")
    print(f"{'N':>8} | {'GiST cache (KB)':>15} | {'GiST disco (KB)':>15} | {'paginas':>8}")
    print("-" * 58)
    for r in resultados:
        print(
            f"{r['n']:>8,} | {r['cache_gist_kb']:>15.1f} | "
            f"{r['indice_disco_kb']:>15.1f} | {r['paginas_gist_en_buffer']:>8}"
        )

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados_memoria_postgis.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2)
    print(f"\nOK: resultados guardados en {out}")


if __name__ == "__main__":
    main()
