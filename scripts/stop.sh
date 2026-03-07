#!/bin/bash
# Stop all ZEROAUDIT services

echo "🛑 Stopping ZEROAUDIT services..."
docker-compose down
echo "✅ Services stopped."