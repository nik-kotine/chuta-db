#include <iostream>
#include <fstream>
#include <string>
#include "scanner.h"
#include "parser.h"
#include "ast.h"
#include "visitor.h"

using namespace std;

int main(int argc, const char* argv[]) {
    // Verificar número de argumentos
    if (argc != 2) {
        cout << "Número incorrecto de argumentos.\n";
        cout << "Uso: " << argv[0] << " <archivo_de_entrada>" << endl;
        return 1;
    }

    // Abrir archivo de entrada
    ifstream infile(argv[1]);
    if (!infile.is_open()) {
        cout << "No se pudo abrir el archivo: " << argv[1] << endl;
        return 1;
    }

    // Leer contenido completo del archivo en un string
    string input, line;
    while (getline(infile, line)) {
        input += line + '\n';
    }
    infile.close();

    // Crear instancias de Scanner
    Scanner scanner1(input.c_str());
    Scanner scanner2(input.c_str());

    // Tokens
    ejecutar_scanner(&scanner1, argv[1]);

    // Crear instancias de Parser
    Parser parser(&scanner2);

    // Parsear y generar AST
    Programa* ast = nullptr;

    try {
        ast = parser.parseProgram();
    } catch (const std::exception& e) {
        cerr << "Error al parsear: " << e.what() << endl;
        ast = nullptr;
    }

    PrintVisitor impresion;
    impresion.imprimir(ast);

    return 0;
}
