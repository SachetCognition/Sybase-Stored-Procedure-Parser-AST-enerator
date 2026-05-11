"""Shared fixtures for the SQL Server test suite."""

import os
import re

import pymssql
import pytest

HOST = os.getenv("MSSQL_HOST", "localhost")
PORT = int(os.getenv("MSSQL_PORT", "1433"))
USER = os.getenv("MSSQL_USER", "sa")
PASSWORD = os.getenv("MSSQL_PASSWORD", "AcmeERP_Test123!")
DATABASE = os.getenv("MSSQL_DATABASE", "master")

GO_PATTERN = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)

INPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "input")

SQL_FILES = [
    "01_create_schema.sql",
    "02_payroll_tables.sql",
    "03_inventory_costing_tables.sql",
    "04_multi_currency_tables.sql",
    "05_sample_data.sql",
    "06_sp_CalculateFifoCost.sql",
    "07_sp_ConvertToBase.sql",
    "08_sp_ProcessFullPayrollCycle.sql",
]


def split_batches(sql_text: str) -> list[str]:
    batches = GO_PATTERN.split(sql_text)
    return [b.strip() for b in batches if b.strip()]


def exec_sql_file(conn, filepath: str) -> None:
    with open(filepath, "r", encoding="utf-8") as f:
        sql_text = f.read()
    cursor = conn.cursor()
    for batch in split_batches(sql_text):
        try:
            cursor.execute(batch)
            conn.commit()
        except Exception:
            conn.rollback()
    cursor.close()


def exec_sp(conn, sp_name: str, params: dict | None = None) -> list[tuple]:
    """Call a stored procedure and return the result set rows."""
    cursor = conn.cursor()
    if params:
        param_list = ", ".join(
            f"@{k} = %s" for k in params
        )
        query = f"EXEC {sp_name} {param_list}"
        cursor.execute(query, tuple(params.values()))
    else:
        cursor.execute(f"EXEC {sp_name}")
    try:
        rows = cursor.fetchall()
    except pymssql.OperationalError:
        rows = []
    conn.commit()
    cursor.close()
    return rows


@pytest.fixture(scope="session")
def db_connection():
    """Session-scoped database connection."""
    conn = pymssql.connect(
        server=HOST, port=PORT, user=USER, password=PASSWORD, database=DATABASE
    )
    yield conn
    conn.close()


@pytest.fixture(scope="session", autouse=True)
def setup_schema(db_connection):
    """Deploy DDL, seed data, and stored procedures once per session."""
    for filename in SQL_FILES:
        filepath = os.path.join(INPUT_DIR, filename)
        if os.path.exists(filepath):
            exec_sql_file(db_connection, filepath)

    # Also create dbo helper tables for edge-case SPs
    cursor = db_connection.cursor()
    dbo_ddl = [
        """
        IF OBJECT_ID('dbo.Employees', 'U') IS NULL
        CREATE TABLE dbo.Employees (
            EmployeeID INT PRIMARY KEY,
            Name VARCHAR(100),
            Department VARCHAR(50),
            Salary DECIMAL(18,2)
        )
        """,
        """
        IF OBJECT_ID('dbo.Tasks', 'U') IS NULL
        CREATE TABLE dbo.Tasks (
            TaskID INT PRIMARY KEY IDENTITY,
            EmployeeID INT,
            TaskName VARCHAR(100),
            Status VARCHAR(20) DEFAULT 'Pending'
        )
        """,
        """
        IF OBJECT_ID('dbo.users', 'U') IS NULL
        CREATE TABLE dbo.users (
            id INT PRIMARY KEY,
            name VARCHAR(100),
            active BIT DEFAULT 1
        )
        """,
        """
        IF OBJECT_ID('dbo.logs', 'U') IS NULL
        CREATE TABLE dbo.logs (
            log_id INT PRIMARY KEY IDENTITY,
            user_id INT,
            status VARCHAR(50),
            attempt INT
        )
        """,
        """
        IF OBJECT_ID('dbo.audit_log', 'U') IS NULL
        CREATE TABLE dbo.audit_log (
            audit_id INT PRIMARY KEY IDENTITY,
            user_id INT,
            message VARCHAR(MAX)
        )
        """,
    ]
    for ddl in dbo_ddl:
        try:
            cursor.execute(ddl)
            db_connection.commit()
        except Exception:
            db_connection.rollback()
    cursor.close()


@pytest.fixture(autouse=True)
def clean_payroll_logs(db_connection):
    """Truncate PayrollLogs between tests to ensure isolation."""
    cursor = db_connection.cursor()
    try:
        cursor.execute("DELETE FROM AcmeERP.PayrollLogs")
        db_connection.commit()
    except Exception:
        db_connection.rollback()
    cursor.close()
    yield
