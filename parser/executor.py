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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "parser"))

from itertools import islice
from ast_sql import (AggFun, AndCond, BetweenCond, BoolValue, CompareCond,
                     DataType, FileOrg, FloatValue, IndexKind, IntValue,
                     OrCond, RelOp, SortDir, StrValue)
from visitor import Visitor
from storage.storage_manager import StorageManager
from indexes.external_sort import ExternalSorter
from indexes.external_hash import ExternalHasher
from indexes.extendible_hash import HashIndex
from storage.formats.serializers.variable_length_serializer import VariableLengthRecordSerializer

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

    def __init__(self, mensaje="", columnas=None, filas=None):
        self.mensaje = mensaje
        self.columnas = columnas or []
        self.filas = filas or []

    def __str__(self):
        if not self.columnas:
            return self.mensaje
        lineas = [" | ".join(self.columnas)]
        lineas.append("-" * len(lineas[0]))
        for fila in self.filas:
            lineas.append(" | ".join(str(v) for v in fila))
        lineas.append(f"({len(self.filas)} fila(s))")
        return "\n".join(lineas)


class ExecuteVisitor(Visitor):
    """Ejecuta un AST del parser contra el StorageManager."""

    def __init__(self, storage_manager: StorageManager):
        self.sm = storage_manager
        self.resultado = None

    def ejecutar(self, programa) -> list:
        salidas = []
        for stmt in programa.slist:
            stmt.accept(self)
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
        tabla = self._abrir(stm.tabla)
        valores = [self._valor(v) for v in stm.valores]

        if len(valores) != len(tabla.schema):
            raise ExecutionError(
                f"La tabla '{stm.tabla}' espera {len(tabla.schema)} valores, "
                f"se dieron {len(valores)}"
            )

        tabla.insert(valores)
        self.resultado = Resultado("1 fila insertada")

    def visit_select_stmt(self, stm):
        tabla = self._abrir(stm.tabla)
        derecha = None

        if stm.join is not None:
            derecha = self._abrir(stm.join.tabla)
            tablas = [(stm.tabla, tabla), (stm.join.tabla, derecha)]
            resolver = self._resolver_columnas(tablas)
            serializador = VariableLengthRecordSerializer(
                list(tabla.schema) + list(derecha.schema)
            )
            filas = self._filas_join(tabla, derecha, stm, resolver, serializador)
            if not self._tiene_agregados(stm) and stm.order_by is not None:
                filas = self._ordenar_externo(
                    filas, resolver, stm.order_by,
                    stm.direccion == SortDir.DESC_DIR, serializador,
                )
        else:
            tablas = [(stm.tabla, tabla)]
            resolver = self._resolver_columnas(tablas)
            serializador = tabla.data_file.serializer
            if not self._tiene_agregados(stm) and stm.order_by is not None:
                filas = self._select_ordenado(
                    tabla, stm.condicion, resolver, stm.order_by,
                    stm.direccion == SortDir.DESC_DIR,
                )
            else:
                filas = self._scan_filtrado(tabla, stm.condicion, resolver)

        if self._tiene_agregados(stm):
            nombres, filas = self._proyeccion_agregada(
                stm, filas, resolver, serializador
            )
            if stm.order_by is not None:
                filas = self._ordenar_resultado(
                    filas, nombres, stm.order_by.columna,
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

        self.resultado = Resultado(columnas=nombres, filas=filas)

    def visit_delete_stmt(self, stm):
        tabla = self._abrir(stm.tabla)
        resolver = self._resolver_columnas([(stm.tabla, tabla)])

        borrados = 0
        for rid, registro in list(tabla.scan()):
            if self._evaluar(stm.condicion, registro, resolver):
                if tabla.delete(rid):
                    borrados += 1
        self.resultado = Resultado(f"{borrados} fila(s) eliminada(s)")

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

        nombre = f"idx_{stm.tabla}_{stm.columna}"
        if stm.tipo == IndexKind.HASH_IDX:
            self.sm.index_manager.create_hash_index(
                nombre, tabla, pos, stm.columna
            )
            tipo_nombre = "HASH"
            org = "no agrupado"
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

    def visit_transaction_stmt(self, stm):
        self.resultado = Resultado(
            "BEGIN TRANSACTION" if stm.es_begin else "END TRANSACTION"
        )

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
        tabla: es el uso estrategico de indices del planificador."""
        plan = self._plan_indice(tabla, condicion, resolver)

        if plan is None:
            for _, registro in tabla.scan():
                if condicion is None or self._evaluar(condicion, registro, resolver):
                    yield registro
            return

        for registro in self._candidatos_con_indice(tabla, plan):
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

    def _indice_para(self, tabla, pos):
        """
        Devuelve ('clustered'|'unclustered'|'hash', indice) para la
        columna pos si hay un indice sobre ella, o None.

        Si la columna tiene varios indices secundarios se prefiere el B+
        (responde busquedas por punto Y por rango); el hash se usa como
        alternativa cuando es el unico indice postulante.
        """
        if tabla.clustered_index is not None and pos == tabla.key_index:
            return ("clustered", tabla.clustered_index)
        secundarios = tabla.secondary_indexes.get(pos)
        if secundarios:
            for idx in secundarios:
                if not isinstance(idx, HashIndex):
                    return ("unclustered", idx)
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
        pos = resolver(colref)

        def items():
            for registro in self._scan_filtrado(tabla, condicion, resolver):
                yield registro[pos], serializador.encode(registro)

        sorter = ExternalSorter(reverse=reverse, budget=EXTERNAL_SORT_BUDGET)
        try:
            for _, registro_bytes in sorter.sort(items()):
                yield serializador.decode(registro_bytes)
        finally:
            sorter.cleanup()

    def _ordenar_externo(self, filas, resolver, colref, reverse, serializador):
        """Ordena un stream de registros combinados (p.ej. de un JOIN)
        con el sorter externo, usando `serializador` para persistirlos."""
        pos = resolver(colref)

        def items():
            for registro in filas:
                yield registro[pos], serializador.encode(registro)

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
        """
        Extrae el valor Python de un nodo Value del parser.
        """
        if isinstance(nodo, (IntValue, FloatValue, StrValue, BoolValue)):
            return nodo.value
        raise ExecutionError(f"Valor no soportado: {nodo}")

    def _evaluar(self, cond, registro, resolver) -> bool:
        """
        Evalua una condicion del WHERE sobre un registro ya leido.
        """
        if isinstance(cond, OrCond):
            return any(self._evaluar(c, registro, resolver) for c in cond.condiciones)
        if isinstance(cond, AndCond):
            return all(self._evaluar(c, registro, resolver) for c in cond.condiciones)

        if isinstance(cond, CompareCond):
            izq = registro[resolver(cond.columna)]
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
