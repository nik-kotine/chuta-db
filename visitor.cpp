#include <iostream>
#include "ast.h"
#include "visitor.h"

using namespace std;

///////////////////////////////////////////////////////////////////////////////////

void IntValue::accept(Visitor* visitor) {
    visitor->visit(this);
}

void FloatValue::accept(Visitor* visitor) {
    visitor->visit(this);
}

void StrValue::accept(Visitor* visitor) {
    visitor->visit(this);
}

void BoolValue::accept(Visitor* visitor) {
    visitor->visit(this);
}

void ColRef::accept(Visitor* visitor) {
    visitor->visit(this);
}

void OrCond::accept(Visitor* visitor) {
    visitor->visit(this);
}

void AndCond::accept(Visitor* visitor) {
    visitor->visit(this);
}

void CompareCond::accept(Visitor* visitor) {
    visitor->visit(this);
}

void BetweenCond::accept(Visitor* visitor) {
    visitor->visit(this);
}

void SelectItem::accept(Visitor* visitor) {
    visitor->visit(this);
}

void JoinClause::accept(Visitor* visitor) {
    visitor->visit(this);
}

void ColumnDec::accept(Visitor* visitor) {
    visitor->visit(this);
}

void CreateTableStmt::accept(Visitor* visitor) {
    visitor->visit(this);
}

void CreateIndexStmt::accept(Visitor* visitor) {
    visitor->visit(this);
}

void SelectStmt::accept(Visitor* visitor) {
    visitor->visit(this);
}

void InsertStmt::accept(Visitor* visitor) {
    visitor->visit(this);
}

void DeleteStmt::accept(Visitor* visitor) {
    visitor->visit(this);
}

void TransactionStmt::accept(Visitor* visitor) {
    visitor->visit(this);
}

void Programa::accept(Visitor* visitor) {
    visitor->visit(this);
}

///////////////////////////////////////////////////////////////////////////////////

// -----------------------------
// Valores literales
// -----------------------------

void PrintVisitor::visit(IntValue* v) {
    cout << v->value;
}

void PrintVisitor::visit(FloatValue* v) {
    cout << v->value;
}

void PrintVisitor::visit(StrValue* v) {
    cout << "'" << v->value << "'";
}

void PrintVisitor::visit(BoolValue* v) {
    cout << (v->value ? "TRUE" : "FALSE");
}

void PrintVisitor::visit(ColRef* c) {
    if (c->tabla != "") cout << c->tabla << ".";
    cout << c->columna;
}

// -----------------------------
// Condiciones
// -----------------------------

void PrintVisitor::visit(OrCond* c) {
    cout << "(";
    bool primero = true;
    for (list<Cond*>::iterator it = c->condiciones.begin(); it != c->condiciones.end(); ++it) {
        if (!primero) cout << " OR ";
        (*it)->accept(this);
        primero = false;
    }
    cout << ")";
}

void PrintVisitor::visit(AndCond* c) {
    cout << "(";
    bool primero = true;
    for (list<Cond*>::iterator it = c->condiciones.begin(); it != c->condiciones.end(); ++it) {
        if (!primero) cout << " AND ";
        (*it)->accept(this);
        primero = false;
    }
    cout << ")";
}

void PrintVisitor::visit(CompareCond* c) {
    c->columna->accept(this);
    cout << " " << Cond::relopToChar(c->op) << " ";
    c->valor->accept(this);
}

void PrintVisitor::visit(BetweenCond* c) {
    c->columna->accept(this);
    cout << " BETWEEN ";
    c->inferior->accept(this);
    cout << " AND ";
    c->superior->accept(this);
}

// -----------------------------
// Piezas del SELECT
// -----------------------------

void PrintVisitor::visit(SelectItem* item) {
    if (item->agg == NONE_AGG) {
        if (item->estrella) cout << "*";
        else item->columna->accept(this);
        return;
    }
    switch (item->agg) {
        case COUNT_AGG: cout << "COUNT("; break;
        case SUM_AGG:   cout << "SUM(";   break;
        case AVG_AGG:   cout << "AVG(";   break;
        case MIN_AGG:   cout << "MIN(";   break;
        case MAX_AGG:   cout << "MAX(";   break;
        default: break;
    }
    if (item->estrella) cout << "*";
    else item->columna->accept(this);
    cout << ")";
}

void PrintVisitor::visit(JoinClause* j) {
    cout << " JOIN " << j->tabla << " ON ";
    j->izquierda->accept(this);
    cout << " = ";
    j->derecha->accept(this);
}

void PrintVisitor::visit(ColumnDec* cd) {
    cout << cd->nombre << " ";
    switch (cd->tipo) {
        case INT_TYPE:     cout << "INT";   break;
        case FLOAT_TYPE:   cout << "FLOAT"; break;
        case BOOL_TYPE:    cout << "BOOL";  break;
        case DATE_TYPE:    cout << "DATE";  break;
        case VARCHAR_TYPE: cout << "VARCHAR(" << cd->longitud << ")"; break;
    }
    if (cd->primaryKey) cout << " PRIMARY KEY";
}

// -----------------------------
// Sentencias
// -----------------------------

void PrintVisitor::visit(CreateTableStmt* stm) {
    cout << "CREATE TABLE " << stm->tabla << " (";
    bool primero = true;
    for (list<ColumnDec*>::iterator it = stm->columnas.begin(); it != stm->columnas.end(); ++it) {
        if (!primero) cout << ", ";
        (*it)->accept(this);
        primero = false;
    }
    cout << ") USING " << (stm->org == HEAP_ORG ? "HEAP" : "SEQUENTIAL");
}

void PrintVisitor::visit(CreateIndexStmt* stm) {
    cout << "CREATE INDEX ON " << stm->tabla << " (" << stm->columna << ")";
    cout << " USING " << (stm->tipo == BTREE_IDX ? "BTREE" : "HASH");
    if (stm->clustered) cout << " CLUSTERED";
}

void PrintVisitor::visit(SelectStmt* stm) {
    cout << "SELECT ";
    if (stm->selectAll) {
        cout << "*";
    }
    else {
        bool primero = true;
        for (list<SelectItem*>::iterator it = stm->proyeccion.begin(); it != stm->proyeccion.end(); ++it) {
            if (!primero) cout << ", ";
            (*it)->accept(this);
            primero = false;
        }
    }
    cout << " FROM " << stm->tabla;

    if (stm->join) stm->join->accept(this);

    if (stm->condicion) {
        cout << " WHERE ";
        stm->condicion->accept(this);
    }
    if (stm->groupBy) {
        cout << " GROUP BY ";
        stm->groupBy->accept(this);
    }
    if (stm->orderBy) {
        cout << " ORDER BY ";
        stm->orderBy->accept(this);
        cout << (stm->direccion == ASC_DIR ? " ASC" : " DESC");
    }
    if (stm->haylimite) {
        cout << " LIMIT " << stm->limite;
    }
}

void PrintVisitor::visit(InsertStmt* stm) {
    cout << "INSERT INTO " << stm->tabla << " VALUES (";
    bool primero = true;
    for (list<Value*>::iterator it = stm->valores.begin(); it != stm->valores.end(); ++it) {
        if (!primero) cout << ", ";
        (*it)->accept(this);
        primero = false;
    }
    cout << ")";
}

void PrintVisitor::visit(DeleteStmt* stm) {
    cout << "DELETE FROM " << stm->tabla << " WHERE ";
    stm->condicion->accept(this);
}

void PrintVisitor::visit(TransactionStmt* stm) {
    cout << (stm->esBegin ? "BEGIN TRANSACTION" : "END TRANSACTION");
}

// -----------------------------
// Programa
// -----------------------------

void PrintVisitor::visit(Programa* program) {
    for (list<Stmt*>::iterator it = program->slist.begin(); it != program->slist.end(); ++it) {
        (*it)->accept(this);
        cout << ";" << endl;
    }
}

void PrintVisitor::imprimir(Programa* program) {
    if (program == nullptr) return;
    cout << "IMPRIMIR" << endl << endl;
    program->accept(this);
    cout << endl;
}
