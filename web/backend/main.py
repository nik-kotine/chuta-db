from __future__ import annotations

import json
import os
import sys
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
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
    affected_rows: int = 0
    duration_ms: float = 0


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
_sessions: dict[str, ExecuteVisitor] = {}


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


@app.get("/api/spatial/points")
def spatial_points(table: str | None = None) -> dict[str, Any]:
    """Returns rows that expose longitude/latitude columns for map rendering."""
    points = []
    metadata_items = []
    for record in _storage.catalog.sys_tables.scan():
        name = _storage.catalog._clean_str(record[1][0])
        if name.startswith("sys_") or (table and name != table):
            continue
        metadata = _storage.catalog.get_table_info(name)
        if metadata:
            metadata_items.append((name, metadata))

    for name, metadata in metadata_items:
        names = [column.lower() for column in metadata["column_names"]]
        lon_index = next((names.index(column) for column in ("longitude", "lon", "lng", "x") if column in names), None)
        lat_index = next((names.index(column) for column in ("latitude", "lat", "y") if column in names), None)
        if lon_index is None or lat_index is None:
            continue
        table_obj = _storage.open_table(name)
        for rid, values in table_obj.scan():
            try:
                longitude = float(values[lon_index])
                latitude = float(values[lat_index])
            except (TypeError, ValueError):
                continue
            points.append({
                "table": name,
                "rid": [rid[0], rid[1]],
                "longitude": longitude,
                "latitude": latitude,
                "label": str(values[0]) if values else name,
                "values": [json_value(value) for value in values],
            })
    return {"points": points}


def affected_rows(message: str, fallback: int) -> int:
    match = re.search(r"(\d+)\s+fila", message or "")
    return int(match.group(1)) if match else fallback


@app.post("/api/query", response_model=QueryResponse)
def execute_query(
    request: QueryRequest,
    session_id: str = Header(default="default", alias="X-Session-ID"),
) -> QueryResponse:
    started = time.perf_counter()
    try:
        program = Parser(Scanner(request.sql)).parse_program()
        visitor = _sessions.setdefault(session_id, ExecuteVisitor(_storage))
        results = visitor.ejecutar(program)
    except Exception as error:
        detail = str(error)
        raise HTTPException(status_code=400, detail=detail) from error

    if not results:
        return QueryResponse(
            message="Consulta ejecutada",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    result = results[-1]
    duration_ms = (time.perf_counter() - started) * 1000
    rows = len(result.filas)
    return QueryResponse(
        message=result.mensaje,
        columns=result.columnas,
        rows=[[json_value(value) for value in row] for row in result.filas],
        plan=result.plan,
        row_count=rows,
        affected_rows=affected_rows(result.mensaje, rows),
        duration_ms=duration_ms,
    )


@app.get("/api/transactions")
def list_transactions() -> dict[str, list[dict[str, Any]]]:
    transactions = []
    for transaction in _storage.transaction_manager._transactions.values():
        transactions.append({
            "transaction_id": transaction.transaction_id,
            "status": transaction.status.name,
            "last_lsn": transaction.last_lsn,
        })
    return {"transactions": transactions}


@app.get("/api/transactions/{transaction_id}")
def transaction_detail(transaction_id: int) -> dict[str, Any]:
    try:
        transaction = _storage.transaction_manager.get(transaction_id)
    except Exception as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    records = [
        record for record in _storage.log_manager.iter_records()
        if record.transaction_id == transaction_id
    ]
    locks = [
        lock for lock in _storage.lock_manager.snapshot()
        if lock["transaction_id"] == transaction_id
    ]
    return {
        "transaction_id": transaction.transaction_id,
        "status": transaction.status.name,
        "last_lsn": transaction.last_lsn,
        "locks": locks,
        "events": [
            {
                "type": record.record_type.name,
                "lsn": record.lsn,
                "prev_lsn": record.prev_lsn,
                "operation": record.operation,
                "resource": record.file_name,
                "resource_type": record.resource_type,
                "page_id": record.page_id,
            }
            for record in records
        ],
    }


@app.get("/api/locks")
def list_locks() -> dict[str, list[dict[str, Any]]]:
    return {"locks": _storage.lock_manager.snapshot()}


@app.get("/api/wal")
def list_wal(transaction_id: int | None = None) -> dict[str, list[dict[str, Any]]]:
    records = []
    for record in _storage.log_manager.iter_records():
        if transaction_id is not None and record.transaction_id != transaction_id:
            continue
        records.append({
            "lsn": record.lsn,
            "prev_lsn": record.prev_lsn,
            "transaction_id": record.transaction_id,
            "type": record.record_type.name,
            "operation": record.operation,
            "resource": record.file_name,
            "file_name": record.file_name,
            "resource_type": record.resource_type,
            "page_id": record.page_id,
            "before_bytes": len(record.before),
            "after_bytes": len(record.after),
        })
    return {"records": records}


@app.get("/api/recovery/status")
def recovery_status() -> dict[str, Any]:
    records = list(_storage.log_manager.iter_records())
    return {
        "recovered_transactions": _storage.recovered_transactions,
        "last_checkpoint": next((record.lsn for record in reversed(records) if record.record_type.name == "CHECKPOINT"), None),
        "redo_records": sum(record.record_type.name == "UPDATE" and record.transaction_id not in _storage.recovered_transactions for record in records),
        "clr_records": sum(record.record_type.name == "CLR" for record in records),
    }


@app.on_event("shutdown")
def close_storage() -> None:
    _storage.close()
