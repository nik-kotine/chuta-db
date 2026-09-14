from enum import Enum, auto


class Token:

    # Tipos de token
    class Type(Enum):
        # Palabras reservadas: DDL
        CREATE = auto()
        TABLE = auto()
        INDEX = auto()
        ON = auto()
        USING = auto()
        CLUSTERED = auto()
        PRIMARY = auto()
        KEY = auto()

        # Organizacion del archivo de datos
        HEAP = auto()
        SEQUENTIAL = auto()

        # Tipos de indice
        BTREE = auto()
        HASH = auto()

        # Tipos de dato
        INT = auto()
        FLOAT = auto()
        BOOL = auto()
        DATE = auto()
        VARCHAR = auto()

        # Palabras reservadas: DML
        SELECT = auto()
        FROM = auto()
        JOIN = auto()
        WHERE = auto()
        GROUP = auto()
        ORDER = auto()
        BY = auto()
        ASC = auto()
        DESC = auto()
        LIMIT = auto()
        INSERT = auto()
        INTO = auto()
        VALUES = auto()
        DELETE = auto()

        # Transacciones
        BEGIN = auto()
        END_KW = auto()
        TRANSACTION = auto()

        # Condiciones
        AND = auto()
        OR = auto()
        BETWEEN = auto()
        TRUE_KW = auto()
        FALSE_KW = auto()

        # Funciones de agregacion
        COUNT = auto()
        SUM = auto()
        AVG = auto()
        MIN = auto()
        MAX = auto()

        # Operadores relacionales
        EQ = auto()       # =
        NEQ = auto()      # != o <>
        LT = auto()       # <
        LE = auto()       # <=
        GT = auto()       # >
        GE = auto()       # >=

        # Signos de puntuacion
        LPAREN = auto()   # (
        RPAREN = auto()   # )
        COMA = auto()     # ,
        SEMICOL = auto()  # ;
        STAR = auto()     # *
        PUNTO = auto()    # .

        NUM = auto()      # Numero
        STR = auto()      # Cadena entre comillas simples
        ID = auto()       # Identificador
        ERR = auto()      # Error
        END = auto()      # Fin de entrada

    # Constructor
    # La posicion se inicializa en 0 y la estampa el Scanner al devolver
    # el token, en next_token()
    def __init__(self, type, text="", linea=0, columna=0):
        self.type = type
        self.text = text
        self.linea = linea
        self.columna = columna

    # Equivalente al operator<< de C++
    def __str__(self):
        if self.type == Token.Type.END:
            return "TOKEN(END)"
        return 'TOKEN({}, "{}")'.format(self.type.name, self.text)

    def __repr__(self):
        return self.__str__()
