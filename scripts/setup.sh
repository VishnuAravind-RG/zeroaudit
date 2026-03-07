#!/bin/bash
# ZEROAUDIT Setup Script for Linux/macOS

set -e

echo "========================================"
echo "🔧 ZEROAUDIT Setup Script (Linux/macOS)"
echo "========================================"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker."
    exit 1
fi

# Check Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose not found. Please install Docker Compose."
    exit 1
fi

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Please install Python 3.10+."
    exit 1
fi

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📚 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Start Docker Compose
echo "🐳 Starting Docker containers..."
docker-compose up -d

echo "⏳ Waiting 45 seconds for services to initialize..."
sleep 45

# Check Kafka Connect
echo "🔌 Registering Debezium PostgreSQL connector..."
curl -X POST -H "Content-Type: application/json" --data @services/debezium/connector-config.json http://localhost:8083/connectors || echo "⚠️  Connector may already exist."

echo ""
echo "🎉 Setup complete!"
echo ""
echo "Next steps:"
echo "1. Activate venv: source venv/bin/activate"
echo "2. Start prover: cd prover; python -m uvicorn main:app --reload --port 8000"
echo "3. Start dashboard: cd verifier; streamlit run dashboard.py"
echo "4. Run simulator: cd simulator; python generate.py"
echo "5. Open dashboard: http://localhost:8501"