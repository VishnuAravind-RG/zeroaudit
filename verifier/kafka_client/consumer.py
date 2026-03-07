import json
import logging
from typing import List, Dict
from kafka import KafkaConsumer
from datetime import datetime, timedelta
import threading
import time

logger = logging.getLogger(__name__)

# Global cache of commitments (for simplicity, we store them in memory)
_commitments_cache = []
_cache_lock = threading.Lock()
_last_fetch = None

KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
COMMITMENT_TOPIC = "commitments"

def _consumer_loop():
    """Background thread that continuously consumes commitments and updates cache."""
    global _commitments_cache
    consumer = KafkaConsumer(
        COMMITMENT_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest',
        group_id='verifier-group',
        enable_auto_commit=True
    )
    logger.info("Verifier Kafka consumer started")
    for msg in consumer:
        try:
            commitment_data = msg.value
            # Add to cache with a limit (keep last 1000)
            with _cache_lock:
                _commitments_cache.append(commitment_data)
                if len(_commitments_cache) > 1000:
                    _commitments_cache = _commitments_cache[-1000:]
        except Exception as e:
            logger.error(f"Error processing message: {e}")

# Start background consumer on import
thread = threading.Thread(target=_consumer_loop, daemon=True)
thread.start()

def get_commitments(limit: int = 100, time_filter: str = "all") -> List[Dict]:
    """Return cached commitments, optionally filtered by time."""
    with _cache_lock:
        commits = list(_commitments_cache)
    
    # Apply time filter
    if time_filter != "all":
        now = datetime.now()
        if time_filter == "Last 5 minutes":
            cutoff = now - timedelta(minutes=5)
        elif time_filter == "Last hour":
            cutoff = now - timedelta(hours=1)
        elif time_filter == "Last 24 hours":
            cutoff = now - timedelta(days=1)
        else:
            cutoff = datetime.min
        
        # Parse timestamp strings (ISO format)
        filtered = []
        for c in commits:
            ts_str = c.get('timestamp')
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    if ts >= cutoff:
                        filtered.append(c)
                except:
                    filtered.append(c)  # keep if can't parse
        commits = filtered
    
    # Sort by timestamp descending (newest first)
    commits.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return commits[:limit]