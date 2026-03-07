import json
import os
from typing import List, Dict
from datetime import datetime, timedelta

# Absolute path to commitments.jsonl in project root
COMMITMENTS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "commitments.jsonl"))
print(f"DEBUG: Consumer using file: {COMMITMENTS_FILE}")

def get_commitments(limit: int = 100, time_filter: str = "all") -> List[Dict]:
    """Read commitments from the JSON lines file."""
    print(f"DEBUG: get_commitments called, limit={limit}, filter={time_filter}")
    if not os.path.exists(COMMITMENTS_FILE):
        print("DEBUG: File not found")
        return []
    
    commitments = []
    with open(COMMITMENTS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                commitments.append(json.loads(line))
            except Exception as e:
                print(f"DEBUG: Failed to parse line: {line[:50]}... error: {e}")
    
    print(f"DEBUG: Loaded {len(commitments)} valid commitments")
    
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
        
        filtered = []
        for c in commitments:
            ts_str = c.get('timestamp')
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    if ts >= cutoff:
                        filtered.append(c)
                except:
                    # If timestamp parsing fails, keep the record (assume it's valid)
                    filtered.append(c)
        commitments = filtered
        print(f"DEBUG: After filter, {len(commitments)} commitments remain")
    
    # Sort newest first
    commitments.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return commitments[:limit]