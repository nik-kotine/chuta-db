#ifndef VISITOR_H
#define VISITOR_H
#include "ast.h"
#include <list>

class IntValue;
class FloatValue;
class StrValue;
class BoolValue;

class Visitor {
public:
    virtual void visit(IntValue* v) = 0;
    virtual void visit(FloatValue* v) = 0;
    virtual void visit(StrValue* v) = 0;
    virtual void visit(BoolValue* v) = 0;
    virtual void visit(ColRef* c) = 0;
    virtual void visit(OrCond* c) = 0;
    virtual void visit(AndCond* c) = 0;
    virtual void visit(CompareCond* c) = 0;
    virtual void visit(BetweenCond* c) = 0;
    virtual void visit(SelectItem* item) = 0;
    virtual void visit(JoinClause* j) = 0;
    virtual void visit(ColumnDec* cd) = 0;
    virtual void visit(CreateTableStmt* stm) = 0;
    virtual void visit(CreateIndexStmt* stm) = 0;
    virtual void visit(SelectStmt* stm) = 0;
    virtual void visit(InsertStmt* stm) = 0;
    virtual void visit(DeleteStmt* stm) = 0;
    virtual void visit(TransactionStmt* stm) = 0;
    virtual void visit(Programa* program) = 0;
};

// Imprime el AST reconstruyendo la sentencia SQL
class PrintVisitor : public Visitor {
public:
    void visit(IntValue* v) override;
    void visit(FloatValue* v) override;
    void visit(StrValue* v) override;
    void visit(BoolValue* v) override;
    void visit(ColRef* c) override;
    void visit(OrCond* c) override;
    void visit(AndCond* c) override;
    void visit(CompareCond* c) override;
    void visit(BetweenCond* c) override;
    void visit(SelectItem* item) override;
    void visit(JoinClause* j) override;
    void visit(ColumnDec* cd) override;
    void visit(CreateTableStmt* stm) override;
    void visit(CreateIndexStmt* stm) override;
    void visit(SelectStmt* stm) override;
    void visit(InsertStmt* stm) override;
    void visit(DeleteStmt* stm) override;
    void visit(TransactionStmt* stm) override;
    void visit(Programa* program) override;
    void imprimir(Programa* program);
};

#endif // VISITOR_H
