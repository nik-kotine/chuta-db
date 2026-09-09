#include <iostream>
#include "token.h"

using namespace std;

// -----------------------------
// Constructores
// -----------------------------

// La posicion se inicializa en 0 y la estampa el Scanner al devolver
// el token, en nextToken()

Token::Token(Type type)
    : type(type), text(""), linea(0), columna(0) { }

Token::Token(Type type, char c)
    : type(type), text(string(1, c)), linea(0), columna(0) { }

Token::Token(Type type, const string& source, int first, int last)
    : type(type), text(source.substr(first, last)), linea(0), columna(0) { }

// -----------------------------
// Sobrecarga de operador <<
// -----------------------------

// Para Token por referencia
ostream& operator<<(ostream& outs, const Token& tok) {
    switch (tok.type) {
        case Token::CREATE:      outs << "TOKEN(CREATE, \""      << tok.text << "\")"; break;
        case Token::TABLE:       outs << "TOKEN(TABLE, \""       << tok.text << "\")"; break;
        case Token::INDEX:       outs << "TOKEN(INDEX, \""       << tok.text << "\")"; break;
        case Token::ON:          outs << "TOKEN(ON, \""          << tok.text << "\")"; break;
        case Token::USING:       outs << "TOKEN(USING, \""       << tok.text << "\")"; break;
        case Token::CLUSTERED:   outs << "TOKEN(CLUSTERED, \""   << tok.text << "\")"; break;
        case Token::PRIMARY:     outs << "TOKEN(PRIMARY, \""     << tok.text << "\")"; break;
        case Token::KEY:         outs << "TOKEN(KEY, \""         << tok.text << "\")"; break;

        case Token::HEAP:        outs << "TOKEN(HEAP, \""        << tok.text << "\")"; break;
        case Token::SEQUENTIAL:  outs << "TOKEN(SEQUENTIAL, \""  << tok.text << "\")"; break;
        case Token::BTREE:       outs << "TOKEN(BTREE, \""       << tok.text << "\")"; break;
        case Token::HASH:        outs << "TOKEN(HASH, \""        << tok.text << "\")"; break;

        case Token::INT:         outs << "TOKEN(INT, \""         << tok.text << "\")"; break;
        case Token::FLOAT:       outs << "TOKEN(FLOAT, \""       << tok.text << "\")"; break;
        case Token::BOOL:        outs << "TOKEN(BOOL, \""        << tok.text << "\")"; break;
        case Token::DATE:        outs << "TOKEN(DATE, \""        << tok.text << "\")"; break;
        case Token::VARCHAR:     outs << "TOKEN(VARCHAR, \""     << tok.text << "\")"; break;

        case Token::SELECT:      outs << "TOKEN(SELECT, \""      << tok.text << "\")"; break;
        case Token::FROM:        outs << "TOKEN(FROM, \""        << tok.text << "\")"; break;
        case Token::JOIN:        outs << "TOKEN(JOIN, \""        << tok.text << "\")"; break;
        case Token::WHERE:       outs << "TOKEN(WHERE, \""       << tok.text << "\")"; break;
        case Token::GROUP:       outs << "TOKEN(GROUP, \""       << tok.text << "\")"; break;
        case Token::ORDER:       outs << "TOKEN(ORDER, \""       << tok.text << "\")"; break;
        case Token::BY:          outs << "TOKEN(BY, \""          << tok.text << "\")"; break;
        case Token::ASC:         outs << "TOKEN(ASC, \""         << tok.text << "\")"; break;
        case Token::DESC:        outs << "TOKEN(DESC, \""        << tok.text << "\")"; break;
        case Token::LIMIT:       outs << "TOKEN(LIMIT, \""       << tok.text << "\")"; break;
        case Token::INSERT:      outs << "TOKEN(INSERT, \""      << tok.text << "\")"; break;
        case Token::INTO:        outs << "TOKEN(INTO, \""        << tok.text << "\")"; break;
        case Token::VALUES:      outs << "TOKEN(VALUES, \""      << tok.text << "\")"; break;
        case Token::DELETE:      outs << "TOKEN(DELETE, \""      << tok.text << "\")"; break;

        case Token::BEGIN:       outs << "TOKEN(BEGIN, \""       << tok.text << "\")"; break;
        case Token::END_KW:      outs << "TOKEN(END_KW, \""      << tok.text << "\")"; break;
        case Token::TRANSACTION: outs << "TOKEN(TRANSACTION, \"" << tok.text << "\")"; break;

        case Token::AND:         outs << "TOKEN(AND, \""         << tok.text << "\")"; break;
        case Token::OR:          outs << "TOKEN(OR, \""          << tok.text << "\")"; break;
        case Token::BETWEEN:     outs << "TOKEN(BETWEEN, \""     << tok.text << "\")"; break;
        case Token::TRUE_KW:     outs << "TOKEN(TRUE_KW, \""     << tok.text << "\")"; break;
        case Token::FALSE_KW:    outs << "TOKEN(FALSE_KW, \""    << tok.text << "\")"; break;

        case Token::COUNT:       outs << "TOKEN(COUNT, \""       << tok.text << "\")"; break;
        case Token::SUM:         outs << "TOKEN(SUM, \""         << tok.text << "\")"; break;
        case Token::AVG:         outs << "TOKEN(AVG, \""         << tok.text << "\")"; break;
        case Token::MIN:         outs << "TOKEN(MIN, \""         << tok.text << "\")"; break;
        case Token::MAX:         outs << "TOKEN(MAX, \""         << tok.text << "\")"; break;

        case Token::EQ:          outs << "TOKEN(EQ, \""          << tok.text << "\")"; break;
        case Token::NEQ:         outs << "TOKEN(NEQ, \""         << tok.text << "\")"; break;
        case Token::LT:          outs << "TOKEN(LT, \""          << tok.text << "\")"; break;
        case Token::LE:          outs << "TOKEN(LE, \""          << tok.text << "\")"; break;
        case Token::GT:          outs << "TOKEN(GT, \""          << tok.text << "\")"; break;
        case Token::GE:          outs << "TOKEN(GE, \""          << tok.text << "\")"; break;

        case Token::LPAREN:      outs << "TOKEN(LPAREN, \""      << tok.text << "\")"; break;
        case Token::RPAREN:      outs << "TOKEN(RPAREN, \""      << tok.text << "\")"; break;
        case Token::COMA:        outs << "TOKEN(COMA, \""        << tok.text << "\")"; break;
        case Token::SEMICOL:     outs << "TOKEN(SEMICOL, \""     << tok.text << "\")"; break;
        case Token::STAR:        outs << "TOKEN(STAR, \""        << tok.text << "\")"; break;
        case Token::PUNTO:       outs << "TOKEN(PUNTO, \""       << tok.text << "\")"; break;

        case Token::NUM:         outs << "TOKEN(NUM, \""         << tok.text << "\")"; break;
        case Token::STR:         outs << "TOKEN(STR, \""         << tok.text << "\")"; break;
        case Token::ID:          outs << "TOKEN(ID, \""          << tok.text << "\")"; break;
        case Token::ERR:         outs << "TOKEN(ERR, \""         << tok.text << "\")"; break;

        case Token::END:         outs << "TOKEN(END)"; break;
    }
    return outs;
}

// Para Token puntero
ostream& operator<<(ostream& outs, const Token* tok) {
    if (!tok) return outs << "TOKEN(NULL)";
    return outs << *tok;  // delega al otro
}
