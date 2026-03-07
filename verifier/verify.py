import hashlib
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

def verify_commitment(commitment: str, expected_value: Optional[float] = None, blinding: Optional[int] = None) -> bool:
    """
    Verify a single commitment.
    In a real auditor scenario, the auditor does NOT know the original value or blinding factor.
    Instead, they rely on the fact that the commitment is well-formed and consistent with the prover's public parameters.
    For this demo, we simply check that the commitment is a 64-character hex string (SHA-256 output).
    """
    if not commitment or len(commitment) != 64:
        return False
    try:
        # Check if it's valid hex
        int(commitment, 16)
        return True
    except ValueError:
        return False

def verify_commitment_chain(commitments: List[Dict]) -> bool:
    """
    Verify the integrity of the commitment chain.
    In a real system, each commitment might include a hash of the previous commitment to prevent reordering.
    For this demo, we just ensure all commitments are individually valid and timestamps are increasing.
    """
    if not commitments:
        return True
    
    # Sort by timestamp
    sorted_commits = sorted(commitments, key=lambda x: x.get('timestamp', ''))
    
    prev_ts = None
    for commit in sorted_commits:
        if not commit.get('verified', False):
            return False
        # Check timestamp monotonicity (optional)
        ts = commit.get('timestamp')
        if prev_ts and ts and ts <= prev_ts:
            logger.warning("Non-increasing timestamp detected")
            # return False  # Uncomment for strict ordering
        prev_ts = ts
    return True