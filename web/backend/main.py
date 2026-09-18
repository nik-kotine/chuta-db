from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "parser"))
sys.path.insert(1, str(ROOT))
os.chdir(ROOT)

from parser import Parser  # noqa: E402
from scanner import Scanner  # noqa: E402
from executor import ExecuteVisitor  # noqa: E402
from storage.storage_manager import StorageManager  # noqa: E402


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)


class QueryResponse(BaseModel):
    message: str = ""
    columns: list[str] = []
    rows: list[list[Any]] = []
    plan: list[dict[str, Any]] = []
    row_count: int = 0


app = FastAPI(
    title="Chuta DB API",
    version="1.0.0",
    description="API HTTP para consultar el motor de almacenamiento de Chuta DB.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_storage = StorageManager()


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def table_metadata(name: str, metadata: dict[str, Any]) -> dict[str, Any]:
    indexes = _storage.catalog.get_table_indexes(name)
    return {
        "name": name,
        "file_type": metadata["file_type"],
        "key_index": metadata["key_index"],
        "columns": [
            {
                "name": column_name,
                "type": column_type,
                "position": position,
                "primary_key": position == metadata["key_index"],
            }
            for position, (column_name, column_type) in enumerate(
                zip(metadata["column_names"], metadata["schema"])
            )
        ],
        "indexes": indexes,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/tables")
def list_tables() -> dict[str, list[dict[str, Any]]]:
    tables = []
    for name in ("sys_tables", "sys_columns", "sys_indexes"):
        metadata = _storage.catalog.get_table_info(name)
        if metadata:
            tables.append(table_metadata(name, metadata))
    for record in _storage.catalog.sys_tables.scan():
        name = _storage.catalog._clean_str(record[1][0])
        if name not in {table["name"] for table in tables}:
            metadata = _storage.catalog.get_table_info(name)
            if metadata:
                tables.append(table_metadata(name, metadata))
    return {"tables": tables}


@app.post("/api/query", response_model=QueryResponse)
def execute_query(request: QueryRequest) -> QueryResponse:
    try:
        program = Parser(Scanner(request.sql)).parse_program()
        results = ExecuteVisitor(_storage).ejecutar(program)
    except Exception as error:
        detail = str(error)
        raise HTTPException(status_code=400, detail=detail) from error

    if not results:
        return QueryResponse(message="Consulta ejecutada")
    result = results[-1]
    return QueryResponse(
        message=result.mensaje,
        columns=result.columnas,
        rows=[[json_value(value) for value in row] for row in result.filas],
        plan=result.plan,
        row_count=len(result.filas),
    )


@app.on_event("shutdown")
def close_storage() -> None:
    _storage.close()
