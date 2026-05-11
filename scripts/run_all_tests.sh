#!/bin/bash
set -e

echo "Starting SQL Server..."
docker-compose up -d mssql
# Wait for healthy
sleep 30

echo "Deploying schema and SPs..."
python scripts/deploy_schema.py

echo "Generating synthetic data..."
python scripts/generate_synthetic_data.py

echo "Running test suite..."
pytest tests/ -v --tb=short --junitxml=reports/test_results.xml

echo "Generating analysis report..."
python scripts/generate_report.py

echo "Done. Report at reports/analysis_report.md"
