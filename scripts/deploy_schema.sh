#!/bin/bash
set -e

HOST="${MSSQL_HOST:-localhost}"
PORT="${MSSQL_PORT:-1433}"
USER="${MSSQL_USER:-sa}"
PASSWORD="${MSSQL_PASSWORD:-AcmeERP_Test123!}"

SQL_FILES=(
    "input/01_create_schema.sql"
    "input/02_payroll_tables.sql"
    "input/03_inventory_costing_tables.sql"
    "input/04_multi_currency_tables.sql"
    "input/05_sample_data.sql"
    "input/06_sp_CalculateFifoCost.sql"
    "input/07_sp_ConvertToBase.sql"
    "input/08_sp_ProcessFullPayrollCycle.sql"
)

echo "Waiting for SQL Server to be healthy..."
MAX_RETRIES=30
RETRY=0
until /opt/mssql-tools18/bin/sqlcmd -S "$HOST,$PORT" -U "$USER" -P "$PASSWORD" -C -Q "SELECT 1" &>/dev/null; do
    RETRY=$((RETRY + 1))
    if [ $RETRY -ge $MAX_RETRIES ]; then
        echo "ERROR: SQL Server did not become healthy after $MAX_RETRIES retries."
        exit 1
    fi
    echo "  Retry $RETRY/$MAX_RETRIES..."
    sleep 5
done
echo "SQL Server is healthy."

for FILE in "${SQL_FILES[@]}"; do
    echo "Executing $FILE..."
    /opt/mssql-tools18/bin/sqlcmd -S "$HOST,$PORT" -U "$USER" -P "$PASSWORD" -C -i "$FILE"
    echo "  Done."
done

echo "Schema deployment complete."
