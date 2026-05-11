"""Unit tests for AcmeERP.usp_CalculateFifoCost."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import exec_sp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_product(conn, name: str, movements: list[tuple]) -> int:
    """Create a product and insert IN movements. Returns ProductID."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO AcmeERP.Products (ProductName, Category, CostMethod, CurrentStock) "
        "VALUES (%s, 'Test', 'FIFO', 0)",
        (name,),
    )
    conn.commit()
    cursor.execute(
        "SELECT ProductID FROM AcmeERP.Products WHERE ProductName = %s", (name,)
    )
    pid = cursor.fetchone()[0]
    today = date.today()
    for i, (qty, cost, direction) in enumerate(movements):
        cursor.execute(
            "INSERT INTO AcmeERP.StockMovements "
            "(ProductID, MovementDate, Quantity, UnitCost, Direction) "
            "VALUES (%s, %s, %s, %s, %s)",
            (pid, today - timedelta(days=len(movements) - i), qty, cost, direction),
        )
    conn.commit()
    cursor.close()
    return pid


def _cleanup_product(conn, pid: int) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM AcmeERP.StockMovements WHERE ProductID = %s", (pid,))
    cursor.execute("DELETE FROM AcmeERP.Products WHERE ProductID = %s", (pid,))
    conn.commit()
    cursor.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFifoCost:
    def test_fifo_basic(self, db_connection):
        """3 batches of 10 at costs 5, 10, 15; request 20 -> AVG of first two."""
        pid = _setup_product(
            db_connection,
            "FIFO_Basic_Test",
            [(10, Decimal("5.00"), "IN"),
             (10, Decimal("10.00"), "IN"),
             (10, Decimal("15.00"), "IN")],
        )
        try:
            rows = exec_sp(
                db_connection,
                "AcmeERP.usp_CalculateFifoCost",
                {"ProductID": pid, "QuantityRequested": 20},
            )
            assert len(rows) == 1
            cost = rows[0][0]
            # Running totals: 10, 20, 30. Rows where RunningTotal <= 20 => first two.
            # AVG(5, 10) = 7.5
            assert cost is not None
            assert float(cost) == pytest.approx(7.5, abs=0.01)
        finally:
            _cleanup_product(db_connection, pid)

    def test_fifo_exact_match(self, db_connection):
        """QuantityRequested exactly equals total available stock."""
        pid = _setup_product(
            db_connection,
            "FIFO_Exact_Test",
            [(10, Decimal("5.00"), "IN"),
             (10, Decimal("10.00"), "IN")],
        )
        try:
            rows = exec_sp(
                db_connection,
                "AcmeERP.usp_CalculateFifoCost",
                {"ProductID": pid, "QuantityRequested": 20},
            )
            assert len(rows) == 1
            cost = rows[0][0]
            assert cost is not None
            assert float(cost) == pytest.approx(7.5, abs=0.01)
        finally:
            _cleanup_product(db_connection, pid)

    def test_fifo_exceeds_stock(self, db_connection):
        """QuantityRequested > total IN quantity -> returns AVG of all or NULL."""
        pid = _setup_product(
            db_connection,
            "FIFO_Exceed_Test",
            [(5, Decimal("8.00"), "IN")],
        )
        try:
            rows = exec_sp(
                db_connection,
                "AcmeERP.usp_CalculateFifoCost",
                {"ProductID": pid, "QuantityRequested": 100},
            )
            assert len(rows) == 1
            cost = rows[0][0]
            # RunningTotal = 5, which is <= 100, so AVG(8) = 8
            assert cost is not None
            assert float(cost) == pytest.approx(8.0, abs=0.01)
        finally:
            _cleanup_product(db_connection, pid)

    def test_fifo_no_in_movements(self, db_connection):
        """Product with only OUT movements -> should return NULL."""
        pid = _setup_product(
            db_connection,
            "FIFO_NoIN_Test",
            [(10, Decimal("5.00"), "OUT")],
        )
        try:
            rows = exec_sp(
                db_connection,
                "AcmeERP.usp_CalculateFifoCost",
                {"ProductID": pid, "QuantityRequested": 10},
            )
            assert len(rows) == 1
            assert rows[0][0] is None
        finally:
            _cleanup_product(db_connection, pid)

    def test_fifo_single_batch(self, db_connection):
        """Only one IN movement -> cost equals that batch's UnitCost."""
        pid = _setup_product(
            db_connection,
            "FIFO_Single_Test",
            [(50, Decimal("12.50"), "IN")],
        )
        try:
            # Request exactly the batch quantity so RunningTotal (50) <= 50
            rows = exec_sp(
                db_connection,
                "AcmeERP.usp_CalculateFifoCost",
                {"ProductID": pid, "QuantityRequested": 50},
            )
            assert len(rows) == 1
            cost = rows[0][0]
            assert cost is not None
            assert float(cost) == pytest.approx(12.50, abs=0.01)
        finally:
            _cleanup_product(db_connection, pid)
