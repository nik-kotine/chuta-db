"""
Puente entre el parser SQL y el motor de almacenamiento.

El parser no conoce al motor y el motor no conoce al parser: este modulo
es el unico que habla los dos idiomas. Recorre el AST con el patron
Visitor (igual que PrintVisitor) y traduce cada nodo a llamadas del
StorageManager.

Resuelve las tres traducciones que faltaban:
  - DataType del parser  ->  string de tipo del motor ("integer", "varchar(20)")
  - PRIMARY KEY          ->  key_index (la posicion de la columna)
  - nombre de columna    ->  posicion, via Table.column_index()
"""

import sys
import os
import json
import math
import time
from contextlib import contextmanager

# Los modulos del parser usan imports absolutos y tambien se ejecutan como
# paquete (`parser.executor`). En ambos casos, la carpeta de este archivo es
# la que contiene `ast_sql.py`, `visitor.py` y el resto del front-end.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from itertools import islice
from ast_sql import (AggFun, AndCond, BetweenCond, BoolValue, ColRef, Cond,
                     CompareCond, DataType, DistanceExpr, FileOrg,
                     FloatValue, IndexKind, IntValue, Metrica, OrCond,
                     PointValue, PolygonValue, WithinExpr,
                     RelOp, SortDir, StrValue)
from ast_sql import SelectStmt
from visitor import Visitor
import explain as explain_fmt

# Nodo que muestra EXPLAIN para las sentencias que no son SELECT
_NODO_DML = {
    "InsertStmt": "Insert",
    "UpdateStmt": "Update",
    "DeleteStmt": "Delete",
    "CreateTableStmt": "Create Table",
    "CreateIndexStmt": "Create Index",
    "DropTableStmt": "Drop Table",
}
from storage.storage_manager import StorageManager
from indexes.external_sort import ExternalSorter
from indexes.external_hash import ExternalHasher
from indexes.extendible_hash import HashIndex
from indexes.bitmap_index import BitmapIndex
from storage.index_manager import RTreeSecondaryIndex
from storage.formats.serializers.variable_length_serializer import VariableLengthRecordSerializer
from storage.lock_manager import LockMode
from storage.rid import RID

EXTERNAL_SORT_BUDGET = 1000
EXTERNAL_HASH_BUCKETS = 16

class ExecutionError(Exception):
    """Error al ejecutar una sentencia ya parseada."""
    pass

TIPOS = {
    DataType.INT_TYPE: "integer",
    DataType.FLOAT_TYPE: "float",
    DataType.BOOL_TYPE: "boolean",
    DataType.DATE_TYPE: "date",
    DataType.POINT_TYPE: "point",
}

def tipo_a_str(columna) -> str:
    """Convierte un ColumnDec del parser al string de tipo que espera Table."""
    if columna.tipo == DataType.VARCHAR_TYPE:
        return f"varchar({columna.longitud})"
    if columna.tipo not in TIPOS:
        raise ExecutionError(f"Tipo no soportado por el motor: {columna.tipo}")
    return TIPOS[columna.tipo]

def buscar_key_index(columnas) -> int:
    """Devuelve la posicion de la columna marcada como PRIMARY KEY."""
    for i, c in enumerate(columnas):
        if c.primary_key:
            return i
    return -1

class Resultado:
    """Salida de una sentencia: filas, columnas y una descripcion del plan."""

    def __init__(self, mensaje="", columnas=None, filas=None, plan=None):
        self.mensaje = mensaje
        self.columnas = columnas or []
        self.filas = filas or []
        self.plan = plan or []

    def __str__(self):
        if not self.columnas:
            return self.mensaje
        lineas = [" | ".join(self.columnas)]
        lineas.append("-" * len(lineas[0]))
        for fila in self.filas:
            lineas.append(" | ".join(str(v) for v in fila))
        lineas.append(f"({len(self.filas)} fila(s))")
        return "\n".join(lineas)


PAGE_SIZE_ESTIMADO = 4096

# Las metricas espaciales viven en spatial/geometry.py, que es tambien lo
# que usa el R-Tree. Importarlas de ahi en vez de reimplementarlas evita
# que la distancia que calcula el filtro difiera de la que usa el indice:
# con dos radios terrestres distintos, una fila podia entrar por el indice
# y quedar fuera del filtro posterior.
from spatial.geometry import (Point as GeoPoint, Rectangle, euclidean,
                              haversine, point_in_polygon)


def _ancho_estimado(tipo: str) -> int:
    """Ancho aproximado de una columna, para el 'width' del plan."""
    try:
        from storage.formats.data_types import return_format
        _fmt, size = return_format(tipo)
        return size if size > 0 else 24
    except Exception:
        return 24


class _MedicionNodo:
    """Contadores de un nodo del plan durante EXPLAIN ANALYZE."""

    def __init__(self, entrada):
        self.entrada = entrada
        self.filas = 0
        self.descartadas = 0
        self.t_primera = None

    def emitida(self):
        if self.t_primera is None:
            self.t_primera = time.perf_counter()
        self.filas += 1

    def descartada(self):
        self.descartadas += 1


class ExecuteVisitor(Visitor):
    """Ejecuta un AST del parser contra el StorageManager."""

    def __init__(self, storage_manager: StorageManager):
        self.sm = storage_manager
        self._t0 = time.perf_counter()
        self._planning_ms = 0.0
        self._promovidos = []
        self.resultado = None
        self.plan = []
        self.transaction_id = None
        self.session_id = self.sm.allocate_session_id()

    def ejecutar(self, programa) -> list:
        salidas = []
        for stmt in programa.slist:
            self.plan = []
            self._planning_ms = 0.0
            self._t0 = time.perf_counter()
            try:
                stmt.accept(self)
            except Exception:
                self._abort_active_transaction()
                raise
            salidas.append(self.resultado)
        return salidas

    def visit_create_table_stmt(self, stm):
        schema = [tipo_a_str(c) for c in stm.columnas]
        column_names = [c.nombre for c in stm.columnas]

        key_index = buscar_key_index(stm.columnas)
        if key_index == -1:
            raise ExecutionError(
                f"La tabla '{stm.tabla}' debe declarar una columna PRIMARY KEY"
            )

        file_type = "heap" if stm.org == FileOrg.HEAP_ORG else "sequential"

        self._log_ddl(
            "create_table",
            stm.tabla,
            {
                "schema": schema,
                "file_type": file_type,
                "key_index": key_index,
                "column_names": column_names,
            },
        )

        self.sm.create_table(
            name=stm.tabla,
            schema=schema,
            file_type=file_type,
            key_index=key_index,
            column_names=column_names,
        )
        self.resultado = Resultado(
            f"Tabla '{stm.tabla}' creada ({file_type}, PK={column_names[key_index]})"
        )

    def visit_insert_stmt(self, stm):
        with self._table_locks([stm.tabla], LockMode.EXCLUSIVE):
            tabla = self._abrir(stm.tabla)
            valores = [self._valor(v) for v in stm.valores]

            if len(valores) != len(tabla.schema):
                raise ExecutionError(
                    f"La tabla '{stm.tabla}' espera {len(tabla.schema)} valores, "
                    f"se dieron {len(valores)}"
                )

            # Un POINT tiene que llegar como par (x, y). Sin este chequeo
            # el error sale desde el serializador como un struct.error.
            for pos, (tipo, valor) in enumerate(zip(tabla.schema, valores)):
                if tipo == "point" and (
                    not isinstance(valor, (tuple, list)) or len(valor) != 2
                ):
                    raise ExecutionError(
                        f"La columna '{tabla.column_names[pos]}' es POINT y "
                        f"espera POINT(x, y); se recibio {valor!r}"
                    )

            tabla.insert(valores, mutation_logger=self._mutation_logger())
        self.resultado = Resultado("1 fila insertada")

    def visit_select_stmt(self, stm):
        nombres_bloqueados = [stm.tabla]
        if stm.join is not None:
            nombres_bloqueados.append(stm.join.tabla)
        with self._table_locks(nombres_bloqueados, LockMode.SHARED):
            self._visit_select_locked(stm)

    def _visit_select_locked(self, stm):
        tabla = self._abrir(stm.tabla)
        derecha = None
        self.plan = [{"node": "SELECT", "table": stm.tabla, "operation": "project"}]

        if stm.join is not None:
            self.plan.append({"node": "HASH JOIN", "table": stm.join.tabla, "operation": "join"})
            derecha = self._abrir(stm.join.tabla)
            tablas = [(stm.tabla, tabla), (stm.join.tabla, derecha)]
            resolver = self._resolver_columnas(tablas)
            serializador = VariableLengthRecordSerializer(
                list(tabla.schema) + list(derecha.schema)
            )
            filas = self._filas_join(tabla, derecha, stm, resolver, serializador)
            if not self._tiene_agregados(stm) and stm.order_by is not None:
                self.plan.append({"node": "EXTERNAL SORT", "operation": "sort", "column": self._etiqueta_orden(stm.order_by), "direction": "DESC" if stm.direccion == SortDir.DESC_DIR else "ASC"})
                filas = self._ordenar_externo(
                    filas, resolver, stm.order_by,
                    stm.direccion == SortDir.DESC_DIR, serializador,
                )
        else:
            tablas = [(stm.tabla, tabla)]
            resolver = self._resolver_columnas(tablas)
            serializador = tabla.data_file.serializer
            if not self._tiene_agregados(stm) and stm.order_by is not None:
                reverse = stm.direccion == SortDir.DESC_DIR
                plan_knn = self._plan_knn(tabla, stm, resolver, reverse)
                if plan_knn is not None:
                    filas = self._filas_knn(tabla, stm, resolver, plan_knn)
                elif self._indice_para_order_by(tabla, stm.order_by) is not None:
                    filas = self._scan_ordenado_por_indice(tabla, stm.condicion, resolver, stm.order_by, reverse)
                else:
                    self.plan.append({"node": "EXTERNAL SORT", "operation": "sort", "column": self._etiqueta_orden(stm.order_by), "direction": "DESC" if reverse else "ASC"})
                    filas = self._select_ordenado(tabla, stm.condicion, resolver, stm.order_by, reverse)
            else:
                filas = self._scan_filtrado(tabla, stm.condicion, resolver)

        if self._tiene_agregados(stm):
            #self.plan.append({"node": "HASH AGGREGATE", "operation": "aggregate"})
            columnas_agregacion = []
            for item in stm.proyeccion:
                if item.agg != AggFun.NONE_AGG:
                    if item.agg == AggFun.COUNT_AGG:
                        columnas_agregacion.append(tabla.column_names[tabla.key_index])
                    else:
                        columnas_agregacion.append(item.columna.columna)
            self.plan.append({"node": "HASH AGGREGATE", "operation": "aggregate", "column": columnas_agregacion})
            nombres, filas = self._proyeccion_agregada(
                stm, filas, resolver, serializador
            )
            if stm.order_by is not None:
                self.plan.append({
                    "node": "IN-MEMORY SORT", 
                    "operation": "sort", 
                    "column": self._etiqueta_orden(stm.order_by), 
                    "direction": "DESC" if stm.direccion == SortDir.DESC_DIR else "ASC"
                })
                # ----------------------------------------------------------------------
                filas = self._ordenar_resultado(
                    filas, nombres, self._etiqueta_orden(stm.order_by),
                    stm.direccion == SortDir.DESC_DIR,
                )
            if stm.haylimite:
                filas = filas[:stm.limite]
        else:
            if stm.haylimite:
                filas = list(islice(filas, stm.limite))

            if stm.select_all:
                nombres = [col for _, t in tablas for col in t.column_names]
                filas = list(filas)
            else:
                nombres = []
                posiciones = []
                for item in stm.proyeccion:
                    nombres.append(self._nombre_item(item))
                    posiciones.append(resolver(item.columna))
                filas = [[fila[p] for p in posiciones] for fila in filas]

        self.plan.append({"node": "OUTPUT", "operation": "return_rows", "rows": len(filas)})
        self.resultado = Resultado(columnas=nombres, filas=filas, plan=self.plan)

    def visit_delete_stmt(self, stm):
        with self._table_locks([stm.tabla], LockMode.EXCLUSIVE):
            tabla = self._abrir(stm.tabla)
            resolver = self._resolver_columnas([(stm.tabla, tabla)])

            borrados = 0
            for rid, registro in list(tabla.scan()):
                if self._evaluar(stm.condicion, registro, resolver):
                    if tabla.delete(rid, mutation_logger=self._mutation_logger()):
                        borrados += 1
        self.resultado = Resultado(f"{borrados} fila(s) eliminada(s)")

    def visit_update_stmt(self, stm):
        with self._table_locks([stm.tabla], LockMode.UREAD):
            tabla = self._abrir(stm.tabla)
            resolver = self._resolver_columnas([(stm.tabla, tabla)])
            posiciones = {}
            for columna, valor in stm.asignaciones:
                if columna in posiciones:
                    raise ExecutionError(f"la columna '{columna}' aparece mas de una vez en SET")
                posiciones[columna] = tabla.column_index(columna)

            pendientes = []
            for rid, registro in list(tabla.scan()):
                if not self._evaluar(stm.condicion, registro, resolver):
                    continue
                nuevos = list(registro)
                for columna, valor in stm.asignaciones:
                    nuevos[posiciones[columna]] = self._valor(valor)
                pendientes.append((rid, nuevos))

            if pendientes:
                self._promote_table_locks([stm.tabla])

            actualizadas = 0
            for rid, nuevos in pendientes:
                if tabla.update(rid, nuevos, mutation_logger=self._mutation_logger()):
                    actualizadas += 1
        self.resultado = Resultado(f"{actualizadas} fila(s) actualizada(s)")

    def visit_create_index_stmt(self, stm):
        tabla = self._abrir(stm.tabla)
        pos = tabla.column_index(stm.columna)

        if stm.tipo == IndexKind.HASH_IDX:
            # El parser ya rechaza HASH CLUSTERED; aquí solo se valida que
            # la tabla sea Heap (igual que un B+ no agrupado).
            if tabla.file_type != "heap":
                raise ExecutionError(
                    "un indice HASH exige una tabla USING HEAP"
                )
        elif stm.tipo == IndexKind.BITMAP_IDX:
            # El parser ya rechaza BITMAP CLUSTERED; el bitmap apunta a filas
            # del heap, asi que solo tiene sentido alli.
            if tabla.file_type != "heap":
                raise ExecutionError(
                    "un indice BITMAP exige una tabla USING HEAP"
                )
        elif stm.tipo == IndexKind.RTREE_IDX:
            # El R-Tree no ordena totalmente: nunca es agrupado, y apunta a
            # filas del heap igual que el bitmap.
            if stm.clustered:
                raise ExecutionError(
                    "un indice RTREE no puede ser CLUSTERED: no define un "
                    "orden total de los registros"
                )
            if tabla.file_type != "heap":
                raise ExecutionError(
                    "un indice RTREE exige una tabla USING HEAP"
                )
            if tabla.schema[pos].strip().lower() != "point":
                raise ExecutionError(
                    f"un indice RTREE exige una columna POINT; "
                    f"'{stm.columna}' es de tipo '{tabla.schema[pos]}'"
                )
        elif stm.clustered:
            if tabla.file_type != "sequential":
                raise ExecutionError(
                    "un indice CLUSTERED exige una tabla USING SEQUENTIAL"
                )
            if pos != tabla.key_index:
                raise ExecutionError(
                    "un indice CLUSTERED debe indexar la PRIMARY KEY"
                )
        elif tabla.file_type != "heap":
            raise ExecutionError(
                "un indice no agrupado exige una tabla USING HEAP"
            )

        # Cada tipo lleva su sufijo porque sobre una misma columna pueden
        # convivir los cuatro: B+ no agrupado, hash, bitmap y R-Tree. Es UNA
        # sola cadena if/elif: con dos `if` seguidos el R-Tree caia en el
        # `else` de abajo (y salia "..._rtree_unclustered") y el hash se
        # quedaba sin sufijo, compartiendo nombre y .idx con el B+.
        nombre = f"idx_{stm.tabla}_{stm.columna}"
        if stm.tipo == IndexKind.RTREE_IDX:
            nombre = f"{nombre}_rtree"
        elif stm.tipo == IndexKind.BITMAP_IDX:
            nombre = f"{nombre}_bitmap"
        elif stm.tipo == IndexKind.HASH_IDX:
            nombre = f"{nombre}_hash"
        elif stm.clustered:
            nombre = f"{nombre}_clustered"
        else:
            nombre = f"{nombre}_unclustered"

        # El sufijo va antes del log: el rollback de ddl_create_index hace
        # drop_index(payload["index_name"]), asi que el log tiene que llevar
        # el nombre final del indice.
        self._log_ddl(
            "create_index",
            stm.tabla,
            {
                "index_name": nombre,
                "column_name": stm.columna,
                "column_index": pos,
                "index_kind": stm.tipo.name,
                "clustered": stm.clustered,
            },
        )
        if stm.tipo == IndexKind.HASH_IDX:
            self.sm.index_manager.create_hash_index(
                nombre, tabla, pos, stm.columna
            )
            tipo_nombre = "HASH"
            org = "no agrupado"
        elif stm.tipo == IndexKind.BITMAP_IDX:
            self.sm.index_manager.create_bitmap_index(
                nombre, tabla, pos, stm.columna
            )
            tipo_nombre = "BITMAP"
            org = "no agrupado"
        elif stm.tipo == IndexKind.RTREE_IDX:
            self.sm.index_manager.create_rtree_index(
                nombre, tabla, pos, stm.columna
            )
            tipo_nombre = "R-Tree"
            org = "espacial"
        elif stm.clustered:
            self.sm.index_manager.create_clustered_index(
                nombre, tabla, stm.columna
            )
            tipo_nombre = "B+"
            org = "CLUSTERED"
        else:
            self.sm.index_manager.create_unclustered_index(
                nombre, tabla, pos, stm.columna
            )
            tipo_nombre = "B+"
            org = "no agrupado"
        self.resultado = Resultado(
            f"Indice {tipo_nombre} {org} '{nombre}' creado sobre {stm.tabla}({stm.columna})"
        )

    def visit_explain_stmt(self, stm):
        """
        EXPLAIN [ANALYZE] <sentencia>, con la misma salida que pgAdmin.

        Sin ANALYZE solo se planifica: se calcula el plan (que indice se
        usaria, si hace falta ordenar) sin tocar las filas. Con ANALYZE
        se ejecuta de verdad y se agregan las filas y los tiempos reales,
        igual que en PostgreSQL.
        """
        interna = stm.sentencia

        t_plan = time.perf_counter()
        self.plan = []
        self._t0 = time.perf_counter()

        es_select = isinstance(interna, SelectStmt)
        filtro = self._texto_filtro(interna) if es_select else ""
        limite = (
            interna.limite
            if es_select and getattr(interna, "haylimite", False)
            else None
        )
        nombre_tabla = getattr(interna, "tabla", None) if es_select else None

        if stm.analyze:
            interna.accept(self)
            resultado_interno = self.resultado
            if not es_select:
                # Las sentencias DML no registran nodo de acceso: se
                # agrega aca para que el plan muestre la operacion.
                self.plan.append({
                    "node": _NODO_DML.get(type(interna).__name__, "Result"),
                    "table": getattr(interna, "tabla", "?"),
                    "operation": "dml",
                })
            ejecucion_ms = (time.perf_counter() - self._t0) * 1000.0
            planificacion_ms = 0.0
        else:
            # Solo planificar: se arma el plan sin consumir el generador.
            resultado_interno = None
            ejecucion_ms = 0.0
            self._planificar_sin_ejecutar(interna)
            planificacion_ms = (time.perf_counter() - t_plan) * 1000.0

        if stm.analyze:
            # La planificacion ocurre dentro de la ejecucion (al elegir
            # indice); se cronometra ahi y se descuenta del total, igual
            # que PostgreSQL separa Planning Time de Execution Time.
            planificacion_ms = self._planning_ms
            ejecucion_ms = max(0.0, ejecucion_ms - planificacion_ms)

        stats = self._estadisticas_tabla(nombre_tabla) if nombre_tabla else (0, 0, 0)
        raiz = explain_fmt.construir(
            self.plan, stats, filtro=filtro, limite=limite, total_ms=ejecucion_ms
        )
        lineas = explain_fmt.formatear(
            raiz, stm.analyze, planificacion_ms, ejecucion_ms
        )

        self.resultado = Resultado(
            columnas=["QUERY PLAN"],
            filas=[[linea] for linea in lineas],
            plan=self.plan,
        )
        self.resultado.explain = "\n".join(lineas)
        self.resultado.filas_reales = (
            len(resultado_interno.filas) if resultado_interno is not None else None
        )

    def _planificar_sin_ejecutar(self, interna):
        """
        Decide el plan de una SELECT sin leer filas: abre la tabla,
        consulta que indices hay y deja la entrada correspondiente en
        self.plan. Es lo que hace EXPLAIN sin ANALYZE.
        """
        # EXPLAIN tambien acepta INSERT/UPDATE/DELETE/DDL; ahi no hay
        # plan de acceso que elegir, solo el nodo de la operacion.
        if not isinstance(interna, SelectStmt):
            self.plan.append({
                "node": _NODO_DML.get(type(interna).__name__, "Result"),
                "table": getattr(interna, "tabla", "?"),
                "operation": "dml",
            })
            return

        nombre = getattr(interna, "tabla", None)
        if nombre is None:
            return
        tabla = self._abrir(nombre)
        resolver = self._resolver_columnas([(nombre, tabla)])
        condicion = getattr(interna, "condicion", None)

        self.plan.append({"node": "SELECT", "table": nombre, "operation": "project"})

        _t_plan = time.perf_counter()
        plan_bitmap = self._plan_bitmap(tabla, condicion, resolver)
        self._planning_ms += (time.perf_counter() - _t_plan) * 1000.0
        if plan_bitmap is not None:
            mascara, partes = plan_bitmap
            self.plan.append({
                "node": "BITMAP INDEX SCAN", "table": nombre,
                "operation": "bitmap_index_scan",
                "columns": [tabla.column_names[pos] for _i, pos, _a, _v in partes],
                "matches": mascara.count(),
            })
        else:
            plan_indice = self._plan_indice(tabla, condicion, resolver)
            if plan_indice is None:
                self.plan.append({
                    "node": "SEQUENTIAL SCAN", "table": nombre, "operation": "scan",
                })
            else:
                tipo, (organizacion, _indice), posicion, valores = plan_indice
                self.plan.append({
                    "node": "INDEX SCAN", "table": nombre, "index": organizacion,
                    "access": tipo.lower(), "operation": "index_scan",
                    "column": tabla.column_names[posicion],
                    "bounded": tipo == "RANGO" and all(v is not None for v in valores),
                })

        order_by = getattr(interna, "order_by", None)
        if order_by is not None and self._indice_para_order_by(tabla, order_by) is None:
            self.plan.append({
                "node": "EXTERNAL SORT", "operation": "sort",
                "column": self._etiqueta_orden(order_by),
                "direction": "DESC" if interna.direccion == SortDir.DESC_DIR else "ASC",
            })

        if self._tiene_agregados(interna):
            #self.plan.append({"node": "HASH AGGREGATE", "operation": "aggregate"})
            columnas_agregacion = []
            for item in interna.proyeccion:
                if item.agg != AggFun.NONE_AGG:
                    if item.agg == AggFun.COUNT_AGG:
                        columnas_agregacion.append(tabla.column_names[tabla.key_index])
                    else:
                        columnas_agregacion.append(item.columna.columna)

            self.plan.append({"node": "HASH AGGREGATE", "operation": "aggregate", "column": columnas_agregacion})

    def _texto_filtro(self, interna) -> str:
        """Reconstruye el WHERE tal como lo muestra el plan de PostgreSQL."""
        condicion = getattr(interna, "condicion", None)
        if condicion is None:
            return ""
        return self._texto_condicion(condicion)

    def _texto_condicion(self, cond) -> str:
        if isinstance(cond, WithinExpr):
            return cond.etiqueta()
        if isinstance(cond, OrCond):
            return " OR ".join(self._texto_condicion(c) for c in cond.condiciones)
        if isinstance(cond, AndCond):
            return " AND ".join(self._texto_condicion(c) for c in cond.condiciones)
        if isinstance(cond, BetweenCond):
            return (f"{self._texto_operando(cond.columna)} BETWEEN "
                    f"{self._texto_valor(cond.inferior)} AND "
                    f"{self._texto_valor(cond.superior)}")
        if isinstance(cond, CompareCond):
            return (f"{self._texto_operando(cond.columna)} "
                    f"{Cond.relop_to_char(cond.op)} "
                    f"{self._texto_valor(cond.valor)}")
        return str(cond)

    def _texto_operando(self, nodo) -> str:
        if isinstance(nodo, DistanceExpr):
            return nodo.etiqueta()
        return nodo.columna if not nodo.tabla else f"{nodo.tabla}.{nodo.columna}"

    def _texto_valor(self, nodo) -> str:
        if isinstance(nodo, PolygonValue):
            return nodo.etiqueta()
        if isinstance(nodo, PointValue):
            return f"POINT({nodo.x}, {nodo.y})"
        if isinstance(nodo, StrValue):
            return f"'{nodo.value}'"
        if isinstance(nodo, BoolValue):
            return "TRUE" if nodo.value else "FALSE"
        return str(getattr(nodo, "value", nodo))

    def visit_drop_table_stmt(self, stm):
        """DROP TABLE: borra la metadata del catalogo y el archivo de datos."""
        try:
            self.sm.drop_table(stm.tabla)
        except KeyError:
            raise ExecutionError(f"La tabla '{stm.tabla}' no existe")
        self.plan.append({
            "node": "DROP TABLE", "table": stm.tabla, "operation": "drop",
        })
        self.resultado = Resultado(f"Tabla '{stm.tabla}' eliminada", plan=self.plan)

    def visit_transaction_stmt(self, stm):
        if stm.es_rollback:
            if self.transaction_id is None:
                raise ExecutionError("ROLLBACK requiere una transaccion activa")
            transaction_id = self.transaction_id
            self.sm.current_transaction_id = None
            self.sm.transaction_manager.rollback(
                transaction_id, self._undo_record
            )
            self.sm.lock_manager.release_all(transaction_id)
            self.transaction_id = None
            self.sm.current_transaction_id = None
            self.resultado = Resultado("ROLLBACK")
            return

        if stm.es_begin:
            if self.transaction_id is not None:
                raise ExecutionError("ya existe una transaccion activa")
            transaction = self.sm.transaction_manager.begin()
            self.transaction_id = transaction.transaction_id
            self.sm.current_transaction_id = self.transaction_id
            self.resultado = Resultado("BEGIN TRANSACTION")
            return

        if self.transaction_id is None:
            raise ExecutionError("END TRANSACTION requiere una transaccion activa")
        transaction_id = self.transaction_id
        self.sm.transaction_manager.commit(transaction_id)
        self.sm.lock_manager.release_all(transaction_id)
        self.transaction_id = None
        self.sm.current_transaction_id = None
        self.resultado = Resultado("END TRANSACTION")

    def _abort_active_transaction(self):
        """Aborta y libera locks si una sentencia falla dentro de una tx."""
        if self.transaction_id is None:
            return
        transaction_id = self.transaction_id
        self.sm.current_transaction_id = None
        try:
            self.sm.transaction_manager.abort(transaction_id, self._undo_record)
        finally:
            self.sm.lock_manager.release_all(transaction_id)
            self.transaction_id = None
            self.sm.current_transaction_id = None

    def _mutation_logger(self):
        """Devuelve el hook WAL solo para transacciones explicitas."""
        if self.transaction_id is None:
            return None

        def log_mutation(operation, table, values, rid, new_values=None):
            payload = json.dumps(
                {
                    "values": values,
                    "key": values[table.key_index],
                    "rid": list(rid) if rid is not None else None,
                },
                default=str,
            ).encode("utf-8")
            if operation == "update":
                before = payload
                after = json.dumps(
                    {
                        "values": new_values,
                        "key": new_values[table.key_index],
                        "rid": list(rid) if rid is not None else None,
                    },
                    default=str,
                ).encode("utf-8")
            else:
                before = payload if operation == "delete" else b""
                after = payload if operation == "insert" else b""
            self.sm.transaction_manager.log_update(
                self.transaction_id,
                operation=f"table_{operation}",
                file_name=table.name,
                resource_type="table_record",
                before=before,
                after=after,
            )
            # El hook corre justo antes de la mutacion fisica.
            self.sm.log_manager.force()

        return log_mutation

    def _log_ddl(self, operation, table_name, payload):
        if self.transaction_id is None:
            return
        self.sm.transaction_manager.log_update(
            self.transaction_id,
            operation=f"ddl_{operation}",
            file_name=table_name,
            resource_type="ddl",
            after=json.dumps(payload).encode("utf-8"),
        )
        self.sm.log_manager.force()

    def _undo_record(self, record):
        """Aplica undo logico y deja que Table actualice sus indices."""
        if record.resource_type in ("page", "header", "allocation", "truncate"):
            self.sm._undo_physical_record(record)
            return
        if record.resource_type == "ddl":
            payload = json.loads(record.after.decode("utf-8"))
            if record.operation == "ddl_create_table":
                self.sm.drop_table(record.file_name)
            elif record.operation == "ddl_create_index":
                self.sm.index_manager.drop_index(payload["index_name"])
            return
        payload_bytes = record.before or record.after
        payload = json.loads(payload_bytes.decode("utf-8"))
        table = self._abrir(record.file_name)

        if record.operation == "table_insert":
            table.delete_by_key(payload["key"])
        elif record.operation == "table_delete":
            table.insert(payload["values"])
        elif record.operation == "table_update":
            old_payload = json.loads(record.before.decode("utf-8"))
            new_payload = json.loads(record.after.decode("utf-8"))
            rid = RID(*new_payload["rid"]) if new_payload.get("rid") else None
            restored = table.update(rid, old_payload["values"]) if rid else None
            if restored is None:
                table.delete_by_key(new_payload["key"])
                table.insert(old_payload["values"])
        else:
            raise ExecutionError(
                f"no existe undo fisico para la operacion '{record.operation}'"
            )

    @contextmanager
    def _table_locks(self, table_names, mode):
        """Adquiere locks en orden estable y libera solo en autocommit."""
        transaction_id = self.transaction_id or self.session_id
        explicit = self.transaction_id is not None
        acquired = []
        # _promote_table_locks adquiere un segundo lock sobre el mismo
        # recurso (UREAD -> EXCLUSIVE). Hay que soltar los dos: si solo
        # se libera el primero, el EXCLUSIVE queda tomado y la siguiente
        # sentencia que escriba esa tabla se cuelga hasta el timeout.
        promovidos_previos = self._promovidos
        self._promovidos = []
        try:
            for table_name in sorted(set(table_names)):
                resource = ("table", table_name)
                self.sm.lock_manager.acquire(resource, transaction_id, mode, timeout=5)
                acquired.append(resource)
            yield
        finally:
            promovidos = self._promovidos
            self._promovidos = promovidos_previos
            if not explicit:
                for resource in reversed(promovidos):
                    self.sm.lock_manager.release(resource, transaction_id)
                for resource in reversed(acquired):
                    self.sm.lock_manager.release(resource, transaction_id)

    def _promote_table_locks(self, table_names):
        """Promueve locks UREAD ya adquiridos a EXCLUSIVE en orden estable."""
        transaction_id = self.transaction_id or self.session_id
        for table_name in sorted(set(table_names)):
            resource = ("table", table_name)
            self.sm.lock_manager.acquire(
                resource, transaction_id, LockMode.EXCLUSIVE, timeout=5
            )
            self._promovidos.append(resource)

    def _abrir(self, nombre):
        try:
            return self.sm.open_table(nombre)
        except KeyError:
            raise ExecutionError(f"La tabla '{nombre}' no existe")

    def _resolver_columnas(self, tablas):
        """
        Fabrica un resolver que traduce un ColRef del parser a la
        posicion dentro del registro combinado de la consulta (varias
        tablas si hay JOIN). Las columnas sin calificar se buscan en
        ambas tablas y se rechazan si resultan ambiguas.
        """

        def resolver(colref):
            if colref.tabla:
                acumulado = 0
                for nombre, t in tablas:
                    if nombre == colref.tabla:
                        return acumulado + t.column_index(colref.columna)
                    acumulado += len(t.schema)
                raise ExecutionError(
                    f"La tabla '{colref.tabla}' no participa en la consulta"
                )

            candidatos = []
            acumulado = 0
            for _, t in tablas:
                try:
                    candidatos.append(
                        acumulado + t.column_index(colref.columna)
                    )
                except KeyError:
                    pass
                acumulado += len(t.schema)

            if not candidatos:
                raise KeyError(
                    f"La columna '{colref.columna}' no existe en la consulta"
                )
            if len(candidatos) > 1:
                raise ExecutionError(
                    f"La columna '{colref.columna}' es ambigua; califiquela "
                    f"con la tabla (ej.: {tablas[0][0]}.{colref.columna})"
                )
            return candidatos[0]

        return resolver

    def _tiene_agregados(self, stm):
        if stm.group_by is not None:
            return True
        return any(item.agg != AggFun.NONE_AGG for item in stm.proyeccion)

    def _nombre_item(self, item) -> str:
        """Nombre de columna de un elemento de la proyeccion (funciones
        de agregacion incluidas: count(*), sum(monto), ...)."""
        if item.agg == AggFun.NONE_AGG:
            return item.columna.columna
        if item.agg == AggFun.COUNT_AGG:
            return "count(*)" if item.estrella else f"count({item.columna.columna})"
        nombre = item.agg.name.lower()
        return f"{nombre}({item.columna.columna})"

    def _scan_filtrado(self, tabla, condicion, resolver):
        """Itera (de forma perezosa) las filas de la tabla que cumplen
        la condicion. Si la condicion tiene un predicado que un indice
        B+ pueda responder exactamente y existe ese indice (agrupado o
        no), recorre el indice (punto o rango) en vez de barrer la
        tabla: es el uso estrategico de indices del planificador.

        El indice de bitmap tiene prioridad: combina los varios
        predicados del WHERE en una sola mascara y solo despues visita
        las paginas del heap que esa mascara senala. Si no hay bitmap
        util, se sigue con el plan del B+/hash y, si tampoco, el
        barrido secuencial."""
        # El indice espacial va primero: un predicado de poligono o de
        # radio no lo puede servir ningun otro indice.
        _t_plan = time.perf_counter()
        plan_esp = self._plan_espacial(tabla, condicion, resolver)
        self._planning_ms += (time.perf_counter() - _t_plan) * 1000.0
        if plan_esp is not None:
            tipo, _indice, pos, _args = plan_esp
            entrada = {
                "node": "RTREE INDEX SCAN", "table": tabla.name,
                "operation": "spatial_index_scan",
                "access": tipo.lower(), "index": "rtree",
                "column": tabla.column_names[pos],
            }
            self.plan.append(entrada)
            with self._medir(entrada) as medida:
                for registro in self._candidatos_espaciales(tabla, plan_esp):
                    # El indice acota; la condicion completa se reevalua
                    # igual, por si el AND traia mas predicados.
                    if condicion is None or self._evaluar(condicion, registro, resolver):
                        medida.emitida()
                        yield registro
                    else:
                        medida.descartada()
            return

        _t_plan = time.perf_counter()
        plan_bitmap = self._plan_bitmap(tabla, condicion, resolver)
        self._planning_ms += (time.perf_counter() - _t_plan) * 1000.0
        if plan_bitmap is not None:
            mascara, partes = plan_bitmap
            yield from self._scan_con_bitmap(
                tabla, mascara, partes, condicion, resolver
            )
            return

        _t_plan = time.perf_counter()
        plan = self._plan_indice(tabla, condicion, resolver)
        self._planning_ms += (time.perf_counter() - _t_plan) * 1000.0

        if plan is None:
            entrada = {"node": "SEQUENTIAL SCAN", "table": tabla.name, "operation": "scan"}
            self.plan.append(entrada)
            with self._medir(entrada) as medida:
                for _, registro in tabla.scan():
                    if condicion is None or self._evaluar(condicion, registro, resolver):
                        medida.emitida()
                        yield registro
                    else:
                        medida.descartada()
            return

        tipo, (organizacion, indice), posicion, valores = plan
        self.plan.append({
            "node": "INDEX SCAN",
            "table": tabla.name,
            "index": organizacion,
            "access": tipo.lower(),
            "operation": "index_scan",
            "column": tabla.column_names[posicion],
            # un rango cerrado (BETWEEN) filtra mucho mas que uno abierto
            "bounded": tipo == "RANGO" and all(v is not None for v in valores),
        })
        with self._medir(self.plan[-1]) as medida:
            for registro in self._candidatos_con_indice(tabla, plan):
                if condicion is None or self._evaluar(condicion, registro, resolver):
                    medida.emitida()
                    yield registro
                else:
                    medida.descartada()

    # ---------------- indice de bitmap ----------------

    def _plan_bitmap(self, tabla, condicion, resolver):
        """Resuelve el WHERE con los indices de bitmap disponibles.

        A diferencia de `_plan_indice`, aqui no alcanza con un unico
        predicado: lo interesante del bitmap es que las mascaras de
        todos los predicados se combinan entre si antes de tocar el
        heap, asi que se devuelve la combinacion completa o None (si
        ningun bitmap sirve, el planificador usa el B+/hash de siempre).

        Devuelve una lista de tuplas `(indice, posicion, acceso, valores)`.
        """
        if condicion is None:
            return None

        combinado = self._combinar_bitmaps(tabla, condicion, resolver)
        if combinado is None:
            return None
        mascara, partes = combinado
        return mascara, partes

    @staticmethod
    def _predicados_de(partes):
        """Aplana los `(indice, posicion, acceso, valores)` de un arbol de
        condiciones, que pueden venir anidados (un AND dentro de un OR)."""
        return [item for _mascara, usados in partes for item in usados]

    def _combinar_bitmaps(self, tabla, condicion, resolver):
        """Resuelve el WHERE entero con mascaras de bitmap.

        Devuelve `(mascara, predicados)`, donde `predicados` son los
        `(indice, posicion, acceso, valores)` que se usaron, o None si
        alguna parte del WHERE no se puede resolver con bitmap (ahi mandan
        el B+/hash o el barrido).

        Cuidado de no confundir dos cosas: una mascara VACIA es una
        respuesta valida (no hay filas que traer, y se contesta sin tocar
        el heap), mientras que None significa que el bitmap no sabe
        acortar ese predicado y por lo tanto no sirve para el WHERE entero.
        """
        if isinstance(condicion, (AndCond, OrCond)):
            es_and = isinstance(condicion, AndCond)
            partes = []
            for hijo in condicion.condiciones:
                combinado = self._combinar_bitmaps(tabla, hijo, resolver)
                if combinado is None:
                    return None
                partes.append(combinado)

            mascaras = [mascara for mascara, _usados in partes]
            total = mascaras[0]
            for bit in mascaras[1:]:
                # AND: quedan las filas que estan en todas; OR: en alguna.
                total = total.intersect(bit) if es_and else total.union(bit)
            return (total, self._predicados_de(partes))

        if isinstance(condicion, WithinExpr):
            return None   # predicado espacial: no lo sirve un bitmap
        if not isinstance(getattr(condicion, "columna", None), ColRef):
            return None   # p.ej. distancia(...) < v: no lo sirve un bitmap
        try:
            pos = resolver(condicion.columna)
        except (ExecutionError, KeyError):
            return None
        indice = self._indice_bitmap(tabla, pos)
        if indice is None:
            return None

        if isinstance(condicion, BetweenCond):
            valores = (
                self._valor(condicion.inferior),
                self._valor(condicion.superior),
            )
            acceso = "RANGO"
        elif isinstance(condicion, CompareCond):
            valor = self._valor(condicion.valor)
            if condicion.op == RelOp.EQ_OP:
                valores = (valor,)
                acceso = "EQ"
            elif (
                self._es_numerico(tabla, pos)
                and condicion.op in (RelOp.LT_OP, RelOp.LE_OP)
            ):
                valores = (None, valor)
                acceso = "RANGO"
            elif (
                self._es_numerico(tabla, pos)
                and condicion.op in (RelOp.GT_OP, RelOp.GE_OP)
            ):
                valores = (valor, None)
                acceso = "RANGO"
            else:
                # != y las comparaciones sin indice no se pueden resolver
                # con una mascara de un solo valor.
                return None
        else:
            return None

        mascara = (
            indice.search(valores[0])
            if acceso == "EQ"
            else indice.search_range(*valores)
        )
        if mascara is None:
            # Rango con demasiados valores distintos: armarlo en memoria
            # saldria mas caro que barrer, asi que se descarta.
            return None

        return (mascara, [(indice, pos, acceso, valores)])

    def _indice_bitmap(self, tabla, pos):
        """Primer indice de bitmap sobre la columna `pos`, o None.

        Conviven con los demas indices de la columna: el bitmap elige su
        propia clave y no estorba al B+ ni al hash."""
        for idx in tabla.secondary_indexes.get(pos, []):
            if isinstance(idx, BitmapIndex):
                return idx
        return None

    def _scan_con_bitmap(self, tabla, mascara, partes, condicion, resolver):
        """Rinde las filas que la mascara del plan senala.

        Los RIDs salen ordenados por pagina y de ahi por slot, asi que el
        heap se recorre de forma secuencial y cada pagina se visita una sola
        vez. El WHERE se vuelve a evaluar sobre cada fila traida, asi que el
        resultado no depende de que la mascara sea exacta: los filtros que
        el bitmap no acoto (un NOT, un predicado sin indice) se resuelven
        ahi."""
        self.plan.append({
            "node": "BITMAP INDEX SCAN",
            "table": tabla.name,
            "operation": "bitmap_index_scan",
            "columns": [tabla.column_names[pos] for _i, pos, _a, _v in partes],
            "access": "+".join(dict.fromkeys(a for _i, _p, a, _v in partes)),
            "matches": mascara.count(),
            "heap_pages": mascara.page_count(),
        })

        # Una mascara vacia ya dice que no hay filas que traer: ni se toca
        # el heap. El nodo del plan queda igual, para que se vea que la
        # consulta se resolvio con el indice y no barriendo la tabla.
        for rid in mascara.rids():
            registro = tabla.data_file.fetch(rid)
            if registro is None:
                continue
            registro = list(registro)
            if condicion is None or self._evaluar(condicion, registro, resolver):
                yield registro

    def _plan_indice(self, tabla, condicion, resolver):
        """Busca un predicado del WHERE que un indice B+ pueda servir.

        Solo se busca en contexto conjuntivo: un predicado dentro de un
        OR no vale para todas las filas del resultado, asi que usar el
        indice perderia filas. Dentro de un AND un predicado si vale:
        el indice da un conjunto acotado que luego el WHERE afina.

        Devuelve ('EQ'|'RANGO', organizacion+indice, posicion, valores)
        o None si no hay postulante.
        """
        if condicion is None or isinstance(condicion, OrCond):
            return None

        def buscar(cond):
            if isinstance(cond, AndCond):
                for hijo in cond.condiciones:
                    plan = buscar(hijo)
                    if plan is not None:
                        return plan
                return None
            if isinstance(cond, OrCond):
                return None

            # ---- PUNTO DE ENGANCHE DEL INDICE ESPACIAL (R-Tree) ----
            # Hoy un predicado espacial cae a barrido secuencial: la
            # distancia se calcula fila por fila. Cuando exista el R-Tree,
            # aca va la rama que lo use, devolviendo un plan del estilo
            #     ("ESPACIAL", (org, indice), pos, (punto, radio))
            # y _candidatos_con_indice debe saber recorrerlo. Lo mismo
            # para el KNN: hoy ORDER BY distancia(...) ordena todo
            # (ver _clave_orden); con el arbol seria una busqueda por
            # cercania sin leer toda la tabla.
            if isinstance(cond, WithinExpr):
                return None
            if isinstance(getattr(cond, "columna", None), DistanceExpr):
                return None

            if not isinstance(getattr(cond, "columna", None), ColRef):
                return None   # cualquier otra expresion tampoco la sirve un B+
            try:
                pos = resolver(cond.columna)
            except (ExecutionError, KeyError):
                return None
            indice = self._indice_para(tabla, pos)
            if indice is None:
                return None
            org, _ = indice
            es_hash = org == "hash"

            if isinstance(cond, BetweenCond):
                if es_hash:
                    return None  # el hash no responde rangos
                return ("RANGO", indice, pos,
                        (self._valor(cond.inferior), self._valor(cond.superior)))
            if not isinstance(cond, CompareCond):
                return None

            valor = self._valor(cond.valor)
            if cond.op == RelOp.EQ_OP:
                return ("EQ", indice, pos, (valor,))
            if es_hash:
                return None  # el hash solo sirve para búsqueda por punto
            if not self._es_numerico(tabla, pos):
                return None
            if cond.op == RelOp.GE_OP:
                return ("RANGO", indice, pos, (valor, None))
            if cond.op == RelOp.GT_OP:
                return ("RANGO", indice, pos, (valor, None))
            if cond.op == RelOp.LE_OP:
                return ("RANGO", indice, pos, (None, valor))
            if cond.op == RelOp.LT_OP:
                return ("RANGO", indice, pos, (None, valor))
            return None

        return buscar(condicion)

    def _es_numerico(self, tabla, pos) -> bool:
        return tabla.schema[pos] in ("integer", "float", "date")

    def _plan_knn(self, tabla, stm, resolver, reverse):
        """
        Decide si un ORDER BY distancia(...) LIMIT k se puede resolver
        como un k-NN sobre el R-Tree.

        El arbol devuelve los k mas cercanos sin leer toda la tabla, pero
        solo sirve si se piden los MAS cercanos (ASC) y hay un LIMIT: con
        DESC harian falta los mas lejanos, que es justo lo que un R-Tree
        no sabe podar.

        Devuelve (indice, posicion, punto, metrica, k) o None.
        """
        order_by = stm.order_by
        if not isinstance(order_by, DistanceExpr) or reverse or not stm.haylimite:
            return None

        punto, columna = self._lados_distancia(order_by)
        if punto is None:
            return None
        try:
            pos = resolver(columna)
        except (ExecutionError, KeyError):
            return None

        indice = self._indice_rtree(tabla, pos)
        if indice is None:
            return None

        metrica = ("haversine" if order_by.metrica == Metrica.GEODESICA
                   else "euclidean")
        return (indice, pos, punto, metrica, stm.limite)

    def _filas_knn(self, tabla, stm, resolver, plan):
        """
        Recorre los k vecinos mas cercanos que devuelve el R-Tree.

        Si la consulta ademas trae WHERE, se pide un k mas grande y se
        filtra: de otro modo un vecino cercano que no cumple el filtro
        dejaria el resultado corto. El multiplicador se amplia hasta
        agotar la tabla antes de devolver menos filas de las pedidas.
        """
        indice, pos, (px, py), metrica, k = plan
        entrada = {
            "node": "RTREE KNN", "table": tabla.name, "operation": "knn",
            "index": "rtree", "column": tabla.column_names[pos],
            "k": k, "metric": metrica,
        }
        self.plan.append(entrada)

        centro = GeoPoint(px, py)
        with self._medir(entrada) as medida:
            if stm.condicion is None:
                vecinos = indice.knn(centro, k, metrica)
                for _punto, rid in vecinos:
                    registro = tabla.get(rid)
                    if registro is not None:
                        medida.emitida()
                        yield registro
                return

            # Con filtro: se agranda el k hasta juntar las k filas que lo
            # cumplen, o hasta que el arbol no tenga mas que ofrecer.
            emitidas = 0
            pedidos = k
            vistos = set()
            while emitidas < k:
                vecinos = indice.knn(centro, pedidos, metrica)
                for _punto, rid in vecinos:
                    if rid in vistos:
                        continue
                    vistos.add(rid)
                    registro = tabla.get(rid)
                    if registro is None:
                        continue
                    if self._evaluar(stm.condicion, registro, resolver):
                        medida.emitida()
                        emitidas += 1
                        yield registro
                        if emitidas >= k:
                            return
                    else:
                        medida.descartada()
                if len(vecinos) < pedidos:
                    return          # el arbol ya devolvio todo lo que tiene
                pedidos *= 2

    def _indice_rtree(self, tabla, pos):
        """Devuelve el R-Tree sobre la columna pos, o None."""
        for idx in tabla.secondary_indexes.get(pos, []):
            if isinstance(idx, RTreeSecondaryIndex):
                return idx
        return None

    def _plan_espacial(self, tabla, condicion, resolver):
        """
        Decide si un predicado espacial se puede resolver con el R-Tree.

        Cubre los dos casos en que el arbol evita leer toda la tabla:
          dentro_de(col, POLYGON(...))        -> polygon_search
          distancia(col, POINT(...)) < radio  -> radius_search

        Devuelve (tipo, indice, posicion, argumentos) o None. Igual que
        con los demas indices, solo se mira en contexto conjuntivo: dentro
        de un OR el indice dejaria fuera las filas de la otra rama.
        """
        if condicion is None or isinstance(condicion, OrCond):
            return None

        candidatos = (condicion.condiciones
                      if isinstance(condicion, AndCond) else [condicion])

        for cond in candidatos:
            # dentro_de(col, POLYGON(...))
            if isinstance(cond, WithinExpr):
                try:
                    pos = resolver(cond.columna)
                except (ExecutionError, KeyError):
                    continue
                indice = self._indice_rtree(tabla, pos)
                if indice is not None:
                    return ("POLIGONO", indice, pos, cond.poligono.value)
                continue

            # distancia(col, POINT(...)) < radio   (o <=)
            if (isinstance(cond, CompareCond)
                    and isinstance(cond.columna, DistanceExpr)
                    and cond.op in (RelOp.LT_OP, RelOp.LE_OP)):
                expr = cond.columna
                punto, columna = self._lados_distancia(expr)
                if punto is None:
                    continue
                try:
                    pos = resolver(columna)
                except (ExecutionError, KeyError):
                    continue
                indice = self._indice_rtree(tabla, pos)
                if indice is None:
                    continue
                radio = self._valor(cond.valor)
                metrica = ("haversine" if expr.metrica == Metrica.GEODESICA
                           else "euclidean")
                return ("RADIO", indice, pos, (punto, radio, metrica))

        return None

    @staticmethod
    def _lados_distancia(expr):
        """
        Separa distancia(a, b) en (punto literal, columna). Si los dos
        lados son columnas no hay nada que el indice pueda acotar, porque
        el centro de la busqueda cambia fila por fila.
        """
        if isinstance(expr.derecha, PointValue) and isinstance(expr.izquierda, ColRef):
            return (expr.derecha.value, expr.izquierda)
        if isinstance(expr.izquierda, PointValue) and isinstance(expr.derecha, ColRef):
            return (expr.izquierda.value, expr.derecha)
        return (None, None)

    def _candidatos_espaciales(self, tabla, plan):
        """Recorre los RID que devuelve el R-Tree para el plan dado."""
        tipo, indice, _pos, args = plan
        if tipo == "POLIGONO":
            vertices = [GeoPoint(x, y) for x, y in args]
            encontrados = indice.polygon_search(vertices)
        else:
            (px, py), radio, metrica = args
            encontrados = indice.radius_search(GeoPoint(px, py), radio, metrica)

        for _punto, rid in encontrados:
            registro = tabla.get(rid)
            if registro is not None:
                yield registro

    def _indice_para(self, tabla, pos):
        """
        Devuelve ('clustered'|'unclustered'|'hash', indice) para la
        columna pos si hay un indice sobre ella, o None.

        Si la columna tiene varios indices secundarios se prefiere el B+
        (responde busquedas por punto Y por rango); el hash se usa como
        alternativa cuando es el unico indice postulante. Los de bitmap se
        dejan fuera: los maneja `_plan_bitmap`, que sabe combinar sus
        mascaras entre si. El R-Tree tambien queda fuera: indexa puntos,
        no claves escalares, asi que no puede responder `col = v` ni un
        rango; lo usa `_plan_espacial`.
        """
        if tabla.clustered_index is not None and pos == tabla.key_index:
            return ("clustered", tabla.clustered_index)
        secundarios = [idx for idx in tabla.secondary_indexes.get(pos, [])
                       if not isinstance(idx, (BitmapIndex, RTreeSecondaryIndex))]
        for idx in secundarios:
            if not isinstance(idx, HashIndex):
                return ("unclustered", idx)
        if secundarios:
            return ("hash", secundarios[0])
        return None

    def _candidatos_con_indice(self, tabla, plan):
        """
        Rinde los registros que el indice B+ postula para el plan:
        búsqueda por punto (EQ) o rango (RANGO via range_search).
        """
        tipo, (org, indice), pos, valores = plan

        if org == "hash":
            # El hash guarda pares (clave, RID): se trae la fila del heap
            # por su RID, igual que hace el B+ no agrupado.
            for kv in indice.search(valores[0]):
                registro = tabla.data_file.fetch(kv.rid)
                if registro is not None:
                    yield list(registro)
            return

        if tipo == "EQ":
            refs = indice.search(valores[0])
            if refs is None:
                return
            if not isinstance(refs, list):
                refs = [refs]
            for ref in refs:
                registro = indice._fetch_record(ref)
                if registro is not None:
                    yield list(registro)
            return

        inicio = valores[0] if valores[0] is not None else float("-inf")
        fin = valores[1] if valores[1] is not None else float("inf")
        for _, ref in indice.range_search(inicio, fin):
            registro = indice._fetch_record(ref)
            if registro is not None:
                yield list(registro)

    def _select_ordenado(self, tabla, condicion, resolver, colref, reverse):
        """ORDER BY con External Sorting (k-way merge).

        Aplica el WHERE (con su plan de indice) y genera pares
        (valor_de_la_columna, registro_serializado) hacia el
        ExternalSorter, que ordena sin haber cargado nunca todas las
        filas en memoria (un run se vuelca apenas supera el budget).
        """
        serializador = tabla.data_file.serializer
        clave = self._clave_orden(colref, resolver)

        def items():
            for registro in self._scan_filtrado(tabla, condicion, resolver):
                yield clave(registro), serializador.encode(registro)

        sorter = ExternalSorter(reverse=reverse, budget=EXTERNAL_SORT_BUDGET)
        try:
            for _, registro_bytes in sorter.sort(items()):
                yield serializador.decode(registro_bytes)
        finally:
            sorter.cleanup()

    # ---------------- instrumentacion para EXPLAIN ANALYZE ----------------

    @contextmanager
    def _medir(self, entrada):
        """
        Acumula en la entrada del plan las filas emitidas, las descartadas
        por el filtro y la ventana de tiempo del nodo.

        Como los nodos son generadores perezosos, el cronometro arranca
        cuando se pide la primera fila y se detiene cuando se agota el
        generador, igual que el "actual time=inicio..fin" de PostgreSQL.
        """
        medida = _MedicionNodo(entrada)
        entrada["actual_start_ms"] = (time.perf_counter() - self._t0) * 1000.0
        try:
            yield medida
        finally:
            entrada["actual_rows"] = medida.filas
            entrada["rows_removed"] = medida.descartadas
            entrada["actual_first_ms"] = (
                (medida.t_primera - self._t0) * 1000.0
                if medida.t_primera is not None
                else entrada["actual_start_ms"]
            )
            entrada["actual_end_ms"] = (time.perf_counter() - self._t0) * 1000.0
            entrada["loops"] = 1

    def _estadisticas_tabla(self, nombre):
        """
        (paginas, filas estimadas, ancho de fila) de una tabla.

        Son las estadisticas que alimentan el costo estimado del plan,
        igual que pg_class.relpages / reltuples en PostgreSQL. Se deducen
        del tamano del archivo para no tener que recorrerlo.
        """
        try:
            tabla = self.sm.open_table(nombre)
        except Exception:
            return (0, 0, 0)

        serializador = tabla.data_file.serializer
        ancho = getattr(serializador, "record_size", None)
        if not ancho:
            ancho = max(1, sum(_ancho_estimado(tipo) for tipo in tabla.schema))

        try:
            tamano = os.path.getsize(tabla.filename)
        except OSError:
            tamano = 0
        paginas = max(1, tamano // PAGE_SIZE_ESTIMADO)
        por_pagina = max(1, PAGE_SIZE_ESTIMADO // max(1, ancho + 17))
        return (paginas, paginas * por_pagina, ancho)

    def _clave_orden(self, order_by, resolver):
        """
        Devuelve la funcion que extrae el valor de orden de un registro.

        Para un ColRef es simplemente su posicion; para distancia(a, b)
        hay que calcularla fila por fila.
        """
        if isinstance(order_by, DistanceExpr):
            return lambda registro: self._distancia(order_by, registro, resolver)
        pos = resolver(order_by)
        return lambda registro: registro[pos]

    def _etiqueta_orden(self, order_by) -> str:
        """Nombre del criterio de orden, para mostrarlo en el plan."""
        if isinstance(order_by, DistanceExpr):
            return order_by.etiqueta()
        return order_by.columna

    def _ordenar_externo(self, filas, resolver, colref, reverse, serializador):
        """Ordena un stream de registros combinados (p.ej. de un JOIN)
        con el sorter externo, usando `serializador` para persistirlos."""
        clave = self._clave_orden(colref, resolver)

        def items():
            for registro in filas:
                yield clave(registro), serializador.encode(registro)

        sorter = ExternalSorter(reverse=reverse, budget=EXTERNAL_SORT_BUDGET)
        try:
            for _, registro_bytes in sorter.sort(items()):
                yield serializador.decode(registro_bytes)
        finally:
            sorter.cleanup()

    def _filas_join(self, izquierda, derecha, stm, resolver, serializador):
        """
        JOIN con External Hashing (Grace Hash Join).
        Particiona ambas tablas por el hash de la columna del ON
        (al fin de guardar solo un item en RAM a la vez) y une las
        particiones de a pares: build sobre la izquierda, probe sobre la
        derecha. Rinde el registro combinado (fila izquierda + fila
        derecha) que cumple el WHERE.
        """
        col_izq, col_der = stm.join.izquierda, stm.join.derecha
        if col_izq.tabla and col_izq.tabla != izquierda.name:
            raise ExecutionError(
                "el lado izquierdo del ON debe referenciar a la tabla "
                f"'{izquierda.name}'"
            )
        if col_der.tabla and col_der.tabla != derecha.name:
            raise ExecutionError(
                "el lado derecho del ON debe referenciar a la tabla "
                f"'{derecha.name}'"
            )

        pos_izq = izquierda.column_index(col_izq.columna)
        pos_der = derecha.column_index(col_der.columna)
        ser_izq = izquierda.data_file.serializer
        ser_der = derecha.data_file.serializer

        def items_izquierda():
            for _, registro in izquierda.scan():
                yield registro[pos_izq], ser_izq.encode(registro)

        def items_derecha():
            for _, registro in derecha.scan():
                yield registro[pos_der], ser_der.encode(registro)

        hasher = ExternalHasher(num_buckets=EXTERNAL_HASH_BUCKETS)
        try:
            for _, izq_bytes, der_bytes in hasher.hash_join(
                items_izquierda(), items_derecha()
            ):
                registro = ser_izq.decode(izq_bytes) + ser_der.decode(der_bytes)
                if stm.condicion is None or self._evaluar(
                    stm.condicion, registro, resolver
                ):
                    yield registro
        finally:
            hasher.cleanup()

    def _proyeccion_agregada(self, stm, filas, resolver, serializador):
        """
        GROUP BY + COUNT/SUM/AVG/MIN/MAX con External Hashing.
        Las filas se particionan a disco por el hash de la clave de
        grupo (o una sola clave fija si no hay GROUP BY: agregacion
        global) y, por cada particion, los grupos se acumulan en un dict
        en memoria. Un resultado por grupo. Devuelve (nombres, filas).
        """
        pos_grupo = resolver(stm.group_by) if stm.group_by is not None else None
        posiciones_agg = []

        for item in stm.proyeccion:
            if item.agg == AggFun.NONE_AGG:
                if pos_grupo is None:
                    raise ExecutionError(
                        f"'{item.columna.columna}' no es agregacion y no puede "
                        "usarse sin GROUP BY"
                    )
                if resolver(item.columna) != pos_grupo:
                    raise ExecutionError(
                        f"'{item.columna.columna}' debe aparecer en el GROUP BY"
                    )
            elif not item.estrella:
                posiciones_agg.append(resolver(item.columna))

        def items():
            for registro in filas:
                clave = 0 if pos_grupo is None else registro[pos_grupo]
                yield clave, serializador.encode(registro)

        def acumular(acc, value_bytes):
            registro = serializador.decode(value_bytes)
            acc["__n"] = acc.get("__n", 0) + 1
            for pos in posiciones_agg:
                stats = acc.get(pos)
                if stats is None:
                    stats = acc[pos] = [0, 0, float("inf"), float("-inf")]
                valor = registro[pos]
                stats[0] += 1
                stats[1] += valor
                if valor < stats[2]:
                    stats[2] = valor
                if valor > stats[3]:
                    stats[3] = valor
            return acc

        hasher = ExternalHasher(num_buckets=EXTERNAL_HASH_BUCKETS)
        nombres = [self._nombre_item(i) for i in stm.proyeccion]
        filas_salida = []

        try:
            for clave, acc in hasher.group_by(items(), dict, acumular):
                fila = []
                for item in stm.proyeccion:
                    if item.agg == AggFun.NONE_AGG:
                        fila.append(clave)
                    elif item.agg == AggFun.COUNT_AGG and item.estrella:
                        fila.append(acc["__n"])
                    else:
                        stats = acc[resolver(item.columna)]
                        if item.agg == AggFun.COUNT_AGG:
                            fila.append(stats[0])
                        elif item.agg == AggFun.SUM_AGG:
                            fila.append(stats[1])
                        elif item.agg == AggFun.AVG_AGG:
                            fila.append(stats[1] / stats[0])
                        elif item.agg == AggFun.MIN_AGG:
                            fila.append(stats[2])
                        elif item.agg == AggFun.MAX_AGG:
                            fila.append(stats[3])
                filas_salida.append(fila)
        finally:
            hasher.cleanup()

        return nombres, filas_salida

    def _ordenar_resultado(self, filas, nombres, columna, reverse):
        """
        ORDER BY sobre el resultado de una agregacion: las filas ya
        estan materializadas (una por grupo), asi que ordenar en RAM el
        resultado es acotado.
        """
        try:
            pos = nombres.index(columna)
        except ValueError:
            raise ExecutionError(
                f"ORDER BY '{columna}' no es una columna del resultado"
            )
        return sorted(filas, key=lambda fila: fila[pos], reverse=reverse)

    def _valor(self, nodo):
        """Extrae el valor Python de un nodo Value del parser."""
        if isinstance(nodo, (PointValue, PolygonValue)):
            return nodo.value
        if isinstance(nodo, (IntValue, FloatValue, StrValue, BoolValue)):
            return nodo.value
        raise ExecutionError(f"Valor no soportado: {nodo}")

    def _punto(self, operando, registro, resolver):
        """
        Obtiene el par (x, y) de un operando espacial: un POINT literal
        o una columna de tipo point del registro.

        El orden es el de PostGIS: x = longitud, y = latitud.
        """
        if isinstance(operando, PointValue):
            return operando.value

        valor = registro[resolver(operando)]
        if not isinstance(valor, (tuple, list)) or len(valor) != 2:
            raise ExecutionError(
                f"La columna '{operando.columna}' no es de tipo POINT "
                f"(contiene {valor!r})"
            )
        return valor

    def _distancia(self, expr, registro, resolver) -> float:
        """
        Distancia entre los dos operandos, con la metrica que pidio la
        consulta. Las coordenadas van en orden PostGIS (longitud, latitud).

        distancia / distancia_euclidiana
            Distancia plana entre las coordenadas, en GRADOS. Es lo que
            hace ST_Distance de PostGIS sobre una geometria sin SRID
            geografico: rapida, pero un grado de longitud no mide lo
            mismo cerca del ecuador que cerca de los polos.

        distancia_geodesica
            Haversine sobre una esfera, en METROS. Equivale a
            ST_DistanceSphere de PostGIS. Mas cara de calcular, pero es
            la que sirve para un radio expresado en metros.
        """
        x1, y1 = self._punto(expr.izquierda, registro, resolver)
        x2, y2 = self._punto(expr.derecha, registro, resolver)

        if expr.metrica == Metrica.GEODESICA:
            # Solo la metrica geodesica interpreta las coordenadas como
            # grados sobre la esfera, asi que es la unica que puede exigir
            # que esten en rango. La euclidiana trata el punto como un par
            # cualquiera del plano y no valida nada.
            self._validar_grados(x1, y1)
            self._validar_grados(x2, y2)
            return haversine(GeoPoint(x1, y1), GeoPoint(x2, y2))
        return euclidean(GeoPoint(x1, y1), GeoPoint(x2, y2))

    @staticmethod
    def _validar_grados(lon, lat):
        if not -180.0 <= lon <= 180.0:
            raise ExecutionError(
                f"La longitud {lon} esta fuera de rango: debe estar entre "
                f"-180 y 180 (el orden de POINT es (longitud, latitud))"
            )
        if not -90.0 <= lat <= 90.0:
            raise ExecutionError(
                f"La latitud {lat} esta fuera de rango: debe estar entre "
                f"-90 y 90 (el orden de POINT es (longitud, latitud))"
            )

    def _dentro_de(self, expr, registro, resolver) -> bool:
        """
        Pertenencia de un punto a un poligono, por ray casting.
        Equivale a ST_Contains(poligono, punto) de PostGIS.
        """
        x, y = self._punto(expr.columna, registro, resolver)
        vertices = [GeoPoint(vx, vy) for vx, vy in expr.poligono.value]
        return point_in_polygon(GeoPoint(x, y), vertices)

    def _operando(self, nodo, registro, resolver):
        """Valor de un lado del predicado: distancia(...) o una columna."""
        if isinstance(nodo, DistanceExpr):
            return self._distancia(nodo, registro, resolver)
        return registro[resolver(nodo)]

    def _evaluar(self, cond, registro, resolver) -> bool:
        """
        Evalua una condicion del WHERE sobre un registro ya leido.
        """
        if isinstance(cond, OrCond):
            return any(self._evaluar(c, registro, resolver) for c in cond.condiciones)
        if isinstance(cond, AndCond):
            return all(self._evaluar(c, registro, resolver) for c in cond.condiciones)

        if isinstance(cond, WithinExpr):
            return self._dentro_de(cond, registro, resolver)

        if isinstance(cond, CompareCond):
            izq = self._operando(cond.columna, registro, resolver)
            der = self._valor(cond.valor)
            if cond.op == RelOp.EQ_OP:
                return izq == der
            if cond.op == RelOp.NEQ_OP:
                return izq != der
            if cond.op == RelOp.LT_OP:
                return izq < der
            if cond.op == RelOp.LE_OP:
                return izq <= der
            if cond.op == RelOp.GT_OP:
                return izq > der
            if cond.op == RelOp.GE_OP:
                return izq >= der

        if isinstance(cond, BetweenCond):
            val = registro[resolver(cond.columna)]
            return self._valor(cond.inferior) <= val <= self._valor(cond.superior)

        raise ExecutionError(f"Condicion no soportada: {cond}")

    def _indice_para_order_by(self, tabla, colref):
        """
        Busca un índice B+ que permita recorrer la columna del ORDER BY de manera ordenada
        (y no índices hash, ya que estos no mantienen el orden, ni de bitmap,
        que no tienen orden de clave).
        Retorna ("clustered" / "unclustered", indice, posicion) o None.
        """
        if not isinstance(colref, ColRef):
            return None   # distancia(...) se calcula, ningun indice la ordena
        try:
            pos = tabla.column_index(colref.columna)
        except KeyError:
            return None

        if tabla.clustered_index is not None and pos == tabla.key_index:
            return ("clustered", tabla.clustered_index, pos)

        for indice in tabla.secondary_indexes.get(pos, []):
            if not isinstance(indice, (HashIndex, BitmapIndex)):
                return ("unclustered", indice, pos)
        return None

    def _scan_ordenado_por_indice(self, tabla, condicion, resolver, colref, reverse=False):
        """
        Recorre un indice B+ en el orden de sus claves y aplica el WHERE.
        El recorrido descendente es tan legitimo como el ascendente: se
        desciende por los hijos de derecha a izquierda y dentro de cada hoja
        se leen las entradas al reves, así que no hacen falta enlaces hacia
        atras en las hojas.
        """
        info = self._indice_para_order_by(tabla, colref)

        if info is None:
            raise ExecutionError(f"No existe un indice B+ sobre '{colref.columna}'")

        organizacion, indice, posicion = info

        self.plan.append({"node": "INDEX SCAN", "table": tabla.name, "index": organizacion, "operation": "index_scan",
            "column": tabla.column_names[posicion], "direction": "DESC" if reverse else "ASC"}) #tal vez "INDEX ORDERED SCAN" o similar en node y operation
        for _, ref in indice.iter_ordered(reverse=reverse):
            registro = indice._fetch_record(ref)

            if registro is None:
                continue

            registro = list(registro)

            if condicion is None or self._evaluar(
                condicion, registro, resolver
            ):
                yield registro

    def visit_int_value(self, v): pass
    def visit_float_value(self, v): pass
    def visit_str_value(self, v): pass
    def visit_bool_value(self, v): pass
    def visit_col_ref(self, c): pass
    def visit_or_cond(self, c): pass
    def visit_and_cond(self, c): pass
    def visit_compare_cond(self, c): pass
    def visit_between_cond(self, c): pass
    def visit_select_item(self, item): pass
    def visit_join_clause(self, j): pass
    def visit_column_dec(self, cd): pass
    def visit_programa(self, program): pass
