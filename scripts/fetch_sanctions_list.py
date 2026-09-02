"""
fetch_sanctions_list.py - Pull the current OFAC Specially Designated
Nationals (SDN) list from the U.S. Treasury and refresh the bundled snapshot.

    python -m scripts.fetch_sanctions_list

Source
------
OFAC's Sanctions List Service (the endpoint that superseded the old
treasury.gov static download paths): every file is served from

    https://sanctionslistservice.ofac.treas.gov/api/download/{filename}

which 302-redirects to a short-lived signed S3 URL. No API key or
authentication is required - this is a public compliance dataset the
Treasury Department publishes precisely so it can be integrated into
screening systems like this one.

The snapshot bundled in this repo (services/neo4j/sdn_snapshot.csv) is a
point-in-time copy - OFAC adds and removes designations continuously, so a
real deployment would run this on a schedule (daily is typical for
compliance tooling) rather than once at build time.

CSV layout (no header row, 12 columns):
    ent_num, SDN_Name, SDN_Type, Program, Title, Call_Sign, Vess_type,
    Tonnage, GRT, Vess_flag, Vess_owner, Remarks
Unpopulated fields are literally the string "-0-", which is OFAC's own
placeholder, not a parsing artifact.
"""

import os
import sys
import logging
import urllib.request

logger = logging.getLogger("zeroaudit.sanctions")

SOURCE_URL = "https://sanctionslistservice.ofac.treas.gov/api/download/SDN.CSV"
DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services", "neo4j", "sdn_snapshot.csv",
)


def fetch(out_path: str = DEFAULT_OUT, timeout: float = 30.0) -> int:
    """Download the current SDN list. Returns the row count fetched."""
    logger.info("fetching current OFAC SDN list from %s", SOURCE_URL)
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "zeroaudit-compliance-sync/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError("unexpected status %s fetching SDN list" % resp.status)
        data = resp.read()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(data)

    row_count = data.count(b"\n")
    logger.info("wrote %s (%d rows, %.1f KB)", out_path, row_count, len(data) / 1024)
    return row_count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        fetch()
    except Exception as exc:
        logger.error("refresh failed (%s: %s) - keeping the existing bundled snapshot",
                     type(exc).__name__, exc)
        sys.exit(1)
