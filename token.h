#ifndef TOKEN_H
#define TOKEN_H

#include <string>
#include <ostream>

using namespace std;

class Token {
public:
    // Tipos de token
    enum Type {
        // palabras reservadas
        CREATE,
        TABLE,
        INDEX,
        ON,
        USING,
        CLUSTERED,
        PRIMARY,
        KEY,

        // organizacion del archivo de datos
        HEAP,
        SEQUENTIAL,

        // tipos de indice
        BTREE,
        HASH,

        // tipos de dato
        INT,
        FLOAT,
        BOOL,
        DATE,
        VARCHAR,

        // palabras reservadas: DML
        SELECT,
        FROM,
        JOIN,
        WHERE,
        GROUP,
        ORDER,
        BY,
        ASC,
        DESC,
        LIMIT,
        INSERT,
        INTO,
        VALUES,
        DELETE,

        // transacciones
        BEGIN,
        END_KW,
        TRANSACTION,

        // condiciones
        AND,
        OR,
        BETWEEN,
        TRUE_KW,
        FALSE_KW,

        // funciones de agregacion
        COUNT,
        SUM,
        AVG,
        MIN,
        MAX,

        // operadores relacionales
        EQ,      // =
        NEQ,     // != o <>
        LT,      // <
        LE,      // <=
        GT,      // >
        GE,      // >=

        // signos de puntuacion
        LPAREN,  // (
        RPAREN,  // )
        COMA,    // ,
        SEMICOL, // ;
        STAR,    // *
        PUNTO,   // .

        NUM,     // num
        STR,     // cadena entre comillas simples
        ID,      // identificador
        ERR,     // error
        END      // fin de entrada
    };

    // Atributos
    Type type;
    string text;
    int linea;    // Fila donde empieza el token (1-based)
    int columna;  // Columna donde empieza el token (1-based)

    // Constructores
    Token(Type type);
    Token(Type type, char c);
    Token(Type type, const string& source, int first, int last);

    // Sobrecarga de operadores de salida
    friend ostream& operator<<(ostream& outs, const Token& tok);
    friend ostream& operator<<(ostream& outs, const Token* tok);
};

#endif // TOKEN_H
