#ifndef PARSER_H
#define PARSER_H

#include "scanner.h"    // Incluye la definición del escáner (provee tokens al parser)
#include "ast.h"        // Incluye las definiciones para construir el Árbol de Sintaxis Abstracta (AST)

class Parser {
private:
    Scanner* scanner;          // Puntero al escáner, de donde se leen los tokens
    Token *current, *previous; // Punteros al token actual y al anterior
    bool match(Token::Type ttype);   // Verifica si el token actual coincide con un tipo esperado y avanza si es así
    bool check(Token::Type ttype);   // Comprueba si el token actual es de cierto tipo, sin avanzar
    bool advance();                  // Avanza al siguiente token
    bool isAtEnd();                  // Comprueba si ya se llegó al final de la entrada
    // Lanzan la excepción incluyendo la fila y columna del token actual
    [[noreturn]] void error(const string& mensaje);
    [[noreturn]] void errorSemantico(const string& mensaje);
public:
    Parser(Scanner* scanner);
    Programa* parseProgram();        // Punto de entrada: analiza un programa completo
    Programa* parseP();              // Regla gramatical P
    Stmt* parsestmt();               // Una sentencia cualquiera
    Stmt* parsecreate();             // Decide entre CREATE TABLE y CREATE INDEX
    Stmt* parsecreatetable();
    Stmt* parsecreateindex();
    Stmt* parseselect();
    Stmt* parseinsert();
    Stmt* parsedelete();
    Stmt* parsetransaction();
    ColumnDec* parsecolumndec();     // Declaración de columna
    SelectItem* parseselectitem();   // Elemento de la proyección
    JoinClause* parsejoin();
    Cond* parseCOND();               // Nivel OR
    Cond* parseAND();                // Nivel AND
    Cond* parsePRED();               // Predicado hoja o ( COND )
    ColRef* parsecolref();
    Value* parsevalue();
};

#endif // PARSER_H
