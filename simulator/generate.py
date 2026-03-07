import psycopg2
import random
import time
import logging
import json
from datetime import datetime
from bank_sim import BankSimulator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PARAMS = {
    'dbname': 'zeroaudit',
    'user': 'audit_user',
    'password': 'StrongPass123!',
    'host': 'localhost',
    'port': 5432  # Ensure port is an integer, not a string
}

ACCOUNTS = ['acc_12345', 'acc_67890', 'acc_11111', 'acc_22222', 'acc_33333', 'acc_44444']
TYPES = ['deposit', 'withdrawal', 'transfer', 'payment', 'refund']

def generate_transaction(bank):
    """Generate a random transaction."""
    tx_id = f"txn_{int(time.time())}_{random.randint(1000, 9999)}"
    account = random.choice(ACCOUNTS)
    amount = round(random.uniform(-10000, 20000), 2)
    balance = round(random.uniform(1000, 100000), 2)

    tx_data = {
        'transaction_id': tx_id,
        'account_id': account,
        'amount': amount,
        'balance': balance
    }

    sig = bank.sign_transaction(tx_data)
    pub = bank.get_public_key_hex()
    meta = json.dumps({'type': random.choice(TYPES)})

    return tx_id, account, amount, balance, sig, pub, meta

def main():
    logger.info("=" * 50)
    logger.info("Starting ZEROAUDIT Simulator")
    logger.info("=" * 50)

    # Test connection
    try:
        with psycopg2.connect(
            dbname=DB_PARAMS['dbname'],
            user=DB_PARAMS['user'],
            password=DB_PARAMS['password'],
            host=DB_PARAMS['host'],
            port=DB_PARAMS['port']
        ) as conn:
            logger.info("✅ Connected to PostgreSQL")

            bank = BankSimulator()
            logger.info("🏦 Bank ready")

            count = 0
            try:
                while True:
                    tx_id, account, amount, balance, sig, pub, meta = generate_transaction(bank)

                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO transactions 
                            (transaction_id, account_id, amount, balance, bank_signature, bank_public_key, metadata)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (tx_id, account, amount, balance, sig, pub, meta)
                        )
                        conn.commit()

                    count += 1
                    logger.info(f"✅ Inserted {tx_id} | Amount: {amount} | Balance: {balance}")
                    time.sleep(random.uniform(3, 8))
            except KeyboardInterrupt:
                logger.info(f"\nStopped. Total transactions: {count}")
            except Exception as e:
                logger.error(f"Error: {e}")
    except psycopg2.Error as e:
        logger.error(f"❌ Database connection failed: {e}")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main()