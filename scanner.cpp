#include <iostream>
#include <cstring>
#include <fstream>
#include "token.h"
#include "scanner.h"

using namespace std;

// -----------------------------
// Constructor
// -----------------------------
Scanner::Scanner(const char* s): input(s), first(0), current(0), linea(1), columna(1) {
    }

// -----------------------------
// Funciones auxiliares
// -----------------------------

bool is_white_space(char c) {
    return c == ' ' || c == '\n' || c == '\r' || c == '\t';
}

// SQL no distingue mayusculas en las palabras reservadas, asi que el
// lexema se normaliza antes de compararlo
string a_mayusculas(const string& s) {
    string r = s;
    for (int i = 0; i < (int)r.length(); i++)
        r[i] = toupper(r[i]);
    return r;
}

// consume un caracter. Es el unico lugar donde se mueve current, para que
// linea y columna nunca queden desincronizadas
void Scanner::avanzar() {
    if (input[current] == '\n') {
        linea++;
        columna = 1;
    }
    else {
        columna++;
    }
    current++;
}

// salta espacios en blanco y comentarios de linea (--)
void Scanner::saltarEspacios() {
    while (current < (int)input.length()) {
        if (is_white_space(input[current])) {
            avanzar();
        }
        else if (input[current] == '-' && current + 1 < (int)input.length()
                 && input[current + 1] == '-') {
            while (current < (int)input.length() && input[current] != '\n')
                avanzar();
        }
        else break;
    }
}

// -----------------------------
// nextToken: obtiene el siguiente token
// -----------------------------

Token* Scanner::nextToken() {
    Token* token;

    saltarEspacios();

    // Posicion donde empieza el token, antes de consumir nada
    int linea_tok = linea;
    int columna_tok = columna;

    // Fin de la entrada
    if (current >= (int)input.length()) {
        token = new Token(Token::END);
        token->linea = linea_tok;
        token->columna = columna_tok;
        return token;
    }

    char c = input[current];

    first = current;

    // Numeros (enteros y reales)
    if (isdigit(c)) {
        avanzar();
        while (current < (int)input.length() && isdigit(input[current]))
            avanzar();
        // El punto solo es decimal si le sigue un digito; asi no se
        // confunde 1.5 con la notacion tabla.columna
        if (current + 1 < (int)input.length() && input[current] == '.'
            && isdigit(input[current + 1])) {
            avanzar();
            while (current < (int)input.length() && isdigit(input[current]))
                avanzar();
        }
        token = new Token(Token::NUM, input, first, current - first);
    }
    // Palabras reservadas e identificadores
    else if (isalpha(c) || c == '_') {
        avanzar();
        while (current < (int)input.length()
               && (isalnum(input[current]) || input[current] == '_'))
            avanzar();
        string lexema = a_mayusculas(input.substr(first, current - first));

        // DDL
        if (lexema=="CREATE") token = new Token(Token::CREATE, input, first, current - first);
        else if (lexema=="TABLE") token = new Token(Token::TABLE, input, first, current - first);
        else if (lexema=="INDEX") token = new Token(Token::INDEX, input, first, current - first);
        else if (lexema=="ON") token = new Token(Token::ON, input, first, current - first);
        else if (lexema=="USING") token = new Token(Token::USING, input, first, current - first);
        else if (lexema=="CLUSTERED") token = new Token(Token::CLUSTERED, input, first, current - first);
        else if (lexema=="PRIMARY") token = new Token(Token::PRIMARY, input, first, current - first);
        else if (lexema=="KEY") token = new Token(Token::KEY, input, first, current - first);

        // porganizacion del archivo (2.1.1)
        else if (lexema=="HEAP") token = new Token(Token::HEAP, input, first, current - first);
        else if (lexema=="SEQUENTIAL") token = new Token(Token::SEQUENTIAL, input, first, current - first);

        // tipos de indice (2.1.2)
        else if (lexema=="BTREE") token = new Token(Token::BTREE, input, first, current - first);
        else if (lexema=="HASH") token = new Token(Token::HASH, input, first, current - first);

        // tipos de dato
        else if (lexema=="INT") token = new Token(Token::INT, input, first, current - first);
        else if (lexema=="FLOAT") token = new Token(Token::FLOAT, input, first, current - first);
        else if (lexema=="BOOL") token = new Token(Token::BOOL, input, first, current - first);
        else if (lexema=="DATE") token = new Token(Token::DATE, input, first, current - first);
        else if (lexema=="VARCHAR") token = new Token(Token::VARCHAR, input, first, current - first);

        //dml
        else if (lexema=="SELECT") token = new Token(Token::SELECT, input, first, current - first);
        else if (lexema=="FROM") token = new Token(Token::FROM, input, first, current - first);
        else if (lexema=="JOIN") token = new Token(Token::JOIN, input, first, current - first);
        else if (lexema=="WHERE") token = new Token(Token::WHERE, input, first, current - first);
        else if (lexema=="GROUP") token = new Token(Token::GROUP, input, first, current - first);
        else if (lexema=="ORDER") token = new Token(Token::ORDER, input, first, current - first);
        else if (lexema=="BY") token = new Token(Token::BY, input, first, current - first);
        else if (lexema=="ASC") token = new Token(Token::ASC, input, first, current - first);
        else if (lexema=="DESC") token = new Token(Token::DESC, input, first, current - first);
        else if (lexema=="LIMIT") token = new Token(Token::LIMIT, input, first, current - first);
        else if (lexema=="INSERT") token = new Token(Token::INSERT, input, first, current - first);
        else if (lexema=="INTO") token = new Token(Token::INTO, input, first, current - first);
        else if (lexema=="VALUES") token = new Token(Token::VALUES, input, first, current - first);
        else if (lexema=="DELETE") token = new Token(Token::DELETE, input, first, current - first);

        // transacciones (2.1.4)
        else if (lexema=="BEGIN") token = new Token(Token::BEGIN, input, first, current - first);
        else if (lexema=="END") token = new Token(Token::END_KW, input, first, current - first);
        else if (lexema=="TRANSACTION") token = new Token(Token::TRANSACTION, input, first, current - first);

        // condiciones
        else if (lexema=="AND") token = new Token(Token::AND, input, first, current - first);
        else if (lexema=="OR") token = new Token(Token::OR, input, first, current - first);
        else if (lexema=="BETWEEN") token = new Token(Token::BETWEEN, input, first, current - first);
        else if (lexema=="TRUE") token = new Token(Token::TRUE_KW, input, first, current - first);
        else if (lexema=="FALSE") token = new Token(Token::FALSE_KW, input, first, current - first);

        // funciones de agregacoin
        else if (lexema=="COUNT") token = new Token(Token::COUNT, input, first, current - first);
        else if (lexema=="SUM") token = new Token(Token::SUM, input, first, current - first);
        else if (lexema=="AVG") token = new Token(Token::AVG, input, first, current - first);
        else if (lexema=="MIN") token = new Token(Token::MIN, input, first, current - first);
        else if (lexema=="MAX") token = new Token(Token::MAX, input, first, current - first);

        else token = new Token(Token::ID, input, first, current - first);
    }
    // Cadenas entre comillas simples ('' representa una comilla literal)
    else if (c == '\'') {
        avanzar();
        string texto = "";
        bool cerrada = false;
        while (current < (int)input.length() && input[current] != '\n') {
            if (input[current] == '\'') {
                if (current + 1 < (int)input.length() && input[current+1] == '\'') {
                    texto += '\'';
                    avanzar();
                    avanzar();
                    continue;
                }
                avanzar();
                cerrada = true;
                break;
            }
            texto += input[current];
            avanzar();
        }
        if (!cerrada) {
            token = new Token(Token::ERR, '\'');
        }
        else {
            token = new Token(Token::STR);
            token->text = texto;
        }
    }
    // operadores y signos de puntuacion
    else if (strchr(",;().*=<>!", c)) {
        switch (c) {
            case ',': token = new Token(Token::COMA,    c); break;
            case ';': token = new Token(Token::SEMICOL, c); break;
            case '(': token = new Token(Token::LPAREN,  c); break;
            case ')': token = new Token(Token::RPAREN,  c); break;
            case '.': token = new Token(Token::PUNTO,   c); break;
            case '*': token = new Token(Token::STAR,    c); break;
            case '=': token = new Token(Token::EQ,      c); break;
            case '<':
            if (input[current+1]=='=')
            {
                avanzar();
                token = new Token(Token::LE, input, first, current + 1 - first);
            }
            else if (input[current+1]=='>')
            {
                avanzar();
                token = new Token(Token::NEQ, input, first, current + 1 - first);
            }
            else{
                token = new Token(Token::LT, c);
            }
            break;
            case '>':
            if (input[current+1]=='=')
            {
                avanzar();
                token = new Token(Token::GE, input, first, current + 1 - first);
            }
            else{
                token = new Token(Token::GT, c);
            }
            break;
            case '!':
            if (input[current+1]=='=')
            {
                avanzar();
                token = new Token(Token::NEQ, input, first, current + 1 - first);
            }
            else{
                token = new Token(Token::ERR, c);
            }
            break;
            default: token = new Token(Token::ERR, c); break;
        }
        avanzar();
    }

    // Caracter invalido
    else {
        token = new Token(Token::ERR, c);
        avanzar();
    }

    // Estampar la posicion donde empezo el token
    token->linea = linea_tok;
    token->columna = columna_tok;
    return token;
}

// -----------------------------
// Destructor
// -----------------------------
Scanner::~Scanner() { }

// -----------------------------
// Funcion de prueba
// -----------------------------

void ejecutar_scanner(Scanner* scanner, const string& InputFile) {
    Token* tok;

    // Crear nombre para archivo de salida
    string OutputFileName = InputFile;
    size_t pos = OutputFileName.find_last_of(".");
    if (pos != string::npos) {
        OutputFileName = OutputFileName.substr(0, pos);
    }
    OutputFileName += "_tokens.txt";

    ofstream outFile(OutputFileName);
    if (!outFile.is_open()) {
        cerr << "Error: no se pudo abrir el archivo " << OutputFileName << endl;
        return;
    }

    outFile << "Scanner\n" << endl;

    while (true) {
        tok = scanner->nextToken();

        if (tok->type == Token::END) {
            outFile << *tok << endl;
            delete tok;
            outFile << "\nScanner exitoso" << endl << endl;
            outFile.close();
            return;
        }

        if (tok->type == Token::ERR) {
            outFile << *tok << endl;
            outFile << "Caracter invalido en linea " << tok->linea
                    << ", columna " << tok->columna << endl << endl;
            delete tok;
            outFile << "Scanner no exitoso" << endl << endl;
            outFile.close();
            return;
        }

        outFile << *tok << endl;
        delete tok;
    }
}
