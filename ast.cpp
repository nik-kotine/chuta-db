#include "ast.h"

using namespace std;

// -----------------------------
// Destructores puros de las clases abstractas
// -----------------------------

Value::~Value() { }
Cond::~Cond() { }
Stmt::~Stmt() { }

// -----------------------------
// Conversion operador -> string
// -----------------------------

string Cond::relopToChar(RelOp op) {
    switch (op) {
        case EQ_OP:  return "=";
        case NEQ_OP: return "!=";
        case LT_OP:  return "<";
        case LE_OP:  return "<=";
        case GT_OP:  return ">";
        case GE_OP:  return ">=";
    }
    return "?";
}

// -----------------------------
// Valores literales
// -----------------------------

IntValue::IntValue(int v): value(v) { }
IntValue::~IntValue() { }

FloatValue::FloatValue(double v): value(v) { }
FloatValue::~FloatValue() { }

StrValue::StrValue(string v): value(v) { }
StrValue::~StrValue() { }

BoolValue::BoolValue(bool v): value(v) { }
BoolValue::~BoolValue() { }

// -----------------------------
// Referencia a columna
// -----------------------------

ColRef::ColRef(): tabla(""), columna("") { }
ColRef::ColRef(string tabla, string columna): tabla(tabla), columna(columna) { }
ColRef::~ColRef() { }

// -----------------------------
// Condiciones
// -----------------------------

OrCond::OrCond() { }
OrCond::~OrCond() {
    for (list<Cond*>::iterator it = condiciones.begin(); it != condiciones.end(); ++it)
        delete *it;
}

AndCond::AndCond() { }
AndCond::~AndCond() {
    for (list<Cond*>::iterator it = condiciones.begin(); it != condiciones.end(); ++it)
        delete *it;
}

CompareCond::CompareCond(ColRef* c, RelOp op, Value* v)
    : columna(c), op(op), valor(v) { }
CompareCond::~CompareCond() {
    delete columna;
    delete valor;
}

BetweenCond::BetweenCond(ColRef* c, Value* inf, Value* sup)
    : columna(c), inferior(inf), superior(sup) { }
BetweenCond::~BetweenCond() {
    delete columna;
    delete inferior;
    delete superior;
}

// -----------------------------
// Piezas del SELECT
// -----------------------------

SelectItem::SelectItem(): agg(NONE_AGG), estrella(false), columna(nullptr) { }
SelectItem::~SelectItem() {
    delete columna;
}

JoinClause::JoinClause(): tabla(""), izquierda(nullptr), derecha(nullptr) { }
JoinClause::~JoinClause() {
    delete izquierda;
    delete derecha;
}

ColumnDec::ColumnDec(): nombre(""), tipo(INT_TYPE), longitud(0) { }
ColumnDec::~ColumnDec() { }

// -----------------------------
// Sentencias
// -----------------------------

CreateTableStmt::CreateTableStmt(): tabla(""), org(HEAP_ORG) { }
CreateTableStmt::~CreateTableStmt() {
    for (list<ColumnDec*>::iterator it = columnas.begin(); it != columnas.end(); ++it)
        delete *it;
}

CreateIndexStmt::CreateIndexStmt(): tabla(""), columna(""), tipo(BTREE_IDX) { }
CreateIndexStmt::~CreateIndexStmt() { }

SelectStmt::SelectStmt(): tabla("") { }
SelectStmt::~SelectStmt() {
    for (list<SelectItem*>::iterator it = proyeccion.begin(); it != proyeccion.end(); ++it)
        delete *it;
    delete join;
    delete condicion;
    delete groupBy;
    delete orderBy;
}

InsertStmt::InsertStmt(): tabla("") { }
InsertStmt::~InsertStmt() {
    for (list<Value*>::iterator it = valores.begin(); it != valores.end(); ++it)
        delete *it;
}

DeleteStmt::DeleteStmt(): tabla("") { }
DeleteStmt::~DeleteStmt() {
    delete condicion;
}

TransactionStmt::TransactionStmt(bool esBegin): esBegin(esBegin) { }
TransactionStmt::~TransactionStmt() { }

// -----------------------------
// Programa
// -----------------------------

Programa::Programa() { }
Programa::~Programa() {
    for (list<Stmt*>::iterator it = slist.begin(); it != slist.end(); ++it)
        delete *it;
}
