from ast_sql import (AggFun, Cond, DataType, FileOrg, IndexKind, SortDir)


class Visitor:
    # En C++ estas eran sobrecargas de visit(); Python no resuelve por
    # tipo de argumento, asi que cada nodo tiene su propio metodo y su
    # accept() llama al que le corresponde.
    def visit_int_value(self, v):
        raise NotImplementedError

    def visit_float_value(self, v):
        raise NotImplementedError

    def visit_str_value(self, v):
        raise NotImplementedError

    def visit_bool_value(self, v):
        raise NotImplementedError

    def visit_col_ref(self, c):
        raise NotImplementedError

    def visit_or_cond(self, c):
        raise NotImplementedError

    def visit_and_cond(self, c):
        raise NotImplementedError

    def visit_compare_cond(self, c):
        raise NotImplementedError

    def visit_between_cond(self, c):
        raise NotImplementedError

    def visit_select_item(self, item):
        raise NotImplementedError

    def visit_join_clause(self, j):
        raise NotImplementedError

    def visit_column_dec(self, cd):
        raise NotImplementedError

    def visit_create_table_stmt(self, stm):
        raise NotImplementedError

    def visit_create_index_stmt(self, stm):
        raise NotImplementedError

    def visit_select_stmt(self, stm):
        raise NotImplementedError

    def visit_insert_stmt(self, stm):
        raise NotImplementedError

    def visit_delete_stmt(self, stm):
        raise NotImplementedError

    def visit_transaction_stmt(self, stm):
        raise NotImplementedError

    def visit_programa(self, program):
        raise NotImplementedError


# Imprime el AST reconstruyendo la sentencia SQL
class PrintVisitor(Visitor):

    # -----------------------------
    # Valores literales
    # -----------------------------

    def visit_int_value(self, v):
        print(v.value, end="")

    def visit_float_value(self, v):
        print(v.value, end="")

    def visit_str_value(self, v):
        print("'" + v.value + "'", end="")

    def visit_bool_value(self, v):
        print("TRUE" if v.value else "FALSE", end="")

    def visit_col_ref(self, c):
        if c.tabla != "":
            print(c.tabla + ".", end="")
        print(c.columna, end="")

    # -----------------------------
    # Condiciones
    # -----------------------------

    def visit_or_cond(self, c):
        print("(", end="")
        primero = True
        for cond in c.condiciones:
            if not primero:
                print(" OR ", end="")
            cond.accept(self)
            primero = False
        print(")", end="")

    def visit_and_cond(self, c):
        print("(", end="")
        primero = True
        for cond in c.condiciones:
            if not primero:
                print(" AND ", end="")
            cond.accept(self)
            primero = False
        print(")", end="")

    def visit_compare_cond(self, c):
        c.columna.accept(self)
        print(" " + Cond.relop_to_char(c.op) + " ", end="")
        c.valor.accept(self)

    def visit_between_cond(self, c):
        c.columna.accept(self)
        print(" BETWEEN ", end="")
        c.inferior.accept(self)
        print(" AND ", end="")
        c.superior.accept(self)

    # -----------------------------
    # Piezas del SELECT
    # -----------------------------

    def visit_select_item(self, item):
        if item.agg == AggFun.NONE_AGG:
            if item.estrella:
                print("*", end="")
            else:
                item.columna.accept(self)
            return

        if item.agg == AggFun.COUNT_AGG:
            print("COUNT(", end="")
        elif item.agg == AggFun.SUM_AGG:
            print("SUM(", end="")
        elif item.agg == AggFun.AVG_AGG:
            print("AVG(", end="")
        elif item.agg == AggFun.MIN_AGG:
            print("MIN(", end="")
        elif item.agg == AggFun.MAX_AGG:
            print("MAX(", end="")

        if item.estrella:
            print("*", end="")
        else:
            item.columna.accept(self)
        print(")", end="")

    def visit_join_clause(self, j):
        print(" JOIN " + j.tabla + " ON ", end="")
        j.izquierda.accept(self)
        print(" = ", end="")
        j.derecha.accept(self)

    def visit_column_dec(self, cd):
        print(cd.nombre + " ", end="")
        if cd.tipo == DataType.INT_TYPE:
            print("INT", end="")
        elif cd.tipo == DataType.FLOAT_TYPE:
            print("FLOAT", end="")
        elif cd.tipo == DataType.BOOL_TYPE:
            print("BOOL", end="")
        elif cd.tipo == DataType.DATE_TYPE:
            print("DATE", end="")
        elif cd.tipo == DataType.VARCHAR_TYPE:
            print("VARCHAR(" + str(cd.longitud) + ")", end="")
        if cd.primary_key:
            print(" PRIMARY KEY", end="")

    # -----------------------------
    # Sentencias
    # -----------------------------

    def visit_create_table_stmt(self, stm):
        print("CREATE TABLE " + stm.tabla + " (", end="")
        primero = True
        for col in stm.columnas:
            if not primero:
                print(", ", end="")
            col.accept(self)
            primero = False
        print(") USING ", end="")
        print("HEAP" if stm.org == FileOrg.HEAP_ORG else "SEQUENTIAL", end="")

    def visit_create_index_stmt(self, stm):
        print("CREATE INDEX ON " + stm.tabla + " (" + stm.columna + ")", end="")
        print(" USING ", end="")
        print("BTREE" if stm.tipo == IndexKind.BTREE_IDX else "HASH", end="")
        if stm.clustered:
            print(" CLUSTERED", end="")

    def visit_select_stmt(self, stm):
        print("SELECT ", end="")
        if stm.select_all:
            print("*", end="")
        else:
            primero = True
            for item in stm.proyeccion:
                if not primero:
                    print(", ", end="")
                item.accept(self)
                primero = False
        print(" FROM " + stm.tabla, end="")

        if stm.join is not None:
            stm.join.accept(self)

        if stm.condicion is not None:
            print(" WHERE ", end="")
            stm.condicion.accept(self)
        if stm.group_by is not None:
            print(" GROUP BY ", end="")
            stm.group_by.accept(self)
        if stm.order_by is not None:
            print(" ORDER BY ", end="")
            stm.order_by.accept(self)
            print(" ASC" if stm.direccion == SortDir.ASC_DIR else " DESC", end="")
        if stm.haylimite:
            print(" LIMIT " + str(stm.limite), end="")

    def visit_insert_stmt(self, stm):
        print("INSERT INTO " + stm.tabla + " VALUES (", end="")
        primero = True
        for v in stm.valores:
            if not primero:
                print(", ", end="")
            v.accept(self)
            primero = False
        print(")", end="")

    def visit_delete_stmt(self, stm):
        print("DELETE FROM " + stm.tabla + " WHERE ", end="")
        stm.condicion.accept(self)

    def visit_transaction_stmt(self, stm):
        print("BEGIN TRANSACTION" if stm.es_begin else "END TRANSACTION", end="")

    # -----------------------------
    # Programa
    # -----------------------------

    def visit_programa(self, program):
        for stm in program.slist:
            stm.accept(self)
            print(";")

    def imprimir(self, program):
        if program is None:
            return
        print("IMPRIMIR\n")
        program.accept(self)
        print()
