#!/usr/bin/env python3
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

from db import TIMEFRAMES, _table_name


ROOT = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = ROOT / "edgerunner.db"
WAREHOUSE_ROOT = ROOT / ".research_warehouse"
PARQUET_ROOT = WAREHOUSE_ROOT / "parquet"
WAREHOUSE_PATH = WAREHOUSE_ROOT / "edgerunner_research.duckdb"

EXTRA_TABLES = ("seeker_cycles", "seeker_cycle_events")
BATCH_SIZE = 100_000


def _require_duckdb():
    return importlib.import_module("duckdb")


def _require_pyarrow():
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    return pa, pq


def _sqlite_arrow_schema(conn: sqlite3.Connection, table: str):
    pa, _ = _require_pyarrow()
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    fields = []
    for _, name, col_type, _, _, _ in rows:
        t = (col_type or "").upper()
        if "INT" in t:
            arrow_type = pa.int64()
        elif any(x in t for x in ("REAL", "FLOA", "DOUB", "NUM")):
            arrow_type = pa.float64()
        elif any(x in t for x in ("TEXT", "CHAR", "CLOB")):
            arrow_type = pa.string()
        else:
            arrow_type = pa.string()
        fields.append(pa.field(name, arrow_type, nullable=True))
    return pa.schema(fields)


def _sqlite_rows(conn: sqlite3.Connection, query: str, params: tuple = ()):
    cur = conn.execute(query, params)
    cols = [d[0] for d in cur.description]
    while True:
        batch = cur.fetchmany(BATCH_SIZE)
        if not batch:
            break
        yield [dict(zip(cols, row, strict=False)) for row in batch]


def _write_table_parquet(sqlite_conn: sqlite3.Connection, table: str, out_dir: Path) -> int:
    pa, pq = _require_pyarrow()
    schema = _sqlite_arrow_schema(sqlite_conn, table)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.parquet"):
        old.unlink()

    total = 0
    writer = None
    for chunk_idx, rows in enumerate(_sqlite_rows(sqlite_conn, f"SELECT * FROM {table} ORDER BY 1")):
        if not rows:
            continue
        arrow_table = pa.Table.from_pylist(rows, schema=schema)
        if writer is None:
            writer = pq.ParquetWriter(str(out_dir / f"{table}.parquet"), schema, compression="zstd")
        writer.write_table(arrow_table)
        total += len(rows)
    if writer is not None:
        writer.close()
    return total


def build_research_warehouse(
    sqlite_path: str | Path = DEFAULT_SQLITE_PATH,
    warehouse_path: str | Path = WAREHOUSE_PATH,
    parquet_root: str | Path = PARQUET_ROOT,
) -> dict[str, object]:
    sqlite_path = Path(sqlite_path)
    warehouse_path = Path(warehouse_path)
    parquet_root = Path(parquet_root)
    warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    parquet_root.mkdir(parents=True, exist_ok=True)

    sqlite_conn = sqlite3.connect(sqlite_path)
    duckdb = _require_duckdb()
    duck = duckdb.connect(str(warehouse_path))

    exported: dict[str, int] = {}
    try:
        for tf in TIMEFRAMES:
            table = _table_name(tf)
            out_dir = parquet_root / table
            exported[table] = _write_table_parquet(sqlite_conn, table, out_dir)
            parquet_glob = str(out_dir / "*.parquet").replace("'", "''")
            duck.execute(
                f"""
                CREATE OR REPLACE VIEW {table} AS
                SELECT * FROM read_parquet('{parquet_glob}')
                """
            )

        for table in EXTRA_TABLES:
            out_dir = parquet_root / table
            exported[table] = _write_table_parquet(sqlite_conn, table, out_dir)
            parquet_glob = str(out_dir / "*.parquet").replace("'", "''")
            duck.execute(
                f"""
                CREATE OR REPLACE VIEW {table} AS
                SELECT * FROM read_parquet('{parquet_glob}')
                """
            )

        duck.execute(
            """
            CREATE OR REPLACE TABLE warehouse_meta AS
            SELECT * FROM (
                VALUES
                    ('sqlite_path', ?),
                    ('parquet_root', ?)
            ) AS meta(key, value)
            """,
            [str(sqlite_path), str(parquet_root)],
        )
    finally:
        sqlite_conn.close()
        duck.close()

    return {
        "sqlitePath": str(sqlite_path),
        "warehousePath": str(warehouse_path),
        "parquetRoot": str(parquet_root),
        "exportedRows": exported,
    }


def connect_warehouse(path: str | Path = WAREHOUSE_PATH):
    duckdb = _require_duckdb()
    return duckdb.connect(str(path), read_only=True)
