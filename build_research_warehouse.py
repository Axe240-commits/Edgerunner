#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from research_warehouse import (
    DEFAULT_SQLITE_PATH,
    PARQUET_ROOT,
    WAREHOUSE_PATH,
    build_research_warehouse,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-path", default=str(DEFAULT_SQLITE_PATH))
    parser.add_argument("--warehouse-path", default=str(WAREHOUSE_PATH))
    parser.add_argument("--parquet-root", default=str(PARQUET_ROOT))
    args = parser.parse_args()

    report = build_research_warehouse(
        sqlite_path=args.sqlite_path,
        warehouse_path=args.warehouse_path,
        parquet_root=args.parquet_root,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
