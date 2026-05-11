#!/usr/bin/env python3
"""Generate additional synthetic data for edge-case testing.

This script is idempotent: it truncates test-specific rows (or uses
DELETE WHERE patterns) before re-inserting so it can be run repeatedly.
"""

import os
import sys
from datetime import date, timedelta

import pymssql

HOST = os.getenv("MSSQL_HOST", "localhost")
PORT = int(os.getenv("MSSQL_PORT", "1433"))
USER = os.getenv("MSSQL_USER", "sa")
PASSWORD = os.getenv("MSSQL_PASSWORD", "AcmeERP_Test123!")
DATABASE = os.getenv("MSSQL_DATABASE", "master")


def get_connection() -> pymssql.Connection:
    return pymssql.connect(
        server=HOST, port=PORT, user=USER, password=PASSWORD, database=DATABASE
    )


def ensure_dbo_tables(cursor) -> None:
    """Create dbo helper tables used by edge-case SPs (sample_1-5, edge_*)."""
    ddl_statements = [
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
    for ddl in ddl_statements:
        cursor.execute(ddl)


def insert_edge_case_employees(cursor) -> None:
    """Insert employees at bonus-tier and tax-bracket boundaries."""
    today = date.today()

    employees = [
        # (FirstName, LastName, Dept, Position, HireDate, BaseSalary, Currency)
        # Exactly 2 years tenure (bonus tier boundary: 5%)
        ("Edge", "TwoYear", "IT", "Analyst",
         today - timedelta(days=2 * 365), 45000.00, "USD"),
        # Exactly 5 years tenure (bonus tier boundary: 10%)
        ("Edge", "FiveYear", "HR", "Manager",
         today - timedelta(days=5 * 365), 60000.00, "USD"),
        # Exactly 10 years tenure (bonus tier boundary: 15%)
        ("Edge", "TenYear", "Finance", "Director",
         today - timedelta(days=10 * 365), 90000.00, "USD"),
        # Salary exactly at 50000 (tax bracket boundary: 10%)
        ("Edge", "Tax50K", "IT", "Analyst",
         today - timedelta(days=365), 50000.00, "USD"),
        # Salary exactly at 75000 (tax bracket boundary: 15%)
        ("Edge", "Tax75K", "HR", "Manager",
         today - timedelta(days=3 * 365), 75000.00, "USD"),
        # EUR currency employee
        ("Edge", "EurEmp", "Finance", "Analyst",
         today - timedelta(days=4 * 365), 55000.00, "EUR"),
        # GBP currency employee
        ("Edge", "GbpEmp", "IT", "Manager",
         today - timedelta(days=6 * 365), 65000.00, "GBP"),
        # JPY currency employee
        ("Edge", "JpyEmp", "HR", "Analyst",
         today - timedelta(days=7 * 365), 70000.00, "JPY"),
        # INR currency employee
        ("Edge", "InrEmp", "Finance", "Manager",
         today - timedelta(days=8 * 365), 80000.00, "INR"),
        # NULL currency employee — inserted separately after ALTER to allow NULL
        # ("Edge", "NullCur", "IT", "Analyst",
        #  today - timedelta(days=365), 40000.00, None),
        # Zero salary
        ("Edge", "ZeroSal", "HR", "Intern",
         today - timedelta(days=365), 0.00, "USD"),
        # Future hire date
        ("Edge", "FutureHire", "IT", "Trainee",
         today + timedelta(days=365), 35000.00, "USD"),
    ]

    # Remove previously inserted edge employees
    cursor.execute(
        "DELETE FROM AcmeERP.Employees WHERE FirstName = 'Edge'"
    )

    for emp in employees:
        cursor.execute(
            """
            INSERT INTO AcmeERP.Employees
                (FirstName, LastName, Department, Position, HireDate, BaseSalary, Currency)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            emp,
        )
    # Insert the NULL currency employee by temporarily allowing NULLs
    cursor.execute(
        "ALTER TABLE AcmeERP.Employees ALTER COLUMN Currency CHAR(3) NULL"
    )
    cursor.execute(
        """
        INSERT INTO AcmeERP.Employees
            (FirstName, LastName, Department, Position, HireDate, BaseSalary, Currency)
        VALUES ('Edge', 'NullCur', 'IT', 'Analyst', %s, 40000.00, NULL)
        """,
        (today - timedelta(days=365),),
    )
    # Keep column nullable so the NULL row persists for testing ISNULL in the SP
    print(f"  Inserted {len(employees) + 1} edge-case employees (including NULL currency).")


def insert_edge_case_stock_movements(cursor) -> None:
    """Insert stock movements for FIFO edge cases."""
    # Ensure we have a product for edge cases
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM AcmeERP.Products WHERE ProductName = 'EdgeProduct-NoIN')
            INSERT INTO AcmeERP.Products (ProductName, Category, CostMethod, CurrentStock)
            VALUES ('EdgeProduct-NoIN', 'Electronics', 'FIFO', 0)
        """
    )
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM AcmeERP.Products WHERE ProductName = 'EdgeProduct-SingleBatch')
            INSERT INTO AcmeERP.Products (ProductName, Category, CostMethod, CurrentStock)
            VALUES ('EdgeProduct-SingleBatch', 'Electronics', 'FIFO', 100)
        """
    )
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM AcmeERP.Products WHERE ProductName = 'EdgeProduct-ExactMatch')
            INSERT INTO AcmeERP.Products (ProductName, Category, CostMethod, CurrentStock)
            VALUES ('EdgeProduct-ExactMatch', 'Electronics', 'FIFO', 30)
        """
    )
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM AcmeERP.Products WHERE ProductName = 'EdgeProduct-Insufficient')
            INSERT INTO AcmeERP.Products (ProductName, Category, CostMethod, CurrentStock)
            VALUES ('EdgeProduct-Insufficient', 'Electronics', 'FIFO', 5)
        """
    )

    today = date.today()

    # Get product IDs
    cursor.execute(
        "SELECT ProductID, ProductName FROM AcmeERP.Products WHERE ProductName LIKE 'EdgeProduct-%'"
    )
    products = {row[1]: row[0] for row in cursor.fetchall()}

    # Product with zero IN movements (only OUT)
    pid_no_in = products.get("EdgeProduct-NoIN")
    if pid_no_in:
        cursor.execute(
            "DELETE FROM AcmeERP.StockMovements WHERE ProductID = %s", (pid_no_in,)
        )
        cursor.execute(
            """
            INSERT INTO AcmeERP.StockMovements (ProductID, MovementDate, Quantity, UnitCost, Direction)
            VALUES (%s, %s, 10, 5.00, 'OUT')
            """,
            (pid_no_in, today - timedelta(days=5)),
        )

    # Product with single large IN movement
    pid_single = products.get("EdgeProduct-SingleBatch")
    if pid_single:
        cursor.execute(
            "DELETE FROM AcmeERP.StockMovements WHERE ProductID = %s", (pid_single,)
        )
        cursor.execute(
            """
            INSERT INTO AcmeERP.StockMovements (ProductID, MovementDate, Quantity, UnitCost, Direction)
            VALUES (%s, %s, 1000, 25.00, 'IN')
            """,
            (pid_single, today - timedelta(days=10)),
        )

    # Product with RunningTotal exactly equal to QuantityRequested
    pid_exact = products.get("EdgeProduct-ExactMatch")
    if pid_exact:
        cursor.execute(
            "DELETE FROM AcmeERP.StockMovements WHERE ProductID = %s", (pid_exact,)
        )
        for i, (qty, cost) in enumerate([(10, 5.00), (10, 10.00), (10, 15.00)]):
            cursor.execute(
                """
                INSERT INTO AcmeERP.StockMovements (ProductID, MovementDate, Quantity, UnitCost, Direction)
                VALUES (%s, %s, %s, %s, 'IN')
                """,
                (pid_exact, today - timedelta(days=30 - i), qty, cost),
            )

    # Product with insufficient stock
    pid_insuff = products.get("EdgeProduct-Insufficient")
    if pid_insuff:
        cursor.execute(
            "DELETE FROM AcmeERP.StockMovements WHERE ProductID = %s", (pid_insuff,)
        )
        cursor.execute(
            """
            INSERT INTO AcmeERP.StockMovements (ProductID, MovementDate, Quantity, UnitCost, Direction)
            VALUES (%s, %s, 5, 8.00, 'IN')
            """,
            (pid_insuff, today - timedelta(days=5)),
        )

    print("  Inserted edge-case stock movements for 4 products.")


def insert_edge_case_exchange_rates(cursor) -> None:
    """Insert exchange rates for currency conversion edge cases."""
    today = date.today()

    # Remove old edge rates
    cursor.execute(
        "DELETE FROM AcmeERP.ExchangeRates WHERE CurrencyCode = 'INR'"
    )

    # INR: exact date match
    cursor.execute(
        """
        INSERT INTO AcmeERP.ExchangeRates (CurrencyCode, RateDate, RateToBase)
        VALUES ('INR', %s, 0.012)
        """,
        (today,),
    )

    # INR: rate before date (for fallback testing)
    cursor.execute(
        """
        INSERT INTO AcmeERP.ExchangeRates (CurrencyCode, RateDate, RateToBase)
        VALUES ('INR', %s, 0.013)
        """,
        (today - timedelta(days=3),),
    )

    # INR: rate within 7 days (for avg fallback)
    cursor.execute(
        """
        INSERT INTO AcmeERP.ExchangeRates (CurrencyCode, RateDate, RateToBase)
        VALUES ('INR', %s, 0.011)
        """,
        (today - timedelta(days=5),),
    )

    # Ensure we have a currency with NO rates at all for testing
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM AcmeERP.Currencies WHERE CurrencyCode = 'XYZ')
            INSERT INTO AcmeERP.Currencies (CurrencyCode, CurrencyName)
            VALUES ('XYZ', 'Test Currency No Rates')
        """
    )
    cursor.execute(
        "DELETE FROM AcmeERP.ExchangeRates WHERE CurrencyCode = 'XYZ'"
    )

    # Currency with NO rates in last 7 days but old rate (>7 days old)
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM AcmeERP.Currencies WHERE CurrencyCode = 'CHF')
            INSERT INTO AcmeERP.Currencies (CurrencyCode, CurrencyName)
            VALUES ('CHF', 'Swiss Franc')
        """
    )
    cursor.execute(
        "DELETE FROM AcmeERP.ExchangeRates WHERE CurrencyCode = 'CHF'"
    )
    cursor.execute(
        """
        INSERT INTO AcmeERP.ExchangeRates (CurrencyCode, RateDate, RateToBase)
        VALUES ('CHF', %s, 1.08)
        """,
        (today - timedelta(days=30),),
    )

    print("  Inserted edge-case exchange rates.")


def insert_dbo_test_data(cursor) -> None:
    """Insert test data into dbo tables for edge-case SPs."""
    # dbo.Employees
    cursor.execute("DELETE FROM dbo.Employees")
    cursor.execute(
        """
        INSERT INTO dbo.Employees (EmployeeID, Name, Department, Salary)
        VALUES
            (1, 'Alice Smith', 'Engineering', 85000.00),
            (2, 'Bob Jones', 'HR', 55000.00),
            (3, 'Charlie Brown', 'Finance', 72000.00)
        """
    )

    # dbo.Tasks
    cursor.execute("DELETE FROM dbo.Tasks")
    cursor.execute(
        """
        SET IDENTITY_INSERT dbo.Tasks ON;
        INSERT INTO dbo.Tasks (TaskID, EmployeeID, TaskName, Status)
        VALUES
            (1, 1, 'Review PR', 'Pending'),
            (2, 1, 'Deploy feature', 'InProgress'),
            (3, 1, 'Write docs', 'Completed'),
            (4, 2, 'Onboarding', 'Pending'),
            (5, 2, 'Handbook update', 'Completed'),
            (6, 3, 'Budget review', 'Pending');
        SET IDENTITY_INSERT dbo.Tasks OFF;
        """
    )

    # dbo.users
    cursor.execute("DELETE FROM dbo.users")
    cursor.execute(
        """
        INSERT INTO dbo.users (id, name, active)
        VALUES
            (1, 'user_alice', 1),
            (2, 'user_bob', 1),
            (3, 'user_charlie', 0),
            (4, 'user_diana', 1)
        """
    )

    # dbo.logs
    cursor.execute("DELETE FROM dbo.logs")
    cursor.execute(
        """
        INSERT INTO dbo.logs (user_id, status, attempt)
        VALUES
            (1, 'pending', 1),
            (1, 'pending', 2),
            (2, 'pending', 1),
            (4, 'processed', 1)
        """
    )

    # dbo.audit_log
    cursor.execute("DELETE FROM dbo.audit_log")

    print("  Inserted dbo test data (Employees, Tasks, users, logs, audit_log).")


def print_summary(cursor) -> None:
    """Print summary stats of all tables."""
    tables = [
        "AcmeERP.Employees",
        "AcmeERP.Products",
        "AcmeERP.StockMovements",
        "AcmeERP.Currencies",
        "AcmeERP.ExchangeRates",
        "AcmeERP.PayrollLogs",
        "dbo.Employees",
        "dbo.Tasks",
        "dbo.users",
        "dbo.logs",
        "dbo.audit_log",
    ]
    print("\n=== Synthetic Data Summary ===")
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"  {table}: {count} rows")
        except Exception as exc:
            print(f"  {table}: ERROR - {exc}")


def main() -> None:
    print("Connecting to SQL Server...")
    conn = get_connection()
    cursor = conn.cursor()

    print("Creating dbo helper tables...")
    ensure_dbo_tables(cursor)
    conn.commit()

    print("Inserting edge-case employees...")
    insert_edge_case_employees(cursor)
    conn.commit()

    print("Inserting edge-case stock movements...")
    insert_edge_case_stock_movements(cursor)
    conn.commit()

    print("Inserting edge-case exchange rates...")
    insert_edge_case_exchange_rates(cursor)
    conn.commit()

    print("Inserting dbo test data for edge-case SPs...")
    insert_dbo_test_data(cursor)
    conn.commit()

    print_summary(cursor)

    cursor.close()
    conn.close()
    print("\nSynthetic data generation complete.")


if __name__ == "__main__":
    main()
