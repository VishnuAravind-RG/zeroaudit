from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any

class Transaction(BaseModel):
    """Raw transaction as it comes from PostgreSQL (after Debezium unwrap)."""
    id: int
    transaction_id: str
    account_id: str
    amount: float
    balance: float
    timestamp: Optional[datetime] = None
    bank_signature: str
    bank_public_key: str
    metadata: Optional[Dict[str, Any]] = None

class Commitment(BaseModel):
    """The commitment that will be published to Kafka."""
    transaction_id: str
    commitment: str
    timestamp: datetime
    verified: bool = True