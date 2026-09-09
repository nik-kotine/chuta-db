#include <iostream>
#include <stdexcept>
#include <string>
#include "token.h"
#include "scanner.h"
#include "ast.h"
#include "parser.h"

using namespace std;

// =============================
// Métodos de la clase Parser
// =============================

Parser::Parser(Scanner* sc) : scanner(sc) {
    previous = nullptr;
    current = scanner->nextToken();
    if (current->type == Token::ERR) {
        throw runtime_error("Error léxico en linea " + to_string(current->linea)
                            + ", columna " + to_string(current->columna)
                            + ": caracter no reconocido '" + current->text + "'");
    }
}

bool Parser::match(Token::Type ttype) {
    if (check(ttype)) {
        advance();
        return true;
    }
    return false;
}

bool Parser::check(Token::Type ttype) {
    if (isAtEnd()) return false;
    return current->type == ttype;
}

bool Parser::advance() {
    if (!isAtEnd()) {
        Token* temp = current;
        if (previous) delete previous;
        current = scanner->nextToken();
        previous = temp;

        if (check(Token::ERR)) {
            throw runtime_error("Error léxico en linea " + to_string(current->linea)
                                + ", columna " + to_string(current->columna)
                                + ": caracter no reconocido '" + current->text + "'");
        }
        return true;
    }
    return false;
}

bool Parser::isAtEnd() {
    return (current->type == Token::END);
}

// La posicion sale del token actual, que es el que no encajo con la regla
void Parser::error(const string& mensaje) {
    throw runtime_error("Error sintáctico en linea " + to_string(current->linea)
                        + ", columna " + to_string(current->columna)
                        + ": " + mensaje);
}

void Parser::errorSemantico(const string& mensaje) {
    throw runtime_error("Error semántico en linea " + to_string(current->linea)
                        + ", columna " + to_string(current->columna)
                        + ": " + mensaje);
}


// =============================
// Reglas gramaticales
// =============================

Programa* Parser::parseProgram() {
    Programa* ast = parseP();
    if (!isAtEnd()) {
        error("se esperaba ';' o el fin de la entrada");
    }
    cout << "Parseo exitoso" << endl;
    return ast;
}


// P ::= Stmt {; Stmt}* [;]
Programa* Parser::parseP() {
    Programa* p = new Programa();

    p->slist.push_back(parsestmt());
    while (match(Token::SEMICOL)) {
        if (isAtEnd()) break;          // ';' final
        p->slist.push_back(parsestmt());
    }
    return p;
}


// Stmt ::= CreateTable | CreateIndex | Select | Insert | Delete | Transaction
Stmt* Parser::parsestmt() {
    if (check(Token::CREATE)) {
        return parsecreate();
    }
    else if (check(Token::SELECT)) {
        return parseselect();
    }
    else if (check(Token::INSERT)) {
        return parseinsert();
    }
    else if (check(Token::DELETE)) {
        return parsedelete();
    }
    else if (check(Token::BEGIN) || check(Token::END_KW)) {
        return parsetransaction();
    }
    else {
        error("se esperaba una sentencia");
    }
}


Stmt* Parser::parsecreate() {
    match(Token::CREATE);
    if (check(Token::TABLE)) {
        return parsecreatetable();
    }
    else if (check(Token::INDEX)) {
        return parsecreateindex();
    }
    else {
        error("se esperaba TABLE o INDEX");
    }
}


// CreateTable ::= CREATE TABLE id ( ColDec {, ColDec}* ) USING FileOrg
Stmt* Parser::parsecreatetable() {
    match(Token::TABLE);
    CreateTableStmt* ct = new CreateTableStmt();

    if (!match(Token::ID)) error("se esperaba el nombre de la tabla");
    ct->tabla = previous->text;

    if (!match(Token::LPAREN)) error("se esperaba (");
    ct->columnas.push_back(parsecolumndec());
    while (match(Token::COMA)) {
        ct->columnas.push_back(parsecolumndec());
    }
    if (!match(Token::RPAREN)) error("se esperaba )");

    if (!match(Token::USING)) error("se esperaba USING");
    if (match(Token::HEAP)) {
        ct->org = HEAP_ORG;
    }
    else if (match(Token::SEQUENTIAL)) {
        ct->org = SEQUENTIAL_ORG;
    }
    else {
        error("se esperaba HEAP o SEQUENTIAL");
    }
    return ct;
}


// ColDec ::= id Type [PRIMARY KEY]
ColumnDec* Parser::parsecolumndec() {
    ColumnDec* cd = new ColumnDec();

    if (!match(Token::ID)) error("se esperaba el nombre de la columna");
    cd->nombre = previous->text;

    if (match(Token::INT)) {
        cd->tipo = INT_TYPE;
    }
    else if (match(Token::FLOAT)) {
        cd->tipo = FLOAT_TYPE;
    }
    else if (match(Token::BOOL)) {
        cd->tipo = BOOL_TYPE;
    }
    else if (match(Token::DATE)) {
        cd->tipo = DATE_TYPE;
    }
    else if (match(Token::VARCHAR)) {
        cd->tipo = VARCHAR_TYPE;
        if (!match(Token::LPAREN)) error("se esperaba (");
        if (!match(Token::NUM)) error("se esperaba la longitud del VARCHAR");
        cd->longitud = stoi(previous->text);
        if (!match(Token::RPAREN)) error("se esperaba )");
    }
    else {
        error("se esperaba un tipo de dato");
    }

    if (match(Token::PRIMARY)) {
        if (!match(Token::KEY)) error("se esperaba KEY");
        cd->primaryKey = true;
    }
    return cd;
}


// CreateIndex ::= CREATE INDEX ON id ( id ) USING IndexKind [CLUSTERED]
Stmt* Parser::parsecreateindex() {
    match(Token::INDEX);
    CreateIndexStmt* ci = new CreateIndexStmt();

    if (!match(Token::ON)) error("se esperaba ON");
    if (!match(Token::ID)) error("se esperaba el nombre de la tabla");
    ci->tabla = previous->text;

    if (!match(Token::LPAREN)) error("se esperaba (");
    if (!match(Token::ID)) error("se esperaba la columna a indexar");
    ci->columna = previous->text;
    if (!match(Token::RPAREN)) error("se esperaba )");

    if (!match(Token::USING)) error("se esperaba USING");
    if (match(Token::BTREE)) {
        ci->tipo = BTREE_IDX;
    }
    else if (match(Token::HASH)) {
        ci->tipo = HASH_IDX;
    }
    else {
        error("se esperaba BTREE o HASH");
    }

    if (match(Token::CLUSTERED)) {
        ci->clustered = true;
    }

    // Un indice hash agrupado no tiene sentido: el hash no preserva el
    // orden, asi que no puede definir el orden fisico de los registros
    if (ci->clustered && ci->tipo == HASH_IDX) {
        errorSemantico("un indice HASH no puede ser CLUSTERED");
    }
    return ci;
}


// Select ::= SELECT SelList FROM id [Join] [Where] [GroupBy] [OrderBy] [Limit]
Stmt* Parser::parseselect() {
    match(Token::SELECT);
    SelectStmt* s = new SelectStmt();

    if (check(Token::STAR)) {
        match(Token::STAR);
        s->selectAll = true;
    }
    else {
        s->proyeccion.push_back(parseselectitem());
        while (match(Token::COMA)) {
            s->proyeccion.push_back(parseselectitem());
        }
    }

    if (!match(Token::FROM)) error("se esperaba FROM");
    if (!match(Token::ID)) error("se esperaba el nombre de la tabla");
    s->tabla = previous->text;

    if (check(Token::JOIN)) {
        s->join = parsejoin();
    }

    if (match(Token::WHERE)) {
        s->condicion = parseCOND();
    }

    if (match(Token::GROUP)) {
        if (!match(Token::BY)) error("se esperaba BY");
        s->groupBy = parsecolref();
    }

    if (match(Token::ORDER)) {
        if (!match(Token::BY)) error("se esperaba BY");
        s->orderBy = parsecolref();
        if (match(Token::ASC)) {
            s->direccion = ASC_DIR;
        }
        else if (match(Token::DESC)) {
            s->direccion = DESC_DIR;
        }
    }

    if (match(Token::LIMIT)) {
        if (!match(Token::NUM)) error("se esperaba el límite de filas");
        s->limite = stoi(previous->text);
        s->haylimite = true;
    }
    return s;
}


// Proj ::= ColRef | AggFun ( ColRef | * )
SelectItem* Parser::parseselectitem() {
    SelectItem* item = new SelectItem();

    if (match(Token::COUNT)) {
        item->agg = COUNT_AGG;
    }
    else if (match(Token::SUM)) {
        item->agg = SUM_AGG;
    }
    else if (match(Token::AVG)) {
        item->agg = AVG_AGG;
    }
    else if (match(Token::MIN)) {
        item->agg = MIN_AGG;
    }
    else if (match(Token::MAX)) {
        item->agg = MAX_AGG;
    }

    if (item->agg != NONE_AGG) {
        if (!match(Token::LPAREN)) error("se esperaba (");
        if (match(Token::STAR)) {
            // Solo COUNT admite '*' como argumento
            if (item->agg != COUNT_AGG) {
                errorSemantico("solo COUNT admite * como argumento");
            }
            item->estrella = true;
        }
        else {
            item->columna = parsecolref();
        }
        if (!match(Token::RPAREN)) error("se esperaba )");
        return item;
    }

    item->columna = parsecolref();
    return item;
}


// Join ::= JOIN id ON ColRef = ColRef
JoinClause* Parser::parsejoin() {
    match(Token::JOIN);
    JoinClause* j = new JoinClause();

    if (!match(Token::ID)) error("se esperaba la tabla del JOIN");
    j->tabla = previous->text;

    if (!match(Token::ON)) error("se esperaba ON");
    j->izquierda = parsecolref();
    if (!match(Token::EQ)) error("se esperaba = en el JOIN");
    j->derecha = parsecolref();
    return j;
}


// Insert ::= INSERT INTO id VALUES ( Value {, Value}* )
Stmt* Parser::parseinsert() {
    match(Token::INSERT);
    if (!match(Token::INTO)) error("se esperaba INTO");
    InsertStmt* ins = new InsertStmt();

    if (!match(Token::ID)) error("se esperaba el nombre de la tabla");
    ins->tabla = previous->text;

    if (!match(Token::VALUES)) error("se esperaba VALUES");
    if (!match(Token::LPAREN)) error("se esperaba (");
    ins->valores.push_back(parsevalue());
    while (match(Token::COMA)) {
        ins->valores.push_back(parsevalue());
    }
    if (!match(Token::RPAREN)) error("se esperaba )");
    return ins;
}


// Delete ::= DELETE FROM id WHERE Cond
Stmt* Parser::parsedelete() {
    match(Token::DELETE);
    if (!match(Token::FROM)) error("se esperaba FROM");
    DeleteStmt* del = new DeleteStmt();

    if (!match(Token::ID)) error("se esperaba el nombre de la tabla");
    del->tabla = previous->text;

    // WHERE obligatorio: evita borrar la tabla completa por descuido
    if (!match(Token::WHERE)) error("DELETE requiere WHERE");
    del->condicion = parseCOND();
    return del;
}


// Transaction ::= BEGIN TRANSACTION | END TRANSACTION
Stmt* Parser::parsetransaction() {
    bool esBegin;
    if (match(Token::BEGIN)) {
        esBegin = true;
    }
    else if (match(Token::END_KW)) {
        esBegin = false;
    }
    else {
        error("se esperaba BEGIN o END");
    }
    if (!match(Token::TRANSACTION)) error("se esperaba TRANSACTION");
    return new TransactionStmt(esBegin);
}


// Cond ::= AndCond {OR AndCond}*
Cond* Parser::parseCOND() {
    Cond* l = parseAND();
    if (!check(Token::OR)) return l;

    OrCond* o = new OrCond();
    o->condiciones.push_back(l);
    while (match(Token::OR)) {
        o->condiciones.push_back(parseAND());
    }
    return o;
}


// AndCond ::= Pred {AND Pred}*
Cond* Parser::parseAND() {
    Cond* l = parsePRED();
    if (!check(Token::AND)) return l;

    AndCond* a = new AndCond();
    a->condiciones.push_back(l);
    while (match(Token::AND)) {
        a->condiciones.push_back(parsePRED());
    }
    return a;
}


// Pred ::= ColRef RelOp Value | ColRef BETWEEN Value AND Value | ( Cond )
Cond* Parser::parsePRED() {
    if (match(Token::LPAREN)) {
        Cond* c = parseCOND();
        if (!match(Token::RPAREN)) error("se esperaba )");
        return c;
    }

    ColRef* col = parsecolref();

    if (match(Token::BETWEEN)) {
        Value* inf = parsevalue();
        if (!match(Token::AND)) error("se esperaba AND en BETWEEN");
        Value* sup = parsevalue();
        return new BetweenCond(col, inf, sup);
    }

    RelOp op;
    if (match(Token::EQ)) {
        op = EQ_OP;
    }
    else if (match(Token::NEQ)) {
        op = NEQ_OP;
    }
    else if (match(Token::LT)) {
        op = LT_OP;
    }
    else if (match(Token::LE)) {
        op = LE_OP;
    }
    else if (match(Token::GT)) {
        op = GT_OP;
    }
    else if (match(Token::GE)) {
        op = GE_OP;
    }
    else {
        error("se esperaba un operador relacional o BETWEEN");
    }
    Value* v = parsevalue();
    return new CompareCond(col, op, v);
}


// ColRef ::= id | id . id
ColRef* Parser::parsecolref() {
    if (!match(Token::ID)) error("se esperaba el nombre de una columna");
    string primero = previous->text;

    ColRef* c = new ColRef();
    if (match(Token::PUNTO)) {
        if (!match(Token::ID)) error("se esperaba la columna después del .");
        c->tabla = primero;
        c->columna = previous->text;
    }
    else {
        c->columna = primero;
    }
    return c;
}


// Value ::= num | str | TRUE | FALSE
Value* Parser::parsevalue() {
    if (match(Token::NUM)) {
        string texto = previous->text;
        if (texto.find('.') != string::npos) {
            return new FloatValue(stod(texto));
        }
        return new IntValue(stoi(texto));
    }
    else if (match(Token::STR)) {
        return new StrValue(previous->text);
    }
    else if (match(Token::TRUE_KW)) {
        return new BoolValue(true);
    }
    else if (match(Token::FALSE_KW)) {
        return new BoolValue(false);
    }
    else {
        error("se esperaba un valor literal");
    }
}
