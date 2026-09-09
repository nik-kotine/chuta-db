#ifndef AST_H
#define AST_H

#include <string>
#include <list>
#include <ostream>

using namespace std;

class Visitor;

// Organizacion del archivo de datos (2.1.1)
enum FileOrg {
    HEAP_ORG,
    SEQUENTIAL_ORG
};

// Estructura del indice (2.1.2)
enum IndexKind {
    BTREE_IDX,
    HASH_IDX
};

// Tipos de dato del catalogo
enum DataType {
    INT_TYPE,
    FLOAT_TYPE,
    BOOL_TYPE,
    DATE_TYPE,
    VARCHAR_TYPE
};

// Operadores relacionales soportados
enum RelOp {
    EQ_OP,
    NEQ_OP,
    LT_OP,
    LE_OP,
    GT_OP,
    GE_OP
};

// Funciones de agregacion; NONE_AGG significa columna simple
enum AggFun {
    NONE_AGG,
    COUNT_AGG,
    SUM_AGG,
    AVG_AGG,
    MIN_AGG,
    MAX_AGG
};

// Direccion del ORDER BY
enum SortDir {
    ASC_DIR,
    DESC_DIR
};

// -----------------------------
// Valores literales
// -----------------------------

// Clase abstracta Value
class Value {
public:
    virtual void accept(Visitor* visitor) = 0;
    virtual ~Value() = 0;  // Destructor puro -> clase abstracta
};

// Literal entero
class IntValue : public Value {
public:
    int value;
    void accept(Visitor* visitor) override;
    IntValue(int v);
    ~IntValue();
};

// Literal real
class FloatValue : public Value {
public:
    double value;
    void accept(Visitor* visitor) override;
    FloatValue(double v);
    ~FloatValue();
};

// Literal de cadena
class StrValue : public Value {
public:
    string value;
    void accept(Visitor* visitor) override;
    StrValue(string v);
    ~StrValue();
};

// Literal booleano
class BoolValue : public Value {
public:
    bool value;
    void accept(Visitor* visitor) override;
    BoolValue(bool v);
    ~BoolValue();
};

// -----------------------------
// Referencia a columna: col o tabla.col
// -----------------------------

class ColRef {
public:
    string tabla;    // vacio si la columna no se califico
    string columna;
    void accept(Visitor* visitor);
    ColRef();
    ColRef(string tabla, string columna);
    ~ColRef();
};

// -----------------------------
// Condiciones del WHERE
//
// La jerarquia OrCond -> AndCond -> predicado codifica la precedencia:
// OR liga mas flojo que AND, asi que "a=1 AND b=2 OR c=3" se agrupa
// como "(a=1 AND b=2) OR c=3".
// -----------------------------

// Clase abstracta Cond
class Cond {
public:
    virtual void accept(Visitor* visitor) = 0;
    virtual ~Cond() = 0;
    static string relopToChar(RelOp op);  // Conversion operador -> string
};

// Disyuncion de condiciones
class OrCond : public Cond {
public:
    list<Cond*> condiciones;
    void accept(Visitor* visitor) override;
    OrCond();
    ~OrCond();
};

// Conjuncion de condiciones
class AndCond : public Cond {
public:
    list<Cond*> condiciones;
    void accept(Visitor* visitor) override;
    AndCond();
    ~AndCond();
};

// Predicado columna op valor
class CompareCond : public Cond {
public:
    ColRef* columna;
    RelOp op;
    Value* valor;
    void accept(Visitor* visitor) override;
    CompareCond(ColRef* c, RelOp op, Value* v);
    ~CompareCond();
};

// Predicado columna BETWEEN inferior AND superior
class BetweenCond : public Cond {
public:
    ColRef* columna;
    Value* inferior;
    Value* superior;
    void accept(Visitor* visitor) override;
    BetweenCond(ColRef* c, Value* inf, Value* sup);
    ~BetweenCond();
};

// -----------------------------
// Piezas del SELECT
// -----------------------------

// Un elemento de la lista de proyeccion
class SelectItem {
public:
    AggFun agg;
    bool estrella;      // '*' suelto, o el '*' de COUNT(*)
    ColRef* columna;
    void accept(Visitor* visitor);
    SelectItem();
    ~SelectItem();
};

// JOIN tabla ON izquierda = derecha
class JoinClause {
public:
    string tabla;
    ColRef* izquierda;
    ColRef* derecha;
    void accept(Visitor* visitor);
    JoinClause();
    ~JoinClause();
};

// -----------------------------
// Declaracion de columna en el CREATE TABLE
// -----------------------------

class ColumnDec {
public:
    string nombre;
    DataType tipo;
    int longitud;        // solo si tipo == VARCHAR_TYPE
    bool primaryKey = false;
    void accept(Visitor* visitor);
    ColumnDec();
    ~ColumnDec();
};

// -----------------------------
// Sentencias
// -----------------------------

// Clase abstracta Stmt
class Stmt {
public:
    virtual void accept(Visitor* visitor) = 0;
    virtual ~Stmt() = 0;
};

// CREATE TABLE t (col tipo [PRIMARY KEY], ...) USING (heap | sequential)
class CreateTableStmt : public Stmt {
public:
    string tabla;
    list<ColumnDec*> columnas;
    FileOrg org;
    void accept(Visitor* visitor) override;
    CreateTableStmt();
    ~CreateTableStmt();
};

// CREATE INDEX ON t (col) USING (btree | hash) [CLUSTERED]
class CreateIndexStmt : public Stmt {
public:
    string tabla;
    string columna;
    IndexKind tipo;
    bool clustered = false;
    void accept(Visitor* visitor) override;
    CreateIndexStmt();
    ~CreateIndexStmt();
};

// SELECT ... FROM t [JOIN ...] [WHERE ...] [GROUP BY ...] [ORDER BY ...] [LIMIT n]
class SelectStmt : public Stmt {
public:
    bool selectAll = false;             // SELECT *
    list<SelectItem*> proyeccion;
    string tabla;
    JoinClause* join = nullptr;
    Cond* condicion = nullptr;
    ColRef* groupBy = nullptr;
    ColRef* orderBy = nullptr;
    SortDir direccion = ASC_DIR;
    int limite = 0;
    bool haylimite = false;
    void accept(Visitor* visitor) override;
    SelectStmt();
    ~SelectStmt();
};

// INSERT INTO t VALUES (...)
class InsertStmt : public Stmt {
public:
    string tabla;
    list<Value*> valores;
    void accept(Visitor* visitor) override;
    InsertStmt();
    ~InsertStmt();
};

// DELETE FROM t WHERE cond
class DeleteStmt : public Stmt {
public:
    string tabla;
    Cond* condicion = nullptr;
    void accept(Visitor* visitor) override;
    DeleteStmt();
    ~DeleteStmt();
};

// BEGIN TRANSACTION | END TRANSACTION
class TransactionStmt : public Stmt {
public:
    bool esBegin = true;
    void accept(Visitor* visitor) override;
    TransactionStmt(bool esBegin);
    ~TransactionStmt();
};

// -----------------------------
// Raiz del arbol
// -----------------------------

class Programa {
public:
    list<Stmt*> slist;
    void accept(Visitor* visitor);
    Programa();
    ~Programa();
};

#endif // AST_H
