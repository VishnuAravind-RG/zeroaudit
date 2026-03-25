"""
verifier/api.py – REST API endpoints for dashboard
"""

from fastapi import APIRouter
from typing import List, Dict, Any
from .kafka_client.consumer import ledger, stats

router = APIRouter()

@router.get("/ledger")
async def get_ledger(limit: int = 20) -> List[Dict[str, Any]]:
    """Return the latest commitments from the ledger."""
    # `ledger` is a list stored in the consumer (ring buffer)
    return list(ledger)[:limit]

@router.get("/stats")
async def get_stats() -> Dict[str, Any]:
    """Return pipeline statistics."""
    return stats

@router.get("/quarantine")
async def get_quarantine(limit: int = 10) -> List[Dict[str, Any]]:
    """Return flagged transactions (anomalies)."""
    # This requires the consumer to store anomalies separately
    # We'll assume `anomalies` list exists in the consumer
    from .kafka_client.consumer import anomalies
    return list(anomalies)[:limit]
