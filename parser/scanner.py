from token_sql import Token


# -----------------------------
# Funciones auxiliares
# -----------------------------

def is_white_space(c):
    return c == ' ' or c == '\n' or c == '\r' or c == '\t'


# SQL no distingue mayusculas en las palabras reservadas, asi que el
# lexema se normaliza antes de compararlo
def a_mayusculas(s):
    return s.upper()


# Tabla de palabras reservadas
PALABRAS_RESERVADAS = {
    # DDL
    "CREATE": Token.Type.CREATE,
    "TABLE": Token.Type.TABLE,
    "INDEX": Token.Type.INDEX,
    "ON": Token.Type.ON,
    "USING": Token.Type.USING,
    "CLUSTERED": Token.Type.CLUSTERED,
    "PRIMARY": Token.Type.PRIMARY,
    "KEY": Token.Type.KEY,

    # Organizacion del archivo (2.1.1)
    "HEAP": Token.Type.HEAP,
    "SEQUENTIAL": Token.Type.SEQUENTIAL,

    # Tipos de indice (2.1.2)
    "BTREE": Token.Type.BTREE,
    "HASH": Token.Type.HASH,

    # Tipos de dato
    "INT": Token.Type.INT,
    "FLOAT": Token.Type.FLOAT,
    "BOOL": Token.Type.BOOL,
    "DATE": Token.Type.DATE,
    "VARCHAR": Token.Type.VARCHAR,

    # DML
    "SELECT": Token.Type.SELECT,
    "FROM": Token.Type.FROM,
    "JOIN": Token.Type.JOIN,
    "WHERE": Token.Type.WHERE,
    "GROUP": Token.Type.GROUP,
    "ORDER": Token.Type.ORDER,
    "BY": Token.Type.BY,
    "ASC": Token.Type.ASC,
    "DESC": Token.Type.DESC,
    "LIMIT": Token.Type.LIMIT,
    "INSERT": Token.Type.INSERT,
    "INTO": Token.Type.INTO,
    "VALUES": Token.Type.VALUES,
    "DELETE": Token.Type.DELETE,

    # Transacciones (2.1.4)
    "BEGIN": Token.Type.BEGIN,
    "END": Token.Type.END_KW,
    "TRANSACTION": Token.Type.TRANSACTION,

    # Condiciones
    "AND": Token.Type.AND,
    "OR": Token.Type.OR,
    "BETWEEN": Token.Type.BETWEEN,
    "TRUE": Token.Type.TRUE_KW,
    "FALSE": Token.Type.FALSE_KW,

    # Funciones de agregacion
    "COUNT": Token.Type.COUNT,
    "SUM": Token.Type.SUM,
    "AVG": Token.Type.AVG,
    "MIN": Token.Type.MIN,
    "MAX": Token.Type.MAX,
}


class Scanner:

    # Constructor
    def __init__(self, in_s):
        self.input = in_s
        self.first = 0
        self.current = 0
        self.linea = 1      # Fila actual (1-based)
        self.columna = 1    # Columna actual (1-based)

    # Consume un caracter. Es el unico lugar donde se mueve current, para
    # que linea y columna nunca queden desincronizadas
    def avanzar(self):
        if self.input[self.current] == '\n':
            self.linea += 1
            self.columna = 1
        else:
            self.columna += 1
        self.current += 1

    # Salta espacios en blanco y comentarios de linea (--)
    def saltar_espacios(self):
        while self.current < len(self.input):
            if is_white_space(self.input[self.current]):
                self.avanzar()
            elif (self.input[self.current] == '-'
                  and self.current + 1 < len(self.input)
                  and self.input[self.current + 1] == '-'):
                while (self.current < len(self.input)
                       and self.input[self.current] != '\n'):
                    self.avanzar()
            else:
                break

    # -----------------------------
    # next_token: obtiene el siguiente token
    # -----------------------------
    def next_token(self):
        self.saltar_espacios()

        # Posicion donde empieza el token, antes de consumir nada
        linea_tok = self.linea
        columna_tok = self.columna

        # Fin de la entrada
        if self.current >= len(self.input):
            return Token(Token.Type.END, "", linea_tok, columna_tok)

        c = self.input[self.current]

        self.first = self.current

        # Numeros (enteros y reales)
        if c.isdigit():
            self.avanzar()
            while self.current < len(self.input) and self.input[self.current].isdigit():
                self.avanzar()
            # El punto solo es decimal si le sigue un digito; asi no se
            # confunde 1.5 con la notacion tabla.columna
            if (self.current + 1 < len(self.input)
                    and self.input[self.current] == '.'
                    and self.input[self.current + 1].isdigit()):
                self.avanzar()
                while self.current < len(self.input) and self.input[self.current].isdigit():
                    self.avanzar()
            token = Token(Token.Type.NUM,
                          self.input[self.first:self.current])

        # Palabras reservadas e identificadores
        elif c.isalpha() or c == '_':
            self.avanzar()
            while (self.current < len(self.input)
                   and (self.input[self.current].isalnum()
                        or self.input[self.current] == '_')):
                self.avanzar()
            texto = self.input[self.first:self.current]
            lexema = a_mayusculas(texto)

            if lexema in PALABRAS_RESERVADAS:
                token = Token(PALABRAS_RESERVADAS[lexema], texto)
            else:
                token = Token(Token.Type.ID, texto)

        # Cadenas entre comillas simples ('' representa una comilla literal)
        elif c == '\'':
            self.avanzar()
            texto = ""
            cerrada = False
            while self.current < len(self.input) and self.input[self.current] != '\n':
                if self.input[self.current] == '\'':
                    if (self.current + 1 < len(self.input)
                            and self.input[self.current + 1] == '\''):
                        texto += '\''
                        self.avanzar()
                        self.avanzar()
                        continue
                    self.avanzar()
                    cerrada = True
                    break
                texto += self.input[self.current]
                self.avanzar()
            if not cerrada:
                token = Token(Token.Type.ERR, '\'')
            else:
                token = Token(Token.Type.STR, texto)

        # Operadores y signos de puntuacion
        elif c in ",;().*=<>!":
            if c == ',':
                token = Token(Token.Type.COMA, c)
            elif c == ';':
                token = Token(Token.Type.SEMICOL, c)
            elif c == '(':
                token = Token(Token.Type.LPAREN, c)
            elif c == ')':
                token = Token(Token.Type.RPAREN, c)
            elif c == '.':
                token = Token(Token.Type.PUNTO, c)
            elif c == '*':
                token = Token(Token.Type.STAR, c)
            elif c == '=':
                token = Token(Token.Type.EQ, c)
            elif c == '<':
                if self.sigue('='):
                    self.avanzar()
                    token = Token(Token.Type.LE, "<=")
                elif self.sigue('>'):
                    self.avanzar()
                    token = Token(Token.Type.NEQ, "<>")
                else:
                    token = Token(Token.Type.LT, c)
            elif c == '>':
                if self.sigue('='):
                    self.avanzar()
                    token = Token(Token.Type.GE, ">=")
                else:
                    token = Token(Token.Type.GT, c)
            elif c == '!':
                if self.sigue('='):
                    self.avanzar()
                    token = Token(Token.Type.NEQ, "!=")
                else:
                    token = Token(Token.Type.ERR, c)
            self.avanzar()

        # Caracter invalido
        else:
            token = Token(Token.Type.ERR, c)
            self.avanzar()

        # Estampar la posicion donde empezo el token
        token.linea = linea_tok
        token.columna = columna_tok
        return token

    # Comprueba si el caracter siguiente al actual es el esperado
    def sigue(self, c):
        return (self.current + 1 < len(self.input)
                and self.input[self.current + 1] == c)


# -----------------------------
# Funcion de prueba
# -----------------------------

def ejecutar_scanner(scanner, input_file):
    # Crear nombre para archivo de salida
    pos = input_file.rfind(".")
    if pos != -1:
        output_file_name = input_file[:pos]
    else:
        output_file_name = input_file
    output_file_name += "_tokens.txt"

    try:
        out_file = open(output_file_name, "w", encoding="utf-8")
    except OSError:
        print("Error: no se pudo abrir el archivo " + output_file_name)
        return

    out_file.write("Scanner\n\n")

    while True:
        tok = scanner.next_token()

        if tok.type == Token.Type.END:
            out_file.write(str(tok) + "\n")
            out_file.write("\nScanner exitoso\n\n")
            out_file.close()
            return

        if tok.type == Token.Type.ERR:
            out_file.write(str(tok) + "\n")
            out_file.write("Caracter invalido en linea " + str(tok.linea)
                           + ", columna " + str(tok.columna) + "\n\n")
            out_file.write("Scanner no exitoso\n\n")
            out_file.close()
            return

        out_file.write(str(tok) + "\n")
