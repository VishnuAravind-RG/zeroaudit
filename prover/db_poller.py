import time
import psycopg2
import json
import logging
import os
from prover.crypto.signature import verify_bank_signature
from prover.crypto.commitment import create_pedersen_commitment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PARAMS = {
    'dbname': 'zeroaudit',
    'user': 'audit_user',
    'password': 'StrongPass123!',
    'host': 'localhost',
    'port': '5432'
}

COMMITMENTS_FILE = os.path.abspath("commitments.jsonl")

def poll_transactions():
    last_id = 0
    conn = psycopg2.connect(**DB_PARAMS)
    logger.info(f"Polling PostgreSQL for new transactions... writing to {COMMITMENTS_FILE}")
    
    while True:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, transaction_id, account_id, amount, balance,
                           bank_signature, bank_public_key, timestamp, metadata
                    FROM transactions
                    WHERE id > %s
                    ORDER BY id
                """, (last_id,))
                rows = cur.fetchall()
                
                for row in rows:
                    id, tx_id, account_id, amount, balance, sig, pub_key, ts, metadata = row
                    signed_data = f"{tx_id}{amount}{balance}"
                    
                    if verify_bank_signature(signed_data, sig, pub_key):
                        commitment, blinding = create_pedersen_commitment(balance)
                        
                        # Handle metadata properly - it might be a dict or a string
                        tx_type = None
                        if metadata:
                            if isinstance(metadata, dict):
                                tx_type = metadata.get('type')
                            elif isinstance(metadata, str):
                                try:
                                    meta_dict = json.loads(metadata)
                                    tx_type = meta_dict.get('type')
                                except:
                                    pass
                        
                        with open(COMMITMENTS_FILE, "a") as f:
                            record = {
                                "transaction_id": tx_id,
                                "account_id": account_id,
                                "transaction_type": tx_type,
                                "commitment": commitment,
                                "timestamp": str(ts),
                                "verified": True
                            }
                            f.write(json.dumps(record) + "\n")
                        
                        last_id = id
                        logger.info(f"✅ Processed {tx_id}")
                    else:
                        logger.error(f"❌ Invalid signature for {tx_id}")
                        
        except Exception as e:
            logger.error(f"Polling error: {e}")
        
        time.sleep(2)

if __name__ == "__main__":
    poll_transactions()