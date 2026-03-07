# ZEROAUDIT Windows Setup Script
# Run this script in PowerShell as Administrator (if needed) to set up the project.

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "🔧 ZEROAUDIT Setup Script (Windows)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check if Docker is installed
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Docker not found. Please install Docker Desktop from https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
    exit 1
}

# Check if Docker is running
$dockerProcess = Get-Process "*Docker*" -ErrorAction SilentlyContinue
if (-not $dockerProcess) {
    Write-Host "⚠️  Docker Desktop is not running. Attempting to start..." -ForegroundColor Yellow
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Write-Host "Waiting 20 seconds for Docker to start..." -ForegroundColor Yellow
    Start-Sleep -Seconds 20
}

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Python not found. Please install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

# Create virtual environment if not exists
if (-not (Test-Path ".\venv")) {
    Write-Host "📦 Creating Python virtual environment..." -ForegroundColor Yellow
    python -m venv venv
}

# Activate virtual environment
Write-Host "🔌 Activating virtual environment..." -ForegroundColor Yellow
& .\venv\Scripts\Activate.ps1

# Install Python dependencies
Write-Host "📚 Installing Python dependencies..." -ForegroundColor Yellow
pip install --upgrade pip
pip install -r requirements.txt

# Start Docker Compose
Write-Host "🐳 Starting Docker containers..." -ForegroundColor Yellow
docker-compose up -d

Write-Host "⏳ Waiting 45 seconds for services to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 45

# Check if Kafka Connect is ready
$retries = 0
$maxRetries = 10
$connected = $false
while ($retries -lt $maxRetries) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8083" -Method GET -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            $connected = $true
            break
        }
    } catch {
        # ignore
    }
    $retries++
    Write-Host "⏳ Waiting for Kafka Connect... ($retries/$maxRetries)" -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}

if ($connected) {
    Write-Host "✅ Kafka Connect is ready." -ForegroundColor Green

    # Register Debezium connector
    Write-Host "🔌 Registering Debezium PostgreSQL connector..." -ForegroundColor Yellow
    $config = Get-Content -Path "services/debezium/connector-config.json" -Raw
    try {
        $result = Invoke-RestMethod -Uri "http://localhost:8083/connectors" -Method Post -Body $config -ContentType "application/json"
        Write-Host "✅ Connector registered: $($result.name)" -ForegroundColor Green
    } catch {
        Write-Host "⚠️  Connector may already exist or failed: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "⚠️  Kafka Connect not ready. You may need to register the connector manually later." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "🎉 Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Activate venv (if not already): .\venv\Scripts\Activate"
Write-Host "2. Start prover: cd prover; python -m uvicorn main:app --reload --port 8000"
Write-Host "3. Start dashboard: cd verifier; streamlit run dashboard.py"
Write-Host "4. Run simulator: cd simulator; python generate.py"
Write-Host "5. Open dashboard: http://localhost:8501"