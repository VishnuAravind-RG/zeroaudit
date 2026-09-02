"""
load_sanctions_graph.py - Load the real OFAC SDN list into Neo4j and seed a
small demo relationship graph.

    python -m scripts.load_sanctions_graph

What this loads
----------------
1. SanctionedEntity nodes - one per row of services/neo4j/sdn_snapshot.csv,
   the actual U.S. Treasury OFAC list (see scripts/fetch_sanctions_list.py
   for provenance). ~19,300 real designations: real names, real program
   tags (RUSSIA-EO14024, SDGT, IRAN-EO13902, ...), real entity types.

2. A small set of deterministic demo Account nodes, wired to real
   SanctionedEntity nodes at controlled hop distances via intermediate
   Proxy nodes:

       ACC-SANC-OFAC-01..04   1 hop  (direct counterparty)
       ACC-SANC-RBI-01..04    2 hops (one shell in between)
       ACC-SANC-FATF-01..04   3 hops (two shells in between)

   This half is necessarily synthetic - no public dataset of real
   interbank transaction relationships exists, for the same reason no
   public dataset of real bank transactions exists (it's private
   financial data). The synthetic part is exactly which demo accounts are
   near which sanctioned entity; the sanctioned entities themselves, and
   the graph traversal that measures distance to them, are both real.

Query mechanism
----------------
verifier/sanctions_graph.py answers "how many hops from this account to a
sanctioned entity" with a real Cypher shortest-path query against this
graph - not a hash-prefix lookup table. That's the thing this script and
sanctions_graph.py together replace.
"""

import os
import csv
import hashlib
import logging
import random

logger = logging.getLogger("zeroaudit.sanctions.loader")

try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None

SNAPSHOT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services", "neo4j", "sdn_snapshot.csv",
)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "zeroaudit-demo")

# Deterministic demo account IDs. simulator/bank_sim.py references these
# same literal strings so both sides agree on which accounts are
# "sanctions-adjacent" without any hash-prefix guessing.
DEMO_TIERS = {
    "OFAC": {"count": 4, "hops": 1, "prefix": "ACC-SANC-OFAC"},
    "RBI": {"count": 4, "hops": 2, "prefix": "ACC-SANC-RBI"},
    "FATF": {"count": 4, "hops": 3, "prefix": "ACC-SANC-FATF"},
}


def account_hash(account_id: str) -> str:
    return hashlib.sha3_256(account_id.encode()).hexdigest()


def demo_account_ids() -> list:
    ids = []
    for tier in DEMO_TIERS.values():
        for i in range(1, tier["count"] + 1):
            ids.append("%s-%02d" % (tier["prefix"], i))
    return ids


def read_sdn_rows(path: str = SNAPSHOT_PATH) -> list:
    """Parse the real OFAC CSV. No header row; -0- means 'not populated'."""
    rows = []
    with open(path, encoding="latin-1", newline="") as fh:
        for rec in csv.reader(fh):
            if len(rec) < 4:
                continue
            ent_num, name, sdn_type, program = (f.strip() for f in rec[:4])
            if not name:
                continue
            rows.append({
                "ent_num": ent_num,
                "name": name,
                "sdn_type": None if sdn_type == "-0-" else sdn_type,
                "program": None if program == "-0-" else program,
            })
    return rows


def apply_constraints(session):
    session.run("CREATE CONSTRAINT sanctioned_entity_id IF NOT EXISTS "
                "FOR (s:SanctionedEntity) REQUIRE s.ent_num IS UNIQUE")
    session.run("CREATE CONSTRAINT account_hash_unique IF NOT EXISTS "
                "FOR (a:Account) REQUIRE a.hash IS UNIQUE")
    session.run("CREATE CONSTRAINT proxy_id_unique IF NOT EXISTS "
                "FOR (p:Proxy) REQUIRE p.id IS UNIQUE")


def load_entities(session, rows: list, batch_size: int = 2000) -> int:
    query = """
    UNWIND $batch AS row
    MERGE (s:SanctionedEntity {ent_num: row.ent_num})
    SET s.name = row.name, s.sdn_type = row.sdn_type, s.program = row.program
    """
    for i in range(0, len(rows), batch_size):
        session.run(query, batch=rows[i:i + batch_size])
    return len(rows)


def seed_demo_accounts(session, entity_ent_nums: list, seed: int = 1337) -> list:
    """Wire deterministic demo accounts to real entities at fixed hop counts."""
    rng = random.Random(seed)          # deterministic across reruns
    chosen_entities = rng.sample(entity_ent_nums, min(12, len(entity_ent_nums)))
    seeded = []
    idx = 0

    for tier_name, tier in DEMO_TIERS.items():
        for i in range(1, tier["count"] + 1):
            account_id = "%s-%02d" % (tier["prefix"], i)
            a_hash = account_hash(account_id)
            ent_num = chosen_entities[idx % len(chosen_entities)]
            idx += 1

            session.run("MERGE (a:Account {hash: $hash}) SET a.demo_id = $account_id",
                       hash=a_hash, account_id=account_id)

            hops = tier["hops"]
            if hops == 1:
                session.run("""
                    MATCH (a:Account {hash: $hash}), (s:SanctionedEntity {ent_num: $ent_num})
                    MERGE (a)-[:TRANSACTED_WITH]->(s)
                """, hash=a_hash, ent_num=ent_num)
            else:
                proxy_ids = ["proxy-%s-%d" % (tier_name, n) for n in range(hops - 1)]
                session.run("""
                    MATCH (a:Account {hash: $hash})
                    MERGE (p0:Proxy {id: $first_proxy})
                    MERGE (a)-[:TRANSACTED_WITH]->(p0)
                """, hash=a_hash, first_proxy=proxy_ids[0])
                for j in range(len(proxy_ids) - 1):
                    session.run("""
                        MATCH (p1:Proxy {id: $p1}) MERGE (p2:Proxy {id: $p2})
                        MERGE (p1)-[:TRANSACTED_WITH]->(p2)
                    """, p1=proxy_ids[j], p2=proxy_ids[j + 1])
                session.run("""
                    MATCH (p:Proxy {id: $last_proxy}), (s:SanctionedEntity {ent_num: $ent_num})
                    MERGE (p)-[:TRANSACTED_WITH]->(s)
                """, last_proxy=proxy_ids[-1], ent_num=ent_num)

            seeded.append((account_id, hops, ent_num))
    return seeded


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if GraphDatabase is None:
        logger.error("neo4j driver not installed - pip install -r requirements.txt")
        raise SystemExit(1)
    if not os.path.exists(SNAPSHOT_PATH):
        logger.error("no SDN snapshot at %s - run scripts/fetch_sanctions_list.py first",
                     SNAPSHOT_PATH)
        raise SystemExit(1)

    logger.info("reading real OFAC SDN snapshot from %s", SNAPSHOT_PATH)
    rows = read_sdn_rows()
    logger.info("parsed %d real sanctioned-entity records", len(rows))

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            logger.info("applying constraints")
            apply_constraints(session)

            logger.info("loading %d real SanctionedEntity nodes", len(rows))
            load_entities(session, rows)

            logger.info("seeding demo account graph (%d accounts across 3 tiers)",
                        sum(t["count"] for t in DEMO_TIERS.values()))
            seeded = seed_demo_accounts(session, [r["ent_num"] for r in rows])
            for account_id, hops, ent_num in seeded:
                logger.info("  %-20s -> %d hop(s) -> entity #%s", account_id, hops, ent_num)

            count = session.run("MATCH (s:SanctionedEntity) RETURN count(s) AS c").single()["c"]
            logger.info("verified: %d SanctionedEntity nodes now in the graph", count)
    finally:
        driver.close()

    logger.info("done")


if __name__ == "__main__":
    main()
