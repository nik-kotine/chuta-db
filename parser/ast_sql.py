from enum import Enum, auto


# Organizacion del archivo de datos (2.1.1)
class FileOrg(Enum):
    HEAP_ORG = auto()
    SEQUENTIAL_ORG = auto()


# Estructura del indice (2.1.2)
class IndexKind(Enum):
    BTREE_IDX = auto()
    HASH_IDX = auto()


# Tipos de dato del catalogo
class DataType(Enum):
    INT_TYPE = auto()
    FLOAT_TYPE = auto()
    BOOL_TYPE = auto()
    DATE_TYPE = auto()
    VARCHAR_TYPE = auto()


# Operadores relacionales soportados
class RelOp(Enum):
    EQ_OP = auto()
    NEQ_OP = auto()
    LT_OP = auto()
    LE_OP = auto()
    GT_OP = auto()
    GE_OP = auto()


# Funciones de agregacion; NONE_AGG significa columna simple
class AggFun(Enum):
    NONE_AGG = auto()
    COUNT_AGG = auto()
    SUM_AGG = auto()
    AVG_AGG = auto()
    MIN_AGG = auto()
    MAX_AGG = auto()


# Direccion del ORDER BY
class SortDir(Enum):
    ASC_DIR = auto()
    DESC_DIR = auto()


# -----------------------------
# Valores literales
# -----------------------------

# Clase abstracta Value
class Value:
    def accept(self, visitor):
        raise NotImplementedError


# Literal entero
class IntValue(Value):
    def __init__(self, value):
        self.value = value

    def accept(self, visitor):
        return visitor.visit_int_value(self)


# Literal real
class FloatValue(Value):
    def __init__(self, value):
        self.value = value

    def accept(self, visitor):
        return visitor.visit_float_value(self)


# Literal de cadena
class StrValue(Value):
    def __init__(self, value):
        self.value = value

    def accept(self, visitor):
        return visitor.visit_str_value(self)


# Literal booleano
class BoolValue(Value):
    def __init__(self, value):
        self.value = value

    def accept(self, visitor):
        return visitor.visit_bool_value(self)


# -----------------------------
# Referencia a columna: col o tabla.col
# -----------------------------

class ColRef:
    def __init__(self, tabla="", columna=""):
        self.tabla = tabla      # vacio si la columna no se califico
        self.columna = columna

    def accept(self, visitor):
        return visitor.visit_col_ref(self)


# -----------------------------
# Condiciones del WHERE
#
# La jerarquia OrCond -> AndCond -> predicado codifica la precedencia:
# OR liga mas flojo que AND, asi que "a=1 AND b=2 OR c=3" se agrupa
# como "(a=1 AND b=2) OR c=3".
# -----------------------------

# Clase abstracta Cond
class Cond:
    def accept(self, visitor):
        raise NotImplementedError

    # Conversion operador -> string
    @staticmethod
    def relop_to_char(op):
        if op == RelOp.EQ_OP:
            return "="
        if op == RelOp.NEQ_OP:
            return "!="
        if op == RelOp.LT_OP:
            return "<"
        if op == RelOp.LE_OP:
            return "<="
        if op == RelOp.GT_OP:
            return ">"
        if op == RelOp.GE_OP:
            return ">="
        return "?"


# Disyuncion de condiciones
class OrCond(Cond):
    def __init__(self):
        self.condiciones = []

    def accept(self, visitor):
        return visitor.visit_or_cond(self)


# Conjuncion de condiciones
class AndCond(Cond):
    def __init__(self):
        self.condiciones = []

    def accept(self, visitor):
        return visitor.visit_and_cond(self)


# Predicado columna op valor
class CompareCond(Cond):
    def __init__(self, columna, op, valor):
        self.columna = columna
        self.op = op
        self.valor = valor

    def accept(self, visitor):
        return visitor.visit_compare_cond(self)


# Predicado columna BETWEEN inferior AND superior
class BetweenCond(Cond):
    def __init__(self, columna, inferior, superior):
        self.columna = columna
        self.inferior = inferior
        self.superior = superior

    def accept(self, visitor):
        return visitor.visit_between_cond(self)


# -----------------------------
# Piezas del SELECT
# -----------------------------

# Un elemento de la lista de proyeccion
class SelectItem:
    def __init__(self):
        self.agg = AggFun.NONE_AGG
        self.estrella = False   # '*' suelto, o el '*' de COUNT(*)
        self.columna = None

    def accept(self, visitor):
        return visitor.visit_select_item(self)


# JOIN tabla ON izquierda = derecha
class JoinClause:
    def __init__(self):
        self.tabla = ""
        self.izquierda = None
        self.derecha = None

    def accept(self, visitor):
        return visitor.visit_join_clause(self)


# -----------------------------
# Declaracion de columna en el CREATE TABLE
# -----------------------------

class ColumnDec:
    def __init__(self):
        self.nombre = ""
        self.tipo = DataType.INT_TYPE
        self.longitud = 0        # solo si tipo == VARCHAR_TYPE
        self.primary_key = False

    def accept(self, visitor):
        return visitor.visit_column_dec(self)


# -----------------------------
# Sentencias
# -----------------------------

# Clase abstracta Stmt
class Stmt:
    def accept(self, visitor):
        raise NotImplementedError


# CREATE TABLE t (col tipo [PRIMARY KEY], ...) USING (heap | sequential)
class CreateTableStmt(Stmt):
    def __init__(self):
        self.tabla = ""
        self.columnas = []
        self.org = FileOrg.HEAP_ORG

    def accept(self, visitor):
        return visitor.visit_create_table_stmt(self)


# CREATE INDEX ON t (col) USING (btree | hash) [CLUSTERED]
class CreateIndexStmt(Stmt):
    def __init__(self):
        self.tabla = ""
        self.columna = ""
        self.tipo = IndexKind.BTREE_IDX
        self.clustered = False

    def accept(self, visitor):
        return visitor.visit_create_index_stmt(self)


# SELECT ... FROM t [JOIN ...] [WHERE ...] [GROUP BY ...] [ORDER BY ...] [LIMIT n]
class SelectStmt(Stmt):
    def __init__(self):
        self.select_all = False     # SELECT *
        self.proyeccion = []
        self.tabla = ""
        self.join = None
        self.condicion = None
        self.group_by = None
        self.order_by = None
        self.direccion = SortDir.ASC_DIR
        self.limite = 0
        self.haylimite = False

    def accept(self, visitor):
        return visitor.visit_select_stmt(self)


# INSERT INTO t VALUES (...)
class InsertStmt(Stmt):
    def __init__(self):
        self.tabla = ""
        self.valores = []

    def accept(self, visitor):
        return visitor.visit_insert_stmt(self)


# DELETE FROM t WHERE cond
class DeleteStmt(Stmt):
    def __init__(self):
        self.tabla = ""
        self.condicion = None

    def accept(self, visitor):
        return visitor.visit_delete_stmt(self)


# BEGIN TRANSACTION | END TRANSACTION
class TransactionStmt(Stmt):
    def __init__(self, es_begin):
        self.es_begin = es_begin

    def accept(self, visitor):
        return visitor.visit_transaction_stmt(self)


# -----------------------------
# Raiz del arbol
# -----------------------------

class Programa:
    def __init__(self):
        self.slist = []

    def accept(self, visitor):
        return visitor.visit_programa(self)
