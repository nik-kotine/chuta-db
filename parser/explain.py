"""
Formateo del plan de ejecucion al estilo de PostgreSQL / pgAdmin.

Toma las entradas que el ExecuteVisitor va dejando en `self.plan` y las
arma como el arbol de nodos que imprime `EXPLAIN [ANALYZE]`:

    Limit  (cost=0.00..1.29 rows=10 width=24) (actual time=0.021..0.043 rows=10 loops=1)
      ->  Sort  (cost=0.00..1.29 rows=120 width=24) (actual time=0.020..0.038 rows=120 loops=1)
            Sort Key: distancia(ubicacion, POINT(-12.0464, -77.0428))
            Sort Method: external merge
            ->  Seq Scan on tiendas  (cost=0.00..18.50 rows=850 width=24) (actual time=0.004..0.031 rows=120 loops=1)
                  Filter: (distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000)
                  Rows Removed by Filter: 730
    Planning Time: 0.112 ms
    Execution Time: 0.287 ms

Sobre los costos: son estimaciones con el mismo modelo de PostgreSQL
(seq_page_cost, random_page_cost, cpu_tuple_cost...), calculadas a partir
del tamano del archivo. No son tiempos: el tiempo real sale de
`actual time`, que solo aparece con ANALYZE.
"""

# Constantes de costo de PostgreSQL (postgresql.conf, valores por defecto)
SEQ_PAGE_COST = 1.0
RANDOM_PAGE_COST = 4.0
CPU_TUPLE_COST = 0.01
CPU_INDEX_TUPLE_COST = 0.005
CPU_OPERATOR_COST = 0.0025

# Selectividad por defecto cuando no hay estadisticas de la columna,
# igual que los valores que usa el planificador de PostgreSQL
SELECTIVIDAD_IGUALDAD = 0.005      # DEFAULT_EQ_SEL
SELECTIVIDAD_RANGO = 0.3333        # DEFAULT_INEQ_SEL   (rango abierto: x > v)
SELECTIVIDAD_RANGO_CERRADO = 0.005  # DEFAULT_RANGE_INEQ_SEL (BETWEEN a AND b)


class NodoPlan:
    """Un nodo del arbol de ejecucion."""

    def __init__(self, etiqueta, costo_inicial=0.0, costo_total=0.0,
                 filas=0, ancho=0, detalles=None, medicion=None):
        self.etiqueta = etiqueta
        self.costo_inicial = costo_inicial
        self.costo_total = costo_total
        self.filas = filas
        self.ancho = ancho
        self.detalles = detalles or []      # "Filter: ...", "Sort Key: ..."
        self.medicion = medicion            # dict con actual_* o None
        self.hijo = None

    def _linea(self, analyze: bool) -> str:
        texto = (
            f"{self.etiqueta}  (cost={self.costo_inicial:.2f}..{self.costo_total:.2f} "
            f"rows={int(self.filas)} width={int(self.ancho)})"
        )
        if not analyze:
            return texto

        m = self.medicion or {}
        inicio = m.get("actual_first_ms", m.get("actual_start_ms", 0.0))
        fin = m.get("actual_end_ms", inicio)
        filas = int(m.get("actual_rows", self.filas))
        loops = int(m.get("loops", 1))
        return (
            f"{texto} (actual time={inicio:.3f}..{fin:.3f} "
            f"rows={filas} loops={loops})"
        )

    def render(self, analyze: bool, nivel: int = 0) -> list:
        """Devuelve las lineas del subarbol, con la sangria de PostgreSQL."""
        columna = 6 * nivel
        lineas = []

        if nivel == 0:
            lineas.append(self._linea(analyze))
        else:
            lineas.append(" " * (columna - 4) + "->  " + self._linea(analyze))

        sangria_detalle = " " * (columna + 2)
        for detalle in self.detalles:
            lineas.append(sangria_detalle + detalle)

        if self.medicion and analyze:
            descartadas = int(self.medicion.get("rows_removed", 0) or 0)
            if descartadas:
                lineas.append(sangria_detalle + f"Rows Removed by Filter: {descartadas}")

        if self.hijo is not None:
            lineas.extend(self.hijo.render(analyze, nivel + 1))
        return lineas


# ---------------------------------------------------------------------
# Construccion del arbol a partir de las entradas del executor
# ---------------------------------------------------------------------

# Nodos que EXPLAIN muestra para sentencias que no son SELECT
NODOS_DML = ("Insert", "Update", "Delete", "Create Table", "Create Index",
             "Drop Table")


def _buscar(entradas, *nodos):
    for entrada in entradas:
        if entrada.get("node") in nodos:
            return entrada
    return None


def _nodo_scan(entrada, stats, filtro):
    """Nodo hoja: el acceso a la tabla (secuencial, por indice o bitmap)."""
    paginas, filas_tabla, ancho = stats
    tabla = entrada.get("table", "?")
    tipo = entrada.get("node")

    if tipo == "SEQUENTIAL SCAN":
        etiqueta = f"Seq Scan on {tabla}"
        costo_inicial = 0.0
        costo_total = paginas * SEQ_PAGE_COST + filas_tabla * CPU_TUPLE_COST
        if filtro:
            costo_total += filas_tabla * CPU_OPERATOR_COST
            filas = max(1, int(filas_tabla * SELECTIVIDAD_RANGO))
        else:
            filas = filas_tabla

    elif tipo == "INDEX SCAN":
        organizacion = entrada.get("index", "indice")
        columna = entrada.get("column", "")
        etiqueta = f"Index Scan using {organizacion}_{tabla}_{columna} on {tabla}"
        acceso = entrada.get("access", "eq")
        if acceso == "eq":
            selectividad = SELECTIVIDAD_IGUALDAD
        elif entrada.get("bounded"):
            selectividad = SELECTIVIDAD_RANGO_CERRADO
        else:
            selectividad = SELECTIVIDAD_RANGO
        filas = max(1, int(filas_tabla * selectividad))
        costo_inicial = 0.29
        costo_total = costo_inicial + filas * (RANDOM_PAGE_COST + CPU_TUPLE_COST)

    elif tipo == "BITMAP INDEX SCAN":
        columnas = ", ".join(entrada.get("columns", []))
        etiqueta = f"Bitmap Heap Scan on {tabla}"
        filas = int(entrada.get("matches", 0)) or 1
        costo_inicial = 0.0
        costo_total = filas * (SEQ_PAGE_COST + CPU_TUPLE_COST)
        detalles = [f"Bitmap Index Scan on {columnas}"] if columnas else []
        nodo = NodoPlan(etiqueta, costo_inicial, costo_total, filas, ancho,
                        detalles=detalles, medicion=entrada)
        if filtro:
            nodo.detalles.append(f"Filter: ({filtro})")
        return nodo

    else:
        etiqueta = f"Scan on {tabla}"
        costo_inicial, costo_total, filas = 0.0, 0.0, filas_tabla

    nodo = NodoPlan(etiqueta, costo_inicial, costo_total, filas, ancho,
                    medicion=entrada)
    if filtro:
        # PostgreSQL distingue el predicado que resuelve el indice
        # ("Index Cond") del que se evalua despues sobre cada fila ("Filter")
        clave = "Index Cond" if tipo == "INDEX SCAN" else "Filter"
        nodo.detalles.append(f"{clave}: ({filtro})")
    return nodo


def construir(entradas, stats, filtro=None, limite=None, total_ms=0.0):
    """
    Arma el arbol de nodos, del mas interno al mas externo, siguiendo el
    orden en que PostgreSQL los anida: acceso -> join -> agregacion ->
    orden -> limite.
    """
    paginas, filas_tabla, ancho = stats

    scan = _buscar(entradas, "SEQUENTIAL SCAN", "INDEX SCAN", "BITMAP INDEX SCAN")
    if scan is not None:
        raiz = _nodo_scan(scan, stats, filtro)
    else:
        # Sentencias que no son SELECT: no hay plan de acceso que elegir,
        # solo el nodo de la operacion (igual que EXPLAIN INSERT en psql).
        dml = _buscar(entradas, *NODOS_DML)
        if dml is not None:
            etiqueta = dml["node"]
            tabla = dml.get("table")
            if tabla and tabla != "?":
                etiqueta = f"{etiqueta} on {tabla}"
            raiz = NodoPlan(etiqueta, 0.0, CPU_TUPLE_COST, 1, ancho)
        else:
            raiz = NodoPlan("Result", 0.0, 0.01, 1, ancho)

    join = _buscar(entradas, "HASH JOIN")
    if join is not None:
        nodo = NodoPlan(
            "Hash Join",
            raiz.costo_total,
            raiz.costo_total + raiz.filas * CPU_TUPLE_COST,
            raiz.filas, ancho * 2,
            detalles=[f"Hash Cond: join con {join.get('table', '?')}"],
        )
        nodo.hijo = raiz
        raiz = nodo

    agregacion = _buscar(entradas, "HASH AGGREGATE")
    if agregacion is not None:
        nodo = NodoPlan(
            "HashAggregate",
            raiz.costo_total,
            raiz.costo_total + raiz.filas * CPU_OPERATOR_COST,
            max(1, raiz.filas // 10), ancho,
        )
        nodo.hijo = raiz
        raiz = nodo

    orden = _buscar(entradas, "EXTERNAL SORT")
    if orden is not None:
        columna = orden.get("column", "?")
        direccion = orden.get("direction", "ASC")
        sufijo = " DESC" if direccion == "DESC" else ""
        nodo = NodoPlan(
            "Sort",
            raiz.costo_total,
            raiz.costo_total + max(1, raiz.filas) * CPU_OPERATOR_COST,
            raiz.filas, ancho,
            detalles=[f"Sort Key: {columna}{sufijo}", "Sort Method: external merge"],
        )
        nodo.hijo = raiz
        raiz = nodo

    if limite is not None:
        filas = min(limite, raiz.filas) if raiz.filas else limite
        proporcion = (filas / raiz.filas) if raiz.filas else 1.0
        nodo = NodoPlan(
            "Limit",
            raiz.costo_inicial,
            raiz.costo_inicial + (raiz.costo_total - raiz.costo_inicial) * proporcion,
            filas, ancho,
        )
        nodo.hijo = raiz
        raiz = nodo

    salida = _buscar(entradas, "OUTPUT")
    filas_reales = int(salida.get("rows", 0)) if salida else None
    _propagar_medicion(raiz, total_ms, filas_reales)
    return raiz


# Nodos que tienen que consumir toda su entrada antes de devolver la
# primera fila: su "actual time" arranca cuando el hijo ya termino.
NODOS_BLOQUEANTES = ("Sort", "HashAggregate", "Hash Join")


def _propagar_medicion(nodo, total_ms, filas_salida):
    """
    Completa actual_* en los nodos que no se midieron directamente,
    de adentro hacia afuera.

    Solo el nodo de acceso lleva contadores propios (es el unico que
    recorre filas). Los de arriba heredan las filas del hijo, salvo el
    Limit, que las recorta, y la agregacion, que las colapsa.
    """
    hijo = nodo.hijo
    medicion_hijo = _propagar_medicion(hijo, total_ms, None) if hijo else None

    if nodo.medicion is not None:
        return nodo.medicion

    if medicion_hijo is not None:
        filas_hijo = int(medicion_hijo.get("actual_rows", nodo.filas))
        if nodo.etiqueta == "Limit":
            filas = min(nodo.filas, filas_hijo)
        elif nodo.etiqueta == "HashAggregate":
            filas = filas_salida if filas_salida is not None else nodo.filas
        else:
            filas = filas_hijo

        if nodo.etiqueta in NODOS_BLOQUEANTES:
            inicio = medicion_hijo.get("actual_end_ms", 0.0)
        else:
            inicio = medicion_hijo.get("actual_first_ms", 0.0)
    else:
        filas = filas_salida if filas_salida is not None else nodo.filas
        inicio = 0.0

    nodo.medicion = {
        "actual_first_ms": inicio,
        "actual_end_ms": total_ms,
        "actual_rows": filas,
        "loops": 1,
    }
    return nodo.medicion


def formatear(raiz, analyze, planning_ms, execution_ms) -> list:
    """Lineas completas del EXPLAIN, incluidos los tiempos finales."""
    lineas = raiz.render(analyze)
    lineas.append(f"Planning Time: {planning_ms:.3f} ms")
    if analyze:
        lineas.append(f"Execution Time: {execution_ms:.3f} ms")
    return lineas
