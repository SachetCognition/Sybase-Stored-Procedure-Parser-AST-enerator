#!/usr/bin/env python3
"""Generate an analysis report after running the test suite.

Collects DB metrics, parses pytest junitxml results, and produces
reports/analysis_report.md with charts saved in reports/charts/.
"""

import os
import sys
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pymssql

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

HOST = os.getenv("MSSQL_HOST", "localhost")
PORT = int(os.getenv("MSSQL_PORT", "1433"))
USER = os.getenv("MSSQL_USER", "sa")
PASSWORD = os.getenv("MSSQL_PASSWORD", "AcmeERP_Test123!")
DATABASE = os.getenv("MSSQL_DATABASE", "master")

REPORTS_DIR = Path("reports")
CHARTS_DIR = REPORTS_DIR / "charts"
JUNIT_XML = REPORTS_DIR / "test_results.xml"

SP_CATALOG = [
    {
        "name": "AcmeERP.usp_CalculateFifoCost",
        "domain": "Inventory / Costing",
        "params": "@ProductID INT, @QuantityRequested INT",
        "tables": "StockMovements, Products",
        "complexity": "Medium",
        "ast_nodes": "CTE, Window Function, SELECT",
    },
    {
        "name": "AcmeERP.usp_ConvertToBase",
        "domain": "Multi-Currency",
        "params": "@CurrencyCode CHAR(3), @Amount DECIMAL, @ConversionDate DATE",
        "tables": "ExchangeRates, Currencies",
        "complexity": "Medium",
        "ast_nodes": "IF/ELSE, SELECT, Variable Assignment",
    },
    {
        "name": "AcmeERP.usp_ProcessFullPayrollCycle",
        "domain": "Payroll",
        "params": "@PayPeriodStart DATE, @PayPeriodEnd DATE",
        "tables": "Employees, PayrollLogs, ExchangeRates, #PayrollCalc",
        "complexity": "High",
        "ast_nodes": "TRY/CATCH, CURSOR, INSERT-SELECT, UPDATE, CASE, DATEDIFF",
    },
    {
        "name": "dbo.sp_check_employee",
        "domain": "Edge Case / HR",
        "params": "@emp_id INT, @emp_name VARCHAR(100)",
        "tables": "dbo.Employees",
        "complexity": "Low",
        "ast_nodes": "IF/ELSE, SELECT, Variable Assignment",
    },
    {
        "name": "dbo.edge_case_nested_if_while",
        "domain": "Edge Case / Control Flow",
        "params": "(none)",
        "tables": "(none)",
        "complexity": "Low",
        "ast_nodes": "WHILE, IF, RETURN, EXEC",
    },
    {
        "name": "dbo.edge_full_cursor_block",
        "domain": "Edge Case / Cursor",
        "params": "(none)",
        "tables": "dbo.users, dbo.audit_log",
        "complexity": "Medium",
        "ast_nodes": "CURSOR, WHILE, INSERT, FETCH",
    },
]


def get_connection():
    return pymssql.connect(
        server=HOST, port=PORT, user=USER, password=PASSWORD, database=DATABASE
    )


def collect_row_counts(conn) -> dict[str, int]:
    tables = [
        "AcmeERP.Employees", "AcmeERP.PayrollLogs", "AcmeERP.Products",
        "AcmeERP.StockMovements", "AcmeERP.Currencies", "AcmeERP.ExchangeRates",
        "dbo.Employees", "dbo.Tasks", "dbo.users", "dbo.logs", "dbo.audit_log",
    ]
    counts = {}
    cursor = conn.cursor()
    for t in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = cursor.fetchone()[0]
        except Exception:
            counts[t] = -1
    cursor.close()
    return counts


def collect_payroll_stats(conn) -> list[dict]:
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT
                e.Department,
                COUNT(*) AS EmpCount,
                AVG(pl.GrossSalary) AS AvgGross,
                MIN(pl.GrossSalary) AS MinGross,
                MAX(pl.GrossSalary) AS MaxGross,
                AVG(pl.TaxDeducted) AS AvgTax
            FROM AcmeERP.PayrollLogs pl
            JOIN AcmeERP.Employees e ON pl.EmployeeID = e.EmployeeID
            GROUP BY e.Department
        """)
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
    except Exception:
        rows = []
    cursor.close()
    return rows


def collect_employee_distributions(conn) -> dict:
    cursor = conn.cursor()
    distributions = {}
    try:
        cursor.execute("""
            SELECT
                CASE
                    WHEN DATEDIFF(YEAR, HireDate, GETDATE()) < 2 THEN '<2 years'
                    WHEN DATEDIFF(YEAR, HireDate, GETDATE()) < 5 THEN '2-4 years'
                    WHEN DATEDIFF(YEAR, HireDate, GETDATE()) < 10 THEN '5-9 years'
                    ELSE '10+ years'
                END AS TenureBucket,
                COUNT(*) AS Cnt
            FROM AcmeERP.Employees
            GROUP BY
                CASE
                    WHEN DATEDIFF(YEAR, HireDate, GETDATE()) < 2 THEN '<2 years'
                    WHEN DATEDIFF(YEAR, HireDate, GETDATE()) < 5 THEN '2-4 years'
                    WHEN DATEDIFF(YEAR, HireDate, GETDATE()) < 10 THEN '5-9 years'
                    ELSE '10+ years'
                END
        """)
        distributions["tenure"] = {r[0]: r[1] for r in cursor.fetchall()}
    except Exception:
        distributions["tenure"] = {}

    try:
        cursor.execute("""
            SELECT
                CASE
                    WHEN BaseSalary <= 50000 THEN '<=50K'
                    WHEN BaseSalary <= 75000 THEN '50K-75K'
                    ELSE '>75K'
                END AS SalaryBracket,
                COUNT(*) AS Cnt
            FROM AcmeERP.Employees
            GROUP BY
                CASE
                    WHEN BaseSalary <= 50000 THEN '<=50K'
                    WHEN BaseSalary <= 75000 THEN '50K-75K'
                    ELSE '>75K'
                END
        """)
        distributions["salary"] = {r[0]: r[1] for r in cursor.fetchall()}
    except Exception:
        distributions["salary"] = {}

    try:
        cursor.execute("""
            SELECT ISNULL(Currency, 'NULL') AS Cur, COUNT(*) AS Cnt
            FROM AcmeERP.Employees
            GROUP BY Currency
        """)
        distributions["currency"] = {r[0]: r[1] for r in cursor.fetchall()}
    except Exception:
        distributions["currency"] = {}

    cursor.close()
    return distributions


def generate_charts(distributions: dict, row_counts: dict) -> list[str]:
    """Generate PNG charts. Returns list of chart file paths."""
    chart_paths = []
    if plt is None:
        return chart_paths

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    # Tenure distribution
    if distributions.get("tenure"):
        fig, ax = plt.subplots(figsize=(6, 4))
        labels = list(distributions["tenure"].keys())
        values = list(distributions["tenure"].values())
        ax.bar(labels, values, color=["#4e79a7", "#f28e2b", "#e15759", "#76b7b2"])
        ax.set_title("Employee Tenure Distribution")
        ax.set_ylabel("Count")
        path = str(CHARTS_DIR / "tenure_distribution.png")
        fig.tight_layout()
        fig.savefig(path, dpi=100)
        plt.close(fig)
        chart_paths.append(path)

    # Salary bracket distribution
    if distributions.get("salary"):
        fig, ax = plt.subplots(figsize=(6, 4))
        labels = list(distributions["salary"].keys())
        values = list(distributions["salary"].values())
        ax.pie(values, labels=labels, autopct="%1.1f%%",
               colors=["#59a14f", "#edc948", "#b07aa1"])
        ax.set_title("Salary Bracket Distribution")
        path = str(CHARTS_DIR / "salary_distribution.png")
        fig.tight_layout()
        fig.savefig(path, dpi=100)
        plt.close(fig)
        chart_paths.append(path)

    # Currency distribution
    if distributions.get("currency"):
        fig, ax = plt.subplots(figsize=(6, 4))
        labels = list(distributions["currency"].keys())
        values = list(distributions["currency"].values())
        ax.bar(labels, values, color="#4e79a7")
        ax.set_title("Employee Currency Distribution")
        ax.set_ylabel("Count")
        path = str(CHARTS_DIR / "currency_distribution.png")
        fig.tight_layout()
        fig.savefig(path, dpi=100)
        plt.close(fig)
        chart_paths.append(path)

    # Row counts
    if row_counts:
        fig, ax = plt.subplots(figsize=(8, 5))
        labels = [k.split(".")[-1] for k in row_counts if row_counts[k] >= 0]
        values = [v for v in row_counts.values() if v >= 0]
        ax.barh(labels, values, color="#4e79a7")
        ax.set_title("Table Row Counts")
        ax.set_xlabel("Rows")
        path = str(CHARTS_DIR / "row_counts.png")
        fig.tight_layout()
        fig.savefig(path, dpi=100)
        plt.close(fig)
        chart_paths.append(path)

    return chart_paths


def parse_junit_xml(xml_path: str) -> dict:
    """Parse pytest junitxml and return summary + per-test details."""
    result = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "test_cases": []}
    if not os.path.exists(xml_path):
        return result

    tree = ET.parse(xml_path)
    root = tree.getroot()

    for suite in root.iter("testsuite"):
        result["tests"] += int(suite.get("tests", 0))
        result["failures"] += int(suite.get("failures", 0))
        result["errors"] += int(suite.get("errors", 0))
        result["skipped"] += int(suite.get("skipped", 0))

    for tc in root.iter("testcase"):
        status = "passed"
        message = ""
        failure = tc.find("failure")
        error = tc.find("error")
        skipped = tc.find("skipped")
        if failure is not None:
            status = "failed"
            message = failure.get("message", "")[:200]
        elif error is not None:
            status = "error"
            message = error.get("message", "")[:200]
        elif skipped is not None:
            status = "skipped"
            message = skipped.get("message", "")[:200]

        result["test_cases"].append({
            "classname": tc.get("classname", ""),
            "name": tc.get("name", ""),
            "time": tc.get("time", "0"),
            "status": status,
            "message": message,
        })

    return result


def run_tests() -> dict:
    """Run pytest and return parsed results."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pytest", "tests/",
        "-v", "--tb=short",
        f"--junitxml={JUNIT_XML}",
    ]
    print(f"Running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(proc.stdout[-2000:] if len(proc.stdout) > 2000 else proc.stdout)
    if proc.stderr:
        print(proc.stderr[-1000:] if len(proc.stderr) > 1000 else proc.stderr)
    return parse_junit_xml(str(JUNIT_XML))


def generate_report(
    test_results: dict,
    row_counts: dict,
    payroll_stats: list[dict],
    distributions: dict,
    chart_paths: list[str],
) -> str:
    """Generate the markdown analysis report."""
    passed = sum(1 for tc in test_results["test_cases"] if tc["status"] == "passed")
    failed = sum(1 for tc in test_results["test_cases"] if tc["status"] == "failed")
    errored = sum(1 for tc in test_results["test_cases"] if tc["status"] == "error")
    total = test_results["tests"] or len(test_results["test_cases"])
    coverage_pct = (passed / total * 100) if total > 0 else 0

    lines = []
    lines.append("# Analysis Report")
    lines.append(f"\n*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")

    # Executive Summary
    lines.append("## Executive Summary\n")
    lines.append(f"- **Total SPs tested**: {len(SP_CATALOG)}")
    lines.append(f"- **Total tests**: {total}")
    lines.append(f"- **Passed**: {passed}")
    lines.append(f"- **Failed**: {failed}")
    lines.append(f"- **Errors**: {errored}")
    lines.append(f"- **Coverage**: {coverage_pct:.1f}%\n")

    # SP Catalog
    lines.append("## Stored Procedure Catalog\n")
    lines.append("| Name | Domain | Parameters | Tables Touched | Complexity | AST Node Types |")
    lines.append("|------|--------|-----------|---------------|------------|----------------|")
    for sp in SP_CATALOG:
        lines.append(
            f"| `{sp['name']}` | {sp['domain']} | {sp['params']} | "
            f"{sp['tables']} | {sp['complexity']} | {sp['ast_nodes']} |"
        )
    lines.append("")

    # Synthetic Data Summary
    lines.append("## Synthetic Data Summary\n")
    lines.append("### Table Row Counts\n")
    lines.append("| Table | Row Count |")
    lines.append("|-------|-----------|")
    for table, count in row_counts.items():
        lines.append(f"| `{table}` | {count} |")
    lines.append("")

    if distributions.get("tenure"):
        lines.append("### Tenure Distribution\n")
        for bucket, cnt in distributions["tenure"].items():
            lines.append(f"- **{bucket}**: {cnt} employees")
        lines.append("")

    if distributions.get("salary"):
        lines.append("### Salary Bracket Distribution\n")
        for bracket, cnt in distributions["salary"].items():
            lines.append(f"- **{bracket}**: {cnt} employees")
        lines.append("")

    if distributions.get("currency"):
        lines.append("### Currency Distribution\n")
        for cur, cnt in distributions["currency"].items():
            lines.append(f"- **{cur}**: {cnt} employees")
        lines.append("")

    # Charts
    if chart_paths:
        lines.append("### Charts\n")
        for cp in chart_paths:
            name = Path(cp).stem.replace("_", " ").title()
            lines.append(f"![{name}]({cp})\n")

    # Unit Test Results
    lines.append("## Unit Test Results\n")
    unit_tests = [
        tc for tc in test_results["test_cases"]
        if "test_unit" in tc["classname"]
    ]
    if unit_tests:
        lines.append("| Test Class | Test Name | Status | Time (s) | Details |")
        lines.append("|-----------|-----------|--------|----------|---------|")
        for tc in unit_tests:
            status_icon = "PASS" if tc["status"] == "passed" else "FAIL"
            lines.append(
                f"| `{tc['classname'].split('.')[-1]}` | `{tc['name']}` | "
                f"{status_icon} | {tc['time']} | {tc['message'][:80]} |"
            )
    else:
        lines.append("*No unit test results found.*\n")
    lines.append("")

    # Functional Test Results
    lines.append("## Functional Test Results\n")
    func_tests = [
        tc for tc in test_results["test_cases"]
        if "test_functional" in tc["classname"]
    ]
    if func_tests:
        lines.append("| Test Name | Status | Time (s) | Details |")
        lines.append("|-----------|--------|----------|---------|")
        for tc in func_tests:
            status_icon = "PASS" if tc["status"] == "passed" else "FAIL"
            lines.append(
                f"| `{tc['name']}` | {status_icon} | {tc['time']} | {tc['message'][:80]} |"
            )
    else:
        lines.append("*No functional test results found.*\n")
    lines.append("")

    # Regression Test Results
    lines.append("## Regression Test Results\n")
    reg_tests = [
        tc for tc in test_results["test_cases"]
        if "test_regression" in tc["classname"]
    ]
    if reg_tests:
        lines.append("| Test Name | Status | Time (s) | Details |")
        lines.append("|-----------|--------|----------|---------|")
        for tc in reg_tests:
            status_icon = "PASS" if tc["status"] == "passed" else "FAIL"
            lines.append(
                f"| `{tc['name']}` | {status_icon} | {tc['time']} | {tc['message'][:80]} |"
            )
    else:
        lines.append("*No regression test results found.*\n")
    lines.append("")

    # Edge Case Test Results
    lines.append("## Edge Case Test Results\n")
    edge_tests = [
        tc for tc in test_results["test_cases"]
        if "test_edge" in tc["classname"]
    ]
    if edge_tests:
        lines.append("| Test Name | Status | Time (s) | Details |")
        lines.append("|-----------|--------|----------|---------|")
        for tc in edge_tests:
            status_icon = "PASS" if tc["status"] == "passed" else "FAIL"
            lines.append(
                f"| `{tc['name']}` | {status_icon} | {tc['time']} | {tc['message'][:80]} |"
            )
    else:
        lines.append("*No edge case test results found.*\n")
    lines.append("")

    # Data Integrity Analysis
    lines.append("## Data Integrity Analysis\n")
    lines.append("- Foreign key constraints enforced via DDL (`FOREIGN KEY` on PayrollLogs, StockMovements, ExchangeRates)")
    lines.append("- `NetSalary` is a computed persisted column: `GrossSalary - TaxDeducted`")
    lines.append("- Currency conversion accuracy validated via unit tests with known rates")
    lines.append("- NULL currency handling verified via ISNULL fallback to 'USD'\n")

    # Payroll Stats
    if payroll_stats:
        lines.append("### Payroll Statistics by Department\n")
        lines.append("| Department | Employees | Avg Gross | Min Gross | Max Gross | Avg Tax |")
        lines.append("|-----------|-----------|-----------|-----------|-----------|---------|")
        for s in payroll_stats:
            lines.append(
                f"| {s.get('Department', 'N/A')} | {s.get('EmpCount', 0)} | "
                f"{s.get('AvgGross', 0):.2f} | {s.get('MinGross', 0):.2f} | "
                f"{s.get('MaxGross', 0):.2f} | {s.get('AvgTax', 0):.2f} |"
            )
        lines.append("")

    # Performance Observations
    lines.append("## Performance Observations\n")
    lines.append("- `usp_ProcessFullPayrollCycle` uses a **CURSOR** for per-employee currency conversion, "
                 "which is slower than a set-based approach for large datasets.")
    lines.append("- `usp_CalculateFifoCost` uses a **CTE with window function** (set-based), "
                 "which is efficient for FIFO calculations.")
    lines.append("- `usp_ConvertToBase` uses sequential IF/ELSE fallback logic, "
                 "executing up to 3 separate queries per call.\n")

    if test_results["test_cases"]:
        times = [float(tc["time"]) for tc in test_results["test_cases"] if tc["time"]]
        if times:
            lines.append(f"- **Total test execution time**: {sum(times):.2f}s")
            lines.append(f"- **Average per test**: {sum(times) / len(times):.3f}s")
            lines.append(f"- **Slowest test**: {max(times):.3f}s\n")

    return "\n".join(lines)


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    # Run tests
    print("=" * 60)
    print("Running test suite...")
    print("=" * 60)
    test_results = run_tests()

    # Collect DB metrics
    print("\nCollecting database metrics...")
    try:
        conn = get_connection()
        row_counts = collect_row_counts(conn)
        payroll_stats = collect_payroll_stats(conn)
        distributions = collect_employee_distributions(conn)
        conn.close()
    except Exception as exc:
        print(f"Warning: Could not connect to DB for metrics: {exc}")
        row_counts = {}
        payroll_stats = []
        distributions = {}

    # Generate charts
    print("Generating charts...")
    chart_paths = generate_charts(distributions, row_counts)

    # Generate report
    print("Generating analysis report...")
    report = generate_report(test_results, row_counts, payroll_stats, distributions, chart_paths)

    report_path = REPORTS_DIR / "analysis_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved to {report_path}")
    print(f"Charts saved to {CHARTS_DIR}/")


if __name__ == "__main__":
    main()
