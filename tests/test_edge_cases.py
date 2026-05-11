"""Tests for edge-case SPs (sample_1-5, edge_case_*, edge_cursor_*, edge_full_cursor_*)."""

import pytest

from tests.conftest import exec_sp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_dbo_employees(conn):
    """Insert test rows into dbo.Employees if not present."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM dbo.Employees")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO dbo.Employees (EmployeeID, Name, Department, Salary) VALUES "
            "(1, 'Alice Smith', 'Engineering', 85000.00), "
            "(2, 'Bob Jones', 'HR', 55000.00)"
        )
        conn.commit()
    cursor.close()


def _ensure_dbo_tasks(conn):
    """Insert test rows into dbo.Tasks if not present."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM dbo.Tasks")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO dbo.Tasks (EmployeeID, TaskName, Status) VALUES "
            "(1, 'Review PR', 'Pending'), "
            "(1, 'Deploy feature', 'InProgress'), "
            "(1, 'Write docs', 'Completed'), "
            "(2, 'Onboarding', 'Pending')"
        )
        conn.commit()
    cursor.close()


def _ensure_dbo_users(conn):
    """Insert test rows into dbo.users if not present."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM dbo.users")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO dbo.users (id, name, active) VALUES "
            "(1, 'user_alice', 1), "
            "(2, 'user_bob', 1), "
            "(3, 'user_charlie', 0)"
        )
        conn.commit()
    cursor.close()


def _create_sp_if_needed(conn, sp_name: str, sql_file_content: str):
    """Create a stored procedure from SQL content if it doesn't exist."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql_file_content)
        conn.commit()
    except Exception:
        conn.rollback()
    cursor.close()


# ---------------------------------------------------------------------------
# Test: sp_check_employee (sample_1.sql)
# ---------------------------------------------------------------------------

class TestSpCheckEmployee:
    @pytest.fixture(autouse=True)
    def _setup(self, db_connection):
        _ensure_dbo_employees(db_connection)
        # Create the SP
        cursor = db_connection.cursor()
        try:
            cursor.execute("""
                IF OBJECT_ID('dbo.sp_check_employee', 'P') IS NOT NULL
                    DROP PROCEDURE dbo.sp_check_employee
            """)
            db_connection.commit()
            cursor.execute("""
                CREATE PROCEDURE dbo.sp_check_employee
                    @emp_id INT,
                    @emp_name VARCHAR(100)
                AS
                BEGIN
                    DECLARE @emp_exists INT
                    DECLARE @emp_message VARCHAR(200)
                    SET @emp_exists = 0
                    SELECT @emp_exists = COUNT(*)
                    FROM dbo.Employees
                    WHERE EmployeeID = @emp_id AND Name = @emp_name
                    IF @emp_exists > 0
                    BEGIN
                        SET @emp_message = 'Employee found'
                    END
                    ELSE
                    BEGIN
                        SET @emp_message = 'Employee not found'
                    END
                    SELECT @emp_message AS Message
                END
            """)
            db_connection.commit()
        except Exception:
            db_connection.rollback()
        cursor.close()

    def test_sp_check_employee_found(self, db_connection):
        """Existing employee -> returns 'Employee found'."""
        rows = exec_sp(
            db_connection,
            "dbo.sp_check_employee",
            {"emp_id": 1, "emp_name": "Alice Smith"},
        )
        assert len(rows) == 1
        assert rows[0][0] == "Employee found"

    def test_sp_check_employee_not_found(self, db_connection):
        """Non-existent employee -> returns 'Employee not found'."""
        rows = exec_sp(
            db_connection,
            "dbo.sp_check_employee",
            {"emp_id": 999, "emp_name": "Nobody"},
        )
        assert len(rows) == 1
        assert rows[0][0] == "Employee not found"


# ---------------------------------------------------------------------------
# Test: edge_case_nested_if_while
# ---------------------------------------------------------------------------

class TestEdgeNestedIfWhile:
    @pytest.fixture(autouse=True)
    def _setup(self, db_connection):
        cursor = db_connection.cursor()
        try:
            cursor.execute("""
                IF OBJECT_ID('dbo.log_event', 'P') IS NOT NULL
                    DROP PROCEDURE dbo.log_event
            """)
            db_connection.commit()
            cursor.execute("""
                CREATE PROCEDURE dbo.log_event @val INT
                AS
                BEGIN
                    -- no-op stub for testing
                    SELECT @val AS LoggedValue
                END
            """)
            db_connection.commit()
        except Exception:
            db_connection.rollback()

        try:
            cursor.execute("""
                IF OBJECT_ID('dbo.edge_case_nested_if_while', 'P') IS NOT NULL
                    DROP PROCEDURE dbo.edge_case_nested_if_while
            """)
            db_connection.commit()
            cursor.execute("""
                CREATE PROCEDURE dbo.edge_case_nested_if_while
                AS
                BEGIN
                    DECLARE @i INT
                    SET @i = 0
                    WHILE @i < 3
                    BEGIN
                        SET @i = @i + 1
                        IF @i = 2
                        BEGIN
                            EXEC dbo.log_event @i
                            RETURN -1
                        END
                    END
                    RETURN 0
                END
            """)
            db_connection.commit()
        except Exception:
            db_connection.rollback()
        cursor.close()

    def test_edge_nested_if_while(self, db_connection):
        """Verify return value is -1 when @i hits 2."""
        cursor = db_connection.cursor()
        cursor.execute("""
            DECLARE @rc INT
            EXEC @rc = dbo.edge_case_nested_if_while
            SELECT @rc AS ReturnCode
        """)
        # The SP calls log_event which returns a result set (the logged value).
        # Skip through result sets until we find the ReturnCode.
        row = cursor.fetchone()
        # First result set is from log_event: (2,). Skip to next.
        while True:
            try:
                if cursor.nextset():
                    row = cursor.fetchone()
                else:
                    break
            except Exception:
                break
        db_connection.commit()
        cursor.close()
        assert row is not None
        assert row[0] == -1


# ---------------------------------------------------------------------------
# Test: edge_full_cursor_block
# ---------------------------------------------------------------------------

class TestEdgeFullCursorBlock:
    @pytest.fixture(autouse=True)
    def _setup(self, db_connection):
        _ensure_dbo_users(db_connection)
        cursor = db_connection.cursor()
        # Clear audit_log
        try:
            cursor.execute("DELETE FROM dbo.audit_log")
            db_connection.commit()
        except Exception:
            db_connection.rollback()

        try:
            cursor.execute("""
                IF OBJECT_ID('dbo.edge_full_cursor_block', 'P') IS NOT NULL
                    DROP PROCEDURE dbo.edge_full_cursor_block
            """)
            db_connection.commit()
            cursor.execute("""
                CREATE PROCEDURE dbo.edge_full_cursor_block
                AS
                BEGIN
                    DECLARE @id INT
                    DECLARE @name VARCHAR(100)

                    DECLARE user_cursor CURSOR FOR
                    SELECT id, name FROM dbo.users WHERE active = 1

                    OPEN user_cursor
                    FETCH NEXT FROM user_cursor INTO @id, @name

                    WHILE @@FETCH_STATUS = 0
                    BEGIN
                        INSERT INTO dbo.audit_log(user_id, message)
                        VALUES (@id, @name)
                        FETCH NEXT FROM user_cursor INTO @id, @name
                    END

                    CLOSE user_cursor
                    DEALLOCATE user_cursor
                END
            """)
            db_connection.commit()
        except Exception:
            db_connection.rollback()
        cursor.close()

    def test_edge_full_cursor_block(self, db_connection):
        """Verify audit_log gets populated from active users."""
        cursor = db_connection.cursor()
        cursor.execute("EXEC dbo.edge_full_cursor_block")
        db_connection.commit()

        cursor.execute("SELECT COUNT(*) FROM dbo.audit_log")
        count = cursor.fetchone()[0]
        cursor.close()

        # We have 2 active users (user_alice, user_bob)
        assert count >= 2
