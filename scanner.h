#ifndef SCANNER_H
#define SCANNER_H

#include <string>
#include "token.h"
using namespace std;

class Scanner {
private:
    string input;
    int first;
    int current;
    int linea;      // Fila actual (1-based)
    int columna;    // Columna actual (1-based)

    // Consume un caracter actualizando linea y columna
    void avanzar();

    // Salta espacios en blanco y comentarios de linea (--)
    void saltarEspacios();

public:
    // Constructor
    Scanner(const char* in_s);

    // Retorna el siguiente token
    Token* nextToken();

    // Destructor
    ~Scanner();

};

// Ejecutar scanner
void ejecutar_scanner(Scanner* scanner, const string& InputFile);

#endif // SCANNER_H
