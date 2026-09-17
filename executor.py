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

from ast_sql import (AggFun, AndCond, BetweenCond, BoolValue, CompareCond,
                     DataType, FileOrg, FloatValue, IntValue, OrCond, RelOp,
                     SortDir, StrValue)
from visitor import Visitor
from storage.storage_manager import StorageManager


class ExecutionError(Exception):
    """Error al ejecutar una sentencia ya parseada."""
    pass


# Traduccion de tipos: DataType del parser -> vocabulario de data_types.py
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

    # -----------------------------
    # Punto de entrada
    # -----------------------------

    def ejecutar(self, programa) -> list:
        salidas = []
        for stmt in programa.slist:
            stmt.accept(self)
            salidas.append(self.resultado)
        return salidas

    # -----------------------------
    # CREATE TABLE
    # -----------------------------

    def visit_create_table_stmt(self, stm):
        schema = [tipo_a_str(c) for c in stm.columnas]
        column_names = [c.nombre for c in stm.columnas]

        key_index = buscar_key_index(stm.columnas)
        if key_index == -1:
            # Sin esto, key_index=0 trataria la primera columna como clave
            # primaria sin que nadie lo haya pedido (error silencioso).
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

    # -----------------------------
    # INSERT
    # -----------------------------

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

    # -----------------------------
    # SELECT
    # -----------------------------

    def visit_select_stmt(self, stm):
        tabla = self._abrir(stm.tabla)

        if stm.join is not None:
            raise ExecutionError("JOIN todavia no esta implementado en el motor")

        # Filtrado
        filas = []
        for _, registro in tabla.scan():
            if stm.condicion is None or self._evaluar(stm.condicion, registro, tabla):
                filas.append(registro)

        # ORDER BY
        if stm.order_by is not None:
            pos = tabla.column_index(stm.order_by.columna)
            filas.sort(key=lambda r: r[pos],
                       reverse=(stm.direccion == SortDir.DESC_DIR))

        # LIMIT
        if stm.haylimite:
            filas = filas[:stm.limite]

        # Proyeccion
        if stm.select_all:
            nombres = list(tabla.column_names)
            proyectadas = filas
        else:
            nombres = []
            posiciones = []
            for item in stm.proyeccion:
                if item.agg != AggFun.NONE_AGG:
                    raise ExecutionError(
                        "Las funciones de agregacion todavia no estan implementadas"
                    )
                nombre = item.columna.columna
                nombres.append(nombre)
                posiciones.append(tabla.column_index(nombre))
            proyectadas = [[fila[p] for p in posiciones] for fila in filas]

        self.resultado = Resultado(columnas=nombres, filas=proyectadas)

    # -----------------------------
    # DELETE
    # -----------------------------

    def visit_delete_stmt(self, stm):
        tabla = self._abrir(stm.tabla)

        borrados = 0
        for rid, registro in list(tabla.scan()):
            if self._evaluar(stm.condicion, registro, tabla):
                if tabla.delete(rid):
                    borrados += 1
        self.resultado = Resultado(f"{borrados} fila(s) eliminada(s)")

    # -----------------------------
    # Fuera del alcance actual
    # -----------------------------

    def visit_create_index_stmt(self, stm):
        raise ExecutionError(
            "CREATE INDEX todavia no esta conectado (pendiente: IndexManager)"
        )

    def visit_transaction_stmt(self, stm):
        self.resultado = Resultado(
            "BEGIN TRANSACTION" if stm.es_begin else "END TRANSACTION"
        )

    # -----------------------------
    # Auxiliares
    # -----------------------------

    def _abrir(self, nombre):
        try:
            return self.sm.open_table(nombre)
        except KeyError:
            raise ExecutionError(f"La tabla '{nombre}' no existe")

    def _valor(self, nodo):
        """Extrae el valor Python de un nodo Value del parser."""
        if isinstance(nodo, (IntValue, FloatValue, StrValue, BoolValue)):
            return nodo.value
        raise ExecutionError(f"Valor no soportado: {nodo}")

    def _evaluar(self, cond, registro, tabla) -> bool:
        """Evalua una condicion del WHERE sobre un registro ya leido."""
        if isinstance(cond, OrCond):
            return any(self._evaluar(c, registro, tabla) for c in cond.condiciones)
        if isinstance(cond, AndCond):
            return all(self._evaluar(c, registro, tabla) for c in cond.condiciones)

        if isinstance(cond, CompareCond):
            izq = registro[tabla.column_index(cond.columna.columna)]
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
            val = registro[tabla.column_index(cond.columna.columna)]
            return self._valor(cond.inferior) <= val <= self._valor(cond.superior)

        raise ExecutionError(f"Condicion no soportada: {cond}")

    # -----------------------------
    # Nodos que no son sentencias: el ejecutor no los visita sueltos
    # -----------------------------

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
