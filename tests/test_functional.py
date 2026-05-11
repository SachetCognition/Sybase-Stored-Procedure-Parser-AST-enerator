"""End-to-end functional tests."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import exec_sp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_employee(conn, first, last, dept, position, hire_date, salary, currency):
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO AcmeERP.Employees "
        "(FirstName, LastName, Department, Position, HireDate, BaseSalary, Currency) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (first, last, dept, position, hire_date, salary, currency),
    )
    conn.commit()
    cursor.execute("SELECT SCOPE_IDENTITY()")
    eid = int(cursor.fetchone()[0])
    cursor.close()
    return eid


def _cleanup_employee(conn, eid):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM AcmeERP.PayrollLogs WHERE EmployeeID = %s", (eid,))
    cursor.execute("DELETE FROM AcmeERP.Employees WHERE EmployeeID = %s", (eid,))
    conn.commit()
    cursor.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFullPayrollFlow:
    def test_full_payroll_then_verify_logs(self, db_connection):
        """Run payroll cycle, then query PayrollLogs and verify entries."""
        today = date.today()
        eids = []
        for i in range(3):
            eid = _insert_employee(
                db_connection,
                f"Func{i}", "Test", "IT", "Analyst",
                today - timedelta(days=(i + 1) * 365),
                Decimal("50000.00"), "USD",
            )
            eids.append(eid)

        try:
            cursor = db_connection.cursor()
            cursor.execute(
                "EXEC AcmeERP.usp_ProcessFullPayrollCycle "
                "@PayPeriodStart = %s, @PayPeriodEnd = %s",
                (today - timedelta(days=30), today),
            )
            db_connection.commit()
            cursor.close()

            cursor = db_connection.cursor()
            for eid in eids:
                cursor.execute(
                    "SELECT COUNT(*) FROM AcmeERP.PayrollLogs WHERE EmployeeID = %s",
                    (eid,),
                )
                count = cursor.fetchone()[0]
                assert count >= 1, f"No payroll log for employee {eid}"
            cursor.close()
        finally:
            for eid in eids:
                _cleanup_employee(db_connection, eid)


class TestFifoAfterMovements:
    def test_fifo_after_stock_movements(self, db_connection):
        """Insert movements, run FIFO, verify cost."""
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO AcmeERP.Products (ProductName, Category, CostMethod, CurrentStock) "
            "VALUES ('FuncTest-FIFO', 'Test', 'FIFO', 0)"
        )
        db_connection.commit()
        cursor.execute(
            "SELECT ProductID FROM AcmeERP.Products WHERE ProductName = 'FuncTest-FIFO'"
        )
        pid = cursor.fetchone()[0]

        today = date.today()
        movements = [
            (20, Decimal("10.00"), today - timedelta(days=3)),
            (30, Decimal("15.00"), today - timedelta(days=2)),
            (10, Decimal("20.00"), today - timedelta(days=1)),
        ]
        for qty, cost, dt in movements:
            cursor.execute(
                "INSERT INTO AcmeERP.StockMovements "
                "(ProductID, MovementDate, Quantity, UnitCost, Direction) "
                "VALUES (%s, %s, %s, %s, 'IN')",
                (pid, dt, qty, cost),
            )
        db_connection.commit()
        cursor.close()

        try:
            rows = exec_sp(
                db_connection,
                "AcmeERP.usp_CalculateFifoCost",
                {"ProductID": pid, "QuantityRequested": 25},
            )
            assert len(rows) == 1
            cost = rows[0][0]
            # RunningTotal: 20, 50, 60. Rows where <= 25: only first (20).
            # AVG of first row = 10.0
            assert cost is not None
            assert float(cost) == pytest.approx(10.0, abs=0.01)
        finally:
            cursor = db_connection.cursor()
            cursor.execute("DELETE FROM AcmeERP.StockMovements WHERE ProductID = %s", (pid,))
            cursor.execute("DELETE FROM AcmeERP.Products WHERE ProductID = %s", (pid,))
            db_connection.commit()
            cursor.close()


class TestCurrencyConversionChain:
    def test_currency_conversion_chain(self, db_connection):
        """Insert rate, convert, verify amount."""
        code = "FNC"
        cursor = db_connection.cursor()
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM AcmeERP.Currencies WHERE CurrencyCode = %s) "
            "INSERT INTO AcmeERP.Currencies (CurrencyCode, CurrencyName) VALUES (%s, %s)",
            (code, code, "Func Test"),
        )
        db_connection.commit()
        cursor.execute(
            "DELETE FROM AcmeERP.ExchangeRates WHERE CurrencyCode = %s", (code,)
        )
        db_connection.commit()
        today = date.today()
        cursor.execute(
            "INSERT INTO AcmeERP.ExchangeRates (CurrencyCode, RateDate, RateToBase) "
            "VALUES (%s, %s, 2.5)",
            (code, today),
        )
        db_connection.commit()
        cursor.close()

        try:
            rows = exec_sp(
                db_connection,
                "AcmeERP.usp_ConvertToBase",
                {"CurrencyCode": code, "Amount": Decimal("100.00"), "ConversionDate": today},
            )
            assert len(rows) == 1
            assert float(rows[0][0]) == pytest.approx(250.0, abs=0.01)
        finally:
            cursor = db_connection.cursor()
            cursor.execute(
                "DELETE FROM AcmeERP.ExchangeRates WHERE CurrencyCode = %s", (code,)
            )
            db_connection.commit()
            cursor.close()


class TestPayrollWithCurrency:
    def test_payroll_with_currency_conversion(self, db_connection):
        """Run payroll for multi-currency employees, verify converted salaries."""
        today = date.today()
        cursor = db_connection.cursor()

        # Set up known EUR rate
        cursor.execute("DELETE FROM AcmeERP.ExchangeRates WHERE CurrencyCode = 'EUR'")
        db_connection.commit()
        cursor.execute(
            "INSERT INTO AcmeERP.ExchangeRates (CurrencyCode, RateDate, RateToBase) "
            "VALUES ('EUR', %s, 1.10)",
            (today,),
        )
        db_connection.commit()
        cursor.close()

        eid_usd = _insert_employee(
            db_connection, "Func", "USD", "IT", "Analyst",
            today - timedelta(days=365), Decimal("50000.00"), "USD",
        )
        eid_eur = _insert_employee(
            db_connection, "Func", "EUR", "IT", "Analyst",
            today - timedelta(days=365), Decimal("50000.00"), "EUR",
        )

        try:
            cursor = db_connection.cursor()
            cursor.execute(
                "EXEC AcmeERP.usp_ProcessFullPayrollCycle "
                "@PayPeriodStart = %s, @PayPeriodEnd = %s",
                (today - timedelta(days=30), today),
            )
            db_connection.commit()
            cursor.close()

            cursor = db_connection.cursor()
            cursor.execute(
                "SELECT GrossSalary FROM AcmeERP.PayrollLogs WHERE EmployeeID = %s",
                (eid_usd,),
            )
            usd_gross = float(cursor.fetchone()[0])
            cursor.execute(
                "SELECT GrossSalary FROM AcmeERP.PayrollLogs WHERE EmployeeID = %s",
                (eid_eur,),
            )
            eur_gross = float(cursor.fetchone()[0])
            cursor.close()

            # USD should stay same, EUR should be multiplied by 1.10
            assert usd_gross == pytest.approx(50000.0, abs=1.0)
            assert eur_gross == pytest.approx(55000.0, abs=100.0)
        finally:
            _cleanup_employee(db_connection, eid_usd)
            _cleanup_employee(db_connection, eid_eur)
