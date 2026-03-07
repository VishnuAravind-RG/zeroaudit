# ZEROAUDIT Health Check Script
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "🔍 ZEROAUDIT Health Check" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check Docker containers
Write-Host "`n📦 Docker Containers:" -ForegroundColor Yellow
$containers = docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker not running or error." -ForegroundColor Red
} else {
    Write-Host $containers
}

# Check PostgreSQL connectivity
Write-Host "`n🐘 PostgreSQL:" -ForegroundColor Yellow
try {
    $connString = "host=localhost port=5432 dbname=zeroaudit user=audit_user password=StrongPass123! connect_timeout=5"
    # Use psql if available, otherwise attempt a simple TCP check
    if (Get-Command psql -ErrorAction SilentlyContinue) {
        $result = psql -d "postgresql://audit_user:StrongPass123!@localhost:5432/zeroaudit" -c "SELECT 1" -t
        if ($result -match "1") {
            Write-Host "✅ PostgreSQL is reachable." -ForegroundColor Green
        } else {
            Write-Host "❌ PostgreSQL query failed." -ForegroundColor Red
        }
    } else {
        # TCP port check
        $tcpTest = Test-NetConnection -ComputerName localhost -Port 5432 -WarningAction SilentlyContinue
        if ($tcpTest.TcpTestSucceeded) {
            Write-Host "✅ PostgreSQL port 5432 is open." -ForegroundColor Green
        } else {
            Write-Host "❌ PostgreSQL port 5432 not reachable." -ForegroundColor Red
        }
    }
} catch {
    Write-Host "❌ PostgreSQL check failed: $_" -ForegroundColor Red
}

# Check Kafka
Write-Host "`n📨 Kafka:" -ForegroundColor Yellow
try {
    $kafkaTest = docker exec zeroaudit-kafka kafka-topics --bootstrap-server localhost:9092 --list 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Kafka is reachable." -ForegroundColor Green
        Write-Host "   Topics: $kafkaTest"
    } else {
        Write-Host "❌ Kafka not reachable." -ForegroundColor Red
    }
} catch {
    Write-Host "❌ Kafka check failed." -ForegroundColor Red
}

# Check Kafka Connect
Write-Host "`n🔌 Kafka Connect:" -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8083" -Method GET -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Host "✅ Kafka Connect is up." -ForegroundColor Green
        # List connectors
        $connectors = Invoke-RestMethod -Uri "http://localhost:8083/connectors" -Method GET
        if ($connectors.Count -gt 0) {
            Write-Host "   Connectors: $($connectors -join ', ')"
        } else {
            Write-Host "   No connectors registered." -ForegroundColor Yellow
        }
    } else {
        Write-Host "❌ Kafka Connect returned status $($response.StatusCode)." -ForegroundColor Red
    }
} catch {
    Write-Host "❌ Kafka Connect not reachable." -ForegroundColor Red
}

# Check Prover API
Write-Host "`n⚙️  Prover API:" -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -Method GET -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Host "✅ Prover API is up." -ForegroundColor Green
    } else {
        Write-Host "❌ Prover API returned status $($response.StatusCode)." -ForegroundColor Red
    }
} catch {
    Write-Host "❌ Prover API not reachable." -ForegroundColor Red
}

# Check Dashboard (Streamlit)
Write-Host "`n📊 Auditor Dashboard:" -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8501" -Method GET -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Host "✅ Dashboard is up." -ForegroundColor Green
    } else {
        Write-Host "❌ Dashboard returned status $($response.StatusCode)." -ForegroundColor Red
    }
} catch {
    Write-Host "❌ Dashboard not reachable." -ForegroundColor Red
}

Write-Host "`n========================================" -ForegroundColor Cyan