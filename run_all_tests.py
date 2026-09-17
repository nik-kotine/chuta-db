#!/usr/bin/env python3
"""
Descubre y ejecuta todos los archivos test_*.py bajo tests/.

Cada test del repo esta escrito como un script ejecutable (con un bloque
`if __name__ == "__main__"` o con un loop a nivel de modulo), asi que el
runner los lanza como subprocesos: eso soporta ambos estilos, aisla el
estado entre archivos y no hay que mantener ninguna lista de tests.

Uso:
    python run_all_tests.py              # corre todo lo que haya en tests/
    python run_all_tests.py heap seq     # corre solo los que matcheen
    python run_all_tests.py -v           # muestra la salida de todos
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"


def discover(filters):
    files = sorted(TESTS_DIR.rglob("test_*.py"))
    if filters:
        files = [
            f
            for f in files
            if any(term.lower() in str(f.relative_to(ROOT)).lower() for term in filters)
        ]
    return files


def run_file(path):
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing if existing else "")

    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout, time.perf_counter() - start


def main():
    args = sys.argv[1:]
    verbose = "-v" in args or "--verbose" in args
    filters = [a for a in args if not a.startswith("-")]

    files = discover(filters)
    if not files:
        print(f"No se encontraron tests en {TESTS_DIR}")
        return 1

    print("=" * 64)
    print(f"Ejecutando {len(files)} archivo(s) de test")
    print("=" * 64)

    failures = []
    total_time = 0.0

    for index, path in enumerate(files, start=1):
        rel = path.relative_to(ROOT)
        print(f"\n[{index}/{len(files)}] {rel}")

        code, output, elapsed = run_file(path)
        total_time += elapsed

        if output and (verbose or code != 0):
            print(output, end="" if output.endswith("\n") else "\n")

        if code == 0:
            print(f"PASS  {rel}  ({elapsed:.2f}s)")
        else:
            print(f"FAIL  {rel}  (exit {code}, {elapsed:.2f}s)")
            failures.append(rel)

    print("\n" + "=" * 64)
    passed = len(files) - len(failures)
    print(f"Resultado: {passed}/{len(files)} archivos OK en {total_time:.2f}s")
    if failures:
        print("Fallaron:")
        for rel in failures:
            print(f"  - {rel}")
    print("=" * 64)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
