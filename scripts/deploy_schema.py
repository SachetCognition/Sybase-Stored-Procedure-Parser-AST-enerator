#!/usr/bin/env python3
"""Deploy SQL schema and stored procedures to SQL Server using pymssql.

Reads the input SQL files (01-08) in order, splits on GO batch separators,
and executes each batch separately.
"""

import os
import re
import sys
import time

import pymssql

HOST = os.getenv("MSSQL_HOST", "localhost")
PORT = int(os.getenv("MSSQL_PORT", "1433"))
USER = os.getenv("MSSQL_USER", "sa")
PASSWORD = os.getenv("MSSQL_PASSWORD", "AcmeERP_Test123!")
DATABASE = os.getenv("MSSQL_DATABASE", "master")

SQL_FILES = [
    "input/01_create_schema.sql",
    "input/02_payroll_tables.sql",
    "input/03_inventory_costing_tables.sql",
    "input/04_multi_currency_tables.sql",
    "input/05_sample_data.sql",
    "input/06_sp_CalculateFifoCost.sql",
    "input/07_sp_ConvertToBase.sql",
    "input/08_sp_ProcessFullPayrollCycle.sql",
]

GO_PATTERN = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)


def wait_for_server(max_retries: int = 30, delay: int = 5) -> pymssql.Connection:
    """Wait for SQL Server to become available and return a connection."""
    for attempt in range(1, max_retries + 1):
        try:
            conn = pymssql.connect(
                server=HOST, port=PORT, user=USER, password=PASSWORD, database=DATABASE
            )
            print("SQL Server is ready.")
            return conn
        except pymssql.OperationalError:
            print(f"  Waiting for SQL Server... attempt {attempt}/{max_retries}")
            time.sleep(delay)
    print("ERROR: SQL Server did not become available.", file=sys.stderr)
    sys.exit(1)


def split_batches(sql_text: str) -> list[str]:
    """Split SQL text on GO batch separators."""
    batches = GO_PATTERN.split(sql_text)
    return [b.strip() for b in batches if b.strip()]


def execute_file(conn: pymssql.Connection, filepath: str) -> None:
    """Execute a single SQL file, splitting on GO separators."""
    with open(filepath, "r", encoding="utf-8") as f:
        sql_text = f.read()

    batches = split_batches(sql_text)
    cursor = conn.cursor()
    for i, batch in enumerate(batches, 1):
        try:
            cursor.execute(batch)
            conn.commit()
        except Exception as exc:
            print(f"  Warning: batch {i} in {filepath} raised: {exc}")
            conn.rollback()
    cursor.close()


def main() -> None:
    print("Connecting to SQL Server...")
    conn = wait_for_server()

    for filepath in SQL_FILES:
        if not os.path.exists(filepath):
            print(f"WARNING: {filepath} not found, skipping.")
            continue
        print(f"Executing {filepath}...")
        execute_file(conn, filepath)
        print(f"  Done.")

    conn.close()
    print("Schema deployment complete.")


if __name__ == "__main__":
    main()
