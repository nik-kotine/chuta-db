from token_sql import Token
from ast_sql import (AggFun, AndCond, BetweenCond, BoolValue, ColRef,
                     ColumnDec, CompareCond, CreateIndexStmt,
                     CreateTableStmt, DataType, DeleteStmt, FileOrg,
                     FloatValue, IndexKind, InsertStmt, IntValue,
                     JoinClause, OrCond, Programa, RelOp, SelectItem,
                     SelectStmt, SortDir, StrValue, TransactionStmt)


# En C++ esto era un runtime_error con la posicion pegada al mensaje.
# Aca la fila y la columna quedan como atributos, para poder armar el
# JSON que Monaco necesita sin tener que parsear el texto del error.
class SqlError(Exception):
    def __init__(self, tipo, mensaje, linea, columna):
        self.tipo = tipo
        self.mensaje = mensaje
        self.linea = linea
        self.columna = columna
        super().__init__("{} en linea {}, columna {}: {}".format(
            tipo, linea, columna, mensaje))


class Parser:

    # =============================
    # Metodos de la clase Parser
    # =============================

    def __init__(self, scanner):
        self.scanner = scanner          # De donde se leen los tokens
        self.previous = None            # Token anterior
        self.current = self.scanner.next_token()   # Token actual
        if self.current.type == Token.Type.ERR:
            self.error_lexico()

    def match(self, ttype):
        if self.check(ttype):
            self.advance()
            return True
        return False

    def check(self, ttype):
        if self.is_at_end():
            return False
        return self.current.type == ttype

    def advance(self):
        if not self.is_at_end():
            temp = self.current
            self.current = self.scanner.next_token()
            self.previous = temp

            if self.check(Token.Type.ERR):
                self.error_lexico()
            return True
        return False

    def is_at_end(self):
        return self.current.type == Token.Type.END

    # La posicion sale del token actual, que es el que no encajo con la regla
    def error(self, mensaje):
        raise SqlError("Error sintáctico", mensaje,
                       self.current.linea, self.current.columna)

    def error_semantico(self, mensaje):
        raise SqlError("Error semántico", mensaje,
                       self.current.linea, self.current.columna)

    def error_lexico(self):
        raise SqlError("Error léxico",
                       "caracter no reconocido '" + self.current.text + "'",
                       self.current.linea, self.current.columna)

    # =============================
    # Reglas gramaticales
    # =============================

    def parse_program(self):
        ast = self.parse_p()
        if not self.is_at_end():
            self.error("se esperaba ';' o el fin de la entrada")
        print("Parseo exitoso")
        return ast

    # P ::= Stmt {; Stmt}* [;]
    def parse_p(self):
        p = Programa()

        p.slist.append(self.parse_stmt())
        while self.match(Token.Type.SEMICOL):
            if self.is_at_end():
                break               # ';' final
            p.slist.append(self.parse_stmt())
        return p

    # Stmt ::= CreateTable | CreateIndex | Select | Insert | Delete | Transaction
    def parse_stmt(self):
        if self.check(Token.Type.CREATE):
            return self.parse_create()
        elif self.check(Token.Type.SELECT):
            return self.parse_select()
        elif self.check(Token.Type.INSERT):
            return self.parse_insert()
        elif self.check(Token.Type.DELETE):
            return self.parse_delete()
        elif self.check(Token.Type.BEGIN) or self.check(Token.Type.END_KW):
            return self.parse_transaction()
        else:
            self.error("se esperaba una sentencia")

    def parse_create(self):
        self.match(Token.Type.CREATE)
        if self.check(Token.Type.TABLE):
            return self.parse_create_table()
        elif self.check(Token.Type.INDEX):
            return self.parse_create_index()
        else:
            self.error("se esperaba TABLE o INDEX")

    # CreateTable ::= CREATE TABLE id ( ColDec {, ColDec}* ) USING FileOrg
    def parse_create_table(self):
        self.match(Token.Type.TABLE)
        ct = CreateTableStmt()

        if not self.match(Token.Type.ID):
            self.error("se esperaba el nombre de la tabla")
        ct.tabla = self.previous.text

        if not self.match(Token.Type.LPAREN):
            self.error("se esperaba (")
        ct.columnas.append(self.parse_column_dec())
        while self.match(Token.Type.COMA):
            ct.columnas.append(self.parse_column_dec())
        if not self.match(Token.Type.RPAREN):
            self.error("se esperaba )")

        if not self.match(Token.Type.USING):
            self.error("se esperaba USING")
        if self.match(Token.Type.HEAP):
            ct.org = FileOrg.HEAP_ORG
        elif self.match(Token.Type.SEQUENTIAL):
            ct.org = FileOrg.SEQUENTIAL_ORG
        else:
            self.error("se esperaba HEAP o SEQUENTIAL")
        return ct

    # ColDec ::= id Type [PRIMARY KEY]
    def parse_column_dec(self):
        cd = ColumnDec()

        if not self.match(Token.Type.ID):
            self.error("se esperaba el nombre de la columna")
        cd.nombre = self.previous.text

        if self.match(Token.Type.INT):
            cd.tipo = DataType.INT_TYPE
        elif self.match(Token.Type.FLOAT):
            cd.tipo = DataType.FLOAT_TYPE
        elif self.match(Token.Type.BOOL):
            cd.tipo = DataType.BOOL_TYPE
        elif self.match(Token.Type.DATE):
            cd.tipo = DataType.DATE_TYPE
        elif self.match(Token.Type.VARCHAR):
            cd.tipo = DataType.VARCHAR_TYPE
            if not self.match(Token.Type.LPAREN):
                self.error("se esperaba (")
            if not self.match(Token.Type.NUM):
                self.error("se esperaba la longitud del VARCHAR")
            cd.longitud = int(self.previous.text)
            if not self.match(Token.Type.RPAREN):
                self.error("se esperaba )")
        else:
            self.error("se esperaba un tipo de dato")

        if self.match(Token.Type.PRIMARY):
            if not self.match(Token.Type.KEY):
                self.error("se esperaba KEY")
            cd.primary_key = True
        return cd

    # CreateIndex ::= CREATE INDEX ON id ( id ) USING IndexKind [CLUSTERED]
    def parse_create_index(self):
        self.match(Token.Type.INDEX)
        ci = CreateIndexStmt()

        if not self.match(Token.Type.ON):
            self.error("se esperaba ON")
        if not self.match(Token.Type.ID):
            self.error("se esperaba el nombre de la tabla")
        ci.tabla = self.previous.text

        if not self.match(Token.Type.LPAREN):
            self.error("se esperaba (")
        if not self.match(Token.Type.ID):
            self.error("se esperaba la columna a indexar")
        ci.columna = self.previous.text
        if not self.match(Token.Type.RPAREN):
            self.error("se esperaba )")

        if not self.match(Token.Type.USING):
            self.error("se esperaba USING")
        if self.match(Token.Type.BTREE):
            ci.tipo = IndexKind.BTREE_IDX
        elif self.match(Token.Type.HASH):
            ci.tipo = IndexKind.HASH_IDX
        else:
            self.error("se esperaba BTREE o HASH")

        if self.match(Token.Type.CLUSTERED):
            ci.clustered = True

        # Un indice hash agrupado no tiene sentido: el hash no preserva el
        # orden, asi que no puede definir el orden fisico de los registros
        if ci.clustered and ci.tipo == IndexKind.HASH_IDX:
            self.error_semantico("un indice HASH no puede ser CLUSTERED")
        return ci

    # Select ::= SELECT SelList FROM id [Join] [Where] [GroupBy] [OrderBy] [Limit]
    def parse_select(self):
        self.match(Token.Type.SELECT)
        s = SelectStmt()

        if self.check(Token.Type.STAR):
            self.match(Token.Type.STAR)
            s.select_all = True
        else:
            s.proyeccion.append(self.parse_select_item())
            while self.match(Token.Type.COMA):
                s.proyeccion.append(self.parse_select_item())

        if not self.match(Token.Type.FROM):
            self.error("se esperaba FROM")
        if not self.match(Token.Type.ID):
            self.error("se esperaba el nombre de la tabla")
        s.tabla = self.previous.text

        if self.check(Token.Type.JOIN):
            s.join = self.parse_join()

        if self.match(Token.Type.WHERE):
            s.condicion = self.parse_cond()

        if self.match(Token.Type.GROUP):
            if not self.match(Token.Type.BY):
                self.error("se esperaba BY")
            s.group_by = self.parse_col_ref()

        if self.match(Token.Type.ORDER):
            if not self.match(Token.Type.BY):
                self.error("se esperaba BY")
            s.order_by = self.parse_col_ref()
            if self.match(Token.Type.ASC):
                s.direccion = SortDir.ASC_DIR
            elif self.match(Token.Type.DESC):
                s.direccion = SortDir.DESC_DIR

        if self.match(Token.Type.LIMIT):
            if not self.match(Token.Type.NUM):
                self.error("se esperaba el limite de filas")
            s.limite = int(self.previous.text)
            s.haylimite = True
        return s

    # Proj ::= ColRef | AggFun ( ColRef | * )
    def parse_select_item(self):
        item = SelectItem()

        if self.match(Token.Type.COUNT):
            item.agg = AggFun.COUNT_AGG
        elif self.match(Token.Type.SUM):
            item.agg = AggFun.SUM_AGG
        elif self.match(Token.Type.AVG):
            item.agg = AggFun.AVG_AGG
        elif self.match(Token.Type.MIN):
            item.agg = AggFun.MIN_AGG
        elif self.match(Token.Type.MAX):
            item.agg = AggFun.MAX_AGG

        if item.agg != AggFun.NONE_AGG:
            if not self.match(Token.Type.LPAREN):
                self.error("se esperaba (")
            if self.match(Token.Type.STAR):
                # Solo COUNT admite '*' como argumento
                if item.agg != AggFun.COUNT_AGG:
                    self.error_semantico("solo COUNT admite * como argumento")
                item.estrella = True
            else:
                item.columna = self.parse_col_ref()
            if not self.match(Token.Type.RPAREN):
                self.error("se esperaba )")
            return item

        item.columna = self.parse_col_ref()
        return item

    # Join ::= JOIN id ON ColRef = ColRef
    def parse_join(self):
        self.match(Token.Type.JOIN)
        j = JoinClause()

        if not self.match(Token.Type.ID):
            self.error("se esperaba la tabla del JOIN")
        j.tabla = self.previous.text

        if not self.match(Token.Type.ON):
            self.error("se esperaba ON")
        j.izquierda = self.parse_col_ref()
        if not self.match(Token.Type.EQ):
            self.error("se esperaba = en el JOIN")
        j.derecha = self.parse_col_ref()
        return j

    # Insert ::= INSERT INTO id VALUES ( Value {, Value}* )
    def parse_insert(self):
        self.match(Token.Type.INSERT)
        if not self.match(Token.Type.INTO):
            self.error("se esperaba INTO")
        ins = InsertStmt()

        if not self.match(Token.Type.ID):
            self.error("se esperaba el nombre de la tabla")
        ins.tabla = self.previous.text

        if not self.match(Token.Type.VALUES):
            self.error("se esperaba VALUES")
        if not self.match(Token.Type.LPAREN):
            self.error("se esperaba (")
        ins.valores.append(self.parse_value())
        while self.match(Token.Type.COMA):
            ins.valores.append(self.parse_value())
        if not self.match(Token.Type.RPAREN):
            self.error("se esperaba )")
        return ins

    # Delete ::= DELETE FROM id WHERE Cond
    def parse_delete(self):
        self.match(Token.Type.DELETE)
        if not self.match(Token.Type.FROM):
            self.error("se esperaba FROM")
        d = DeleteStmt()

        if not self.match(Token.Type.ID):
            self.error("se esperaba el nombre de la tabla")
        d.tabla = self.previous.text

        # WHERE obligatorio: evita borrar la tabla completa por descuido
        if not self.match(Token.Type.WHERE):
            self.error("DELETE requiere WHERE")
        d.condicion = self.parse_cond()
        return d

    # Transaction ::= BEGIN TRANSACTION | END TRANSACTION
    def parse_transaction(self):
        if self.match(Token.Type.BEGIN):
            es_begin = True
        elif self.match(Token.Type.END_KW):
            es_begin = False
        else:
            self.error("se esperaba BEGIN o END")
        if not self.match(Token.Type.TRANSACTION):
            self.error("se esperaba TRANSACTION")
        return TransactionStmt(es_begin)

    # Cond ::= AndCond {OR AndCond}*
    def parse_cond(self):
        l = self.parse_and()
        if not self.check(Token.Type.OR):
            return l

        o = OrCond()
        o.condiciones.append(l)
        while self.match(Token.Type.OR):
            o.condiciones.append(self.parse_and())
        return o

    # AndCond ::= Pred {AND Pred}*
    def parse_and(self):
        l = self.parse_pred()
        if not self.check(Token.Type.AND):
            return l

        a = AndCond()
        a.condiciones.append(l)
        while self.match(Token.Type.AND):
            a.condiciones.append(self.parse_pred())
        return a

    # Pred ::= ColRef RelOp Value | ColRef BETWEEN Value AND Value | ( Cond )
    def parse_pred(self):
        if self.match(Token.Type.LPAREN):
            c = self.parse_cond()
            if not self.match(Token.Type.RPAREN):
                self.error("se esperaba )")
            return c

        col = self.parse_col_ref()

        if self.match(Token.Type.BETWEEN):
            inf = self.parse_value()
            if not self.match(Token.Type.AND):
                self.error("se esperaba AND en BETWEEN")
            sup = self.parse_value()
            return BetweenCond(col, inf, sup)

        if self.match(Token.Type.EQ):
            op = RelOp.EQ_OP
        elif self.match(Token.Type.NEQ):
            op = RelOp.NEQ_OP
        elif self.match(Token.Type.LT):
            op = RelOp.LT_OP
        elif self.match(Token.Type.LE):
            op = RelOp.LE_OP
        elif self.match(Token.Type.GT):
            op = RelOp.GT_OP
        elif self.match(Token.Type.GE):
            op = RelOp.GE_OP
        else:
            self.error("se esperaba un operador relacional o BETWEEN")
        v = self.parse_value()
        return CompareCond(col, op, v)

    # ColRef ::= id | id . id
    def parse_col_ref(self):
        if not self.match(Token.Type.ID):
            self.error("se esperaba el nombre de una columna")
        primero = self.previous.text

        c = ColRef()
        if self.match(Token.Type.PUNTO):
            if not self.match(Token.Type.ID):
                self.error("se esperaba la columna despues del .")
            c.tabla = primero
            c.columna = self.previous.text
        else:
            c.columna = primero
        return c

    # Value ::= num | str | TRUE | FALSE
    def parse_value(self):
        if self.match(Token.Type.NUM):
            texto = self.previous.text
            if texto.find('.') != -1:
                return FloatValue(float(texto))
            return IntValue(int(texto))
        elif self.match(Token.Type.STR):
            return StrValue(self.previous.text)
        elif self.match(Token.Type.TRUE_KW):
            return BoolValue(True)
        elif self.match(Token.Type.FALSE_KW):
            return BoolValue(False)
        else:
            self.error("se esperaba un valor literal")
