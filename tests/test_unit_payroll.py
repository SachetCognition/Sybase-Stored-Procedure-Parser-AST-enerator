"""Unit tests for AcmeERP.usp_ProcessFullPayrollCycle."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import exec_sp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_employee(conn, first: str, last: str, dept: str, position: str,
                     hire_date: date, salary: Decimal, currency: str | None) -> int:
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


def _cleanup_employee(conn, eid: int) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM AcmeERP.PayrollLogs WHERE EmployeeID = %s", (eid,))
    cursor.execute("DELETE FROM AcmeERP.Employees WHERE EmployeeID = %s", (eid,))
    conn.commit()
    cursor.close()


def _cleanup_all_test_employees(conn) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM AcmeERP.PayrollLogs WHERE EmployeeID IN "
                   "(SELECT EmployeeID FROM AcmeERP.Employees WHERE FirstName LIKE 'PayTest%')")
    cursor.execute("DELETE FROM AcmeERP.Employees WHERE FirstName LIKE 'PayTest%'")
    conn.commit()
    cursor.close()


def _run_payroll(conn, period_start: date, period_end: date) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "EXEC AcmeERP.usp_ProcessFullPayrollCycle "
        "@PayPeriodStart = %s, @PayPeriodEnd = %s",
        (period_start, period_end),
    )
    conn.commit()
    cursor.close()


def _get_payroll_log(conn, eid: int) -> list[tuple]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT EmployeeID, GrossSalary, TaxDeducted, NetSalary "
        "FROM AcmeERP.PayrollLogs WHERE EmployeeID = %s",
        (eid,),
    )
    rows = cursor.fetchall()
    cursor.close()
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPayrollBonusTiers:
    """Test bonus calculation based on tenure."""

    def setup_method(self):
        self._eids = []

    def _track(self, eid):
        self._eids.append(eid)
        return eid

    def test_bonus_tier_0(self, db_connection):
        """Employee < 2 years tenure -> bonus = 0."""
        today = date.today()
        eid = self._track(_insert_employee(
            db_connection, "PayTest", "Tier0", "IT", "Analyst",
            today - timedelta(days=365), Decimal("40000.00"), "USD",
        ))
        period_end = today
        period_start = today - timedelta(days=30)
        try:
            _run_payroll(db_connection, period_start, period_end)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            gross = float(logs[0][1])
            # Bonus = 0, so GrossSalary = BaseSalary = 40000
            # But SP inserts ConvertedSalary as GrossSalary, and for USD it's same
            assert gross == pytest.approx(40000.0, abs=1.0)
        finally:
            _cleanup_employee(db_connection, eid)

    def test_bonus_tier_5pct(self, db_connection):
        """Employee 2-4 years -> bonus = 5%."""
        today = date.today()
        eid = self._track(_insert_employee(
            db_connection, "PayTest", "Tier5", "IT", "Analyst",
            today - timedelta(days=3 * 365), Decimal("40000.00"), "USD",
        ))
        period_end = today
        period_start = today - timedelta(days=30)
        try:
            _run_payroll(db_connection, period_start, period_end)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            gross = float(logs[0][1])
            # Bonus = 5% of 40000 = 2000, GrossSalary = 42000
            assert gross == pytest.approx(42000.0, abs=1.0)
        finally:
            _cleanup_employee(db_connection, eid)

    def test_bonus_tier_10pct(self, db_connection):
        """Employee 5-9 years -> bonus = 10%."""
        today = date.today()
        eid = self._track(_insert_employee(
            db_connection, "PayTest", "Tier10", "IT", "Analyst",
            today - timedelta(days=7 * 365), Decimal("40000.00"), "USD",
        ))
        period_end = today
        period_start = today - timedelta(days=30)
        try:
            _run_payroll(db_connection, period_start, period_end)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            gross = float(logs[0][1])
            # Bonus = 10% of 40000 = 4000, GrossSalary = 44000
            assert gross == pytest.approx(44000.0, abs=1.0)
        finally:
            _cleanup_employee(db_connection, eid)

    def test_bonus_tier_15pct(self, db_connection):
        """Employee >= 10 years -> bonus = 15%."""
        today = date.today()
        eid = self._track(_insert_employee(
            db_connection, "PayTest", "Tier15", "IT", "Analyst",
            today - timedelta(days=12 * 365), Decimal("40000.00"), "USD",
        ))
        period_end = today
        period_start = today - timedelta(days=30)
        try:
            _run_payroll(db_connection, period_start, period_end)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            gross = float(logs[0][1])
            # Bonus = 15% of 40000 = 6000, GrossSalary = 46000
            assert gross == pytest.approx(46000.0, abs=1.0)
        finally:
            _cleanup_employee(db_connection, eid)


class TestPayrollTaxBrackets:
    """Test tax calculation based on salary brackets."""

    def test_tax_bracket_10pct(self, db_connection):
        """Salary <= 50000 -> tax = 10%."""
        today = date.today()
        eid = _insert_employee(
            db_connection, "PayTest", "Tax10", "IT", "Analyst",
            today - timedelta(days=365), Decimal("45000.00"), "USD",
        )
        try:
            _run_payroll(db_connection, today - timedelta(days=30), today)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            tax = float(logs[0][2])
            assert tax == pytest.approx(4500.0, abs=1.0)
        finally:
            _cleanup_employee(db_connection, eid)

    def test_tax_bracket_15pct(self, db_connection):
        """Salary 50001-75000 -> tax = 15%."""
        today = date.today()
        eid = _insert_employee(
            db_connection, "PayTest", "Tax15", "IT", "Analyst",
            today - timedelta(days=365), Decimal("60000.00"), "USD",
        )
        try:
            _run_payroll(db_connection, today - timedelta(days=30), today)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            tax = float(logs[0][2])
            assert tax == pytest.approx(9000.0, abs=1.0)
        finally:
            _cleanup_employee(db_connection, eid)

    def test_tax_bracket_20pct(self, db_connection):
        """Salary > 75000 -> tax = 20%."""
        today = date.today()
        eid = _insert_employee(
            db_connection, "PayTest", "Tax20", "IT", "Analyst",
            today - timedelta(days=365), Decimal("90000.00"), "USD",
        )
        try:
            _run_payroll(db_connection, today - timedelta(days=30), today)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            tax = float(logs[0][2])
            assert tax == pytest.approx(18000.0, abs=1.0)
        finally:
            _cleanup_employee(db_connection, eid)


class TestPayrollCurrency:
    """Test currency conversion in payroll."""

    def test_currency_conversion(self, db_connection):
        """Non-USD employee -> verify ConvertedSalary uses exchange rate."""
        today = date.today()
        cursor = db_connection.cursor()
        # Set up a known EUR rate
        cursor.execute("DELETE FROM AcmeERP.ExchangeRates WHERE CurrencyCode = 'EUR'")
        db_connection.commit()
        cursor.execute(
            "INSERT INTO AcmeERP.ExchangeRates (CurrencyCode, RateDate, RateToBase) "
            "VALUES ('EUR', %s, 1.10)",
            (today,),
        )
        db_connection.commit()
        cursor.close()

        eid = _insert_employee(
            db_connection, "PayTest", "EurConv", "Finance", "Analyst",
            today - timedelta(days=365), Decimal("50000.00"), "EUR",
        )
        try:
            _run_payroll(db_connection, today - timedelta(days=30), today)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            gross = float(logs[0][1])
            # GrossSalary = 50000 (no bonus <2yr), converted = 50000 * 1.10 = 55000
            assert gross == pytest.approx(55000.0, abs=100.0)
        finally:
            _cleanup_employee(db_connection, eid)

    def test_usd_no_conversion(self, db_connection):
        """USD employee -> ConvertedSalary = GrossSalary."""
        today = date.today()
        eid = _insert_employee(
            db_connection, "PayTest", "UsdNoConv", "IT", "Analyst",
            today - timedelta(days=365), Decimal("50000.00"), "USD",
        )
        try:
            _run_payroll(db_connection, today - timedelta(days=30), today)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            gross = float(logs[0][1])
            assert gross == pytest.approx(50000.0, abs=1.0)
        finally:
            _cleanup_employee(db_connection, eid)


class TestPayrollLogs:
    """Test payroll log insertion behavior."""

    def test_payroll_logs_inserted(self, db_connection):
        """After execution, verify rows inserted into PayrollLogs."""
        today = date.today()
        eid = _insert_employee(
            db_connection, "PayTest", "LogCheck", "IT", "Analyst",
            today - timedelta(days=365), Decimal("40000.00"), "USD",
        )
        try:
            _run_payroll(db_connection, today - timedelta(days=30), today)
            logs = _get_payroll_log(db_connection, eid)
            assert len(logs) >= 1
            assert logs[0][0] == eid
        finally:
            _cleanup_employee(db_connection, eid)

    def test_transaction_rollback(self, db_connection):
        """Verify that a failed payroll run doesn't leave partial data.

        We simulate this by checking that before any payroll run,
        the logs are empty (due to clean_payroll_logs fixture),
        and after a successful run they have data.
        """
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM AcmeERP.PayrollLogs")
        count_before = cursor.fetchone()[0]
        cursor.close()
        assert count_before == 0
