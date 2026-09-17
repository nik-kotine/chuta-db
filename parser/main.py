import sys

from scanner import Scanner, ejecutar_scanner
from parser import Parser
from visitor import PrintVisitor


def main():
    # Verificar numero de argumentos
    if len(sys.argv) != 2:
        print("Numero incorrecto de argumentos.")
        print("Uso: python " + sys.argv[0] + " <archivo_de_entrada>")
        return 1

    # Abrir archivo de entrada
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as infile:
            input_text = infile.read()
    except OSError:
        print("No se pudo abrir el archivo: " + sys.argv[1])
        return 1

    # Crear instancias de Scanner
    scanner1 = Scanner(input_text)
    scanner2 = Scanner(input_text)

    # Tokens
    ejecutar_scanner(scanner1, sys.argv[1])

    # Crear instancia de Parser
    parser = Parser(scanner2)

    # Parsear y generar AST
    ast = None

    try:
        ast = parser.parse_program()
    except Exception as e:
        print("Error al parsear: " + str(e), file=sys.stderr)
        ast = None

    impresion = PrintVisitor()
    impresion.imprimir(ast)

    return 0


if __name__ == "__main__":
    sys.exit(main())
