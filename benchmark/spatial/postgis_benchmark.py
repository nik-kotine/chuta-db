# Benchmark de PostGIS/GiST, para comparar contra R-Tree y busqueda
# secuencial (ver rtree_benchmark.py). Vive fuera de tests/ por la misma
# razon que el resto de benchmark/: es rendimiento contra un motor externo,
# no correccion del codigo de este repo.
#
# Requiere un servidor PostgreSQL local con la extension PostGIS instalada
# y las credenciales en las variables de entorno estandar de libpq
# (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE -- PGDATABASE debe apuntar a
# una base de mantenimiento como "postgres", este script crea su propia
# base de benchmark).
import json
import os
import random
import sys
import time
from statistics import mean

import psycopg2
import psycopg2.extras

REPEATS_QUERY = 100
SIZES = [int(a) for a in sys.argv[1:]] or [1_000, 10_000, 100_000]
RADII_KM = [1, 5, 10]
KS = [10, 50, 100]

LON_RANGE = (-0.25, 0.25)
LAT_RANGE = (-0.25, 0.25)

BENCH_DB = "chuta_spatial_bench"


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
    cur.close()
    conn.close()


def construir(conn, n, seed):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS points")
    cur.execute("CREATE TABLE points (id serial PRIMARY KEY, geog geography(Point, 4326))")
    conn.commit()

    random.seed(seed)
    points = [
        (random.uniform(*LON_RANGE), random.uniform(*LAT_RANGE))
        for _ in range(n)
    ]

    t0 = time.perf_counter()
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO points (geog) VALUES %s",
        points,
        template="(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)",
        page_size=2000,
    )
    conn.commit()
    t_insert = time.perf_counter() - t0

    t0 = time.perf_counter()
    cur.execute("CREATE INDEX idx_points_gist ON points USING GIST (geog)")
    conn.commit()
    t_index = time.perf_counter() - t0

    cur.execute("ANALYZE points")
    conn.commit()

    cur.execute("SELECT pg_relation_size('points')")
    tabla_bytes = cur.fetchone()[0]
    cur.execute("SELECT pg_relation_size('idx_points_gist')")
    indice_bytes = cur.fetchone()[0]

    cur.close()
    return points, {
        "n": n,
        "ms_insert": t_insert * 1000,
        "ms_index": t_index * 1000,
        "ms_construccion": (t_insert + t_index) * 1000,
        "tabla_kb": tabla_bytes / 1024,
        "indice_kb": indice_bytes / 1024,
    }


def medir_range_queries(conn, n):
    cur = conn.cursor()
    resultados = []
    for radio_km in RADII_KM:
        radio_m = radio_km * 1000
        tiempos, tamanos = [], []
        for _ in range(REPEATS_QUERY):
            lon, lat = random.uniform(*LON_RANGE), random.uniform(*LAT_RANGE)
            t0 = time.perf_counter()
            cur.execute(
                "SELECT id FROM points WHERE ST_DWithin("
                "geog, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)",
                (lon, lat, radio_m),
            )
            rows = cur.fetchall()
            tiempos.append(time.perf_counter() - t0)
            tamanos.append(len(rows))
        resultados.append({
            "n": n,
            "radio_km": radio_km,
            "selectividad_pct": mean(tamanos) / n * 100,
            "us_postgis": mean(tiempos) * 1e6,
        })
    cur.close()
    return resultados


def medir_knn_queries(conn, n):
    cur = conn.cursor()
    resultados = []
    for k in KS:
        tiempos = []
        for _ in range(REPEATS_QUERY):
            lon, lat = random.uniform(*LON_RANGE), random.uniform(*LAT_RANGE)
            t0 = time.perf_counter()
            cur.execute(
                "SELECT id FROM points ORDER BY "
                "geog <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography LIMIT %s",
                (lon, lat, k),
            )
            cur.fetchall()
            tiempos.append(time.perf_counter() - t0)
        resultados.append({"n": n, "k": k, "us_postgis": mean(tiempos) * 1e6})
    cur.close()
    return resultados


asegurar_base()
conn = connect(BENCH_DB)

resultados_escalamiento = []
resultados_range = []
resultados_knn = []

print(f"{'N':>8} | {'insert (ms)':>11} | {'index (ms)':>10} | {'tabla (KB)':>10} | {'indice (KB)':>11}")
print("-" * 62)
for n in SIZES:
    points, esc = construir(conn, n, seed=42)
    resultados_escalamiento.append(esc)
    print(f"{esc['n']:>8} | {esc['ms_insert']:>11.2f} | {esc['ms_index']:>10.2f} | "
          f"{esc['tabla_kb']:>10.1f} | {esc['indice_kb']:>11.1f}")

    resultados_range.extend(medir_range_queries(conn, n))
    resultados_knn.extend(medir_knn_queries(conn, n))

print(f"\n{'N':>8} | {'radio (km)':>10} | {'% dataset':>9} | {'us/postgis':>11}")
print("-" * 48)
for r in resultados_range:
    print(f"{r['n']:>8} | {r['radio_km']:>10} | {r['selectividad_pct']:>9.3f} | {r['us_postgis']:>11.2f}")

print(f"\n{'N':>8} | {'k':>5} | {'us/postgis':>11}")
print("-" * 30)
for r in resultados_knn:
    print(f"{r['n']:>8} | {r['k']:>5} | {r['us_postgis']:>11.2f}")

conn.close()

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados_postgis.json")
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({
        "escalamiento": resultados_escalamiento,
        "range_queries": resultados_range,
        "knn_queries": resultados_knn,
    }, f, indent=2)

print(f"\nOK: resultados guardados en {OUT}")
