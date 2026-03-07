# ZEROAUDIT Architecture

## High-Level Overview

ZEROAUDIT is a privacy-preserving audit system that replaces raw data transfer with **mathematical verification using Zero-Knowledge Proofs**.

```
+-------------+      +-----------+      +--------+      +--------+
| PostgreSQL  | ---> | Debezium  | ---> | Kafka  | ---> | Prover |
| (WAL Level) |      |   (CDC)   |      |Internal|      |FastAPI |
+-------------+      +-----------+      +--------+      +--------+
                                                           |
                                                           |  Pedersen Commitment
                                                           v
+-----------+      +-----------+      +--------+      +-----------+
| Auditor   | <--- | Dashboard | <--- | Kafka  | <--- |Commitment |
| Verifier  |      | Streamlit |      | Public |      |   Topic   |
+-----------+      +-----------+      +--------+      +-----------+
```

---

## Components

### 1. Data Layer (PostgreSQL)

* Stores raw transactions with **bank-issued digital signatures (ECDSA)**
* Write-Ahead Log (**WAL**) enabled for logical replication

---

### 2. Change Data Capture (Debezium)

* Monitors PostgreSQL **WAL in real time**
* Streams every insert/update to an **internal Kafka topic**

---

### 3. Message Broker (Apache Kafka)

Two Kafka streams exist:

**Internal Topic**

```
zeroaudit.public.transactions
```

Contains **raw signed transactions**.

**Public Topic**

```
commitments
```

Contains **only cryptographic commitments**.

---

### 4. Prover Service (FastAPI)

Responsibilities:

* Consumes raw transactions from **internal Kafka**
* Verifies **bank ECDSA signature**
* Generates Pedersen commitment

```
C = xG + rH
```

Where:

* `x` = balance value
* `r` = random blinding factor

After commitment creation:

* raw transaction data is **discarded**
* commitment is **published to public Kafka**

---

### 5. Auditor Dashboard (Streamlit)

* Consumes commitments from **public Kafka**
* Verifies **mathematical consistency of the commitment chain**
* Displays **real-time audit trail**

Auditors see **proofs**, not financial data.

---

### 6. Simulator (Mock Data Generator)

Used for testing the pipeline.

Functions:

* Generates realistic bank transactions
* Signs transactions with **mock bank keys**
* Inserts them into PostgreSQL
* Triggers the full CDC pipeline
