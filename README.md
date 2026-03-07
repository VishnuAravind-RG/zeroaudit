# 🔐 ZEROAUDIT – Zero-Knowledge Proof Audit System

> **Prove compliance. Reveal nothing.**

[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://choosealicense.com/licenses/mit/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-required-blue)](https://www.docker.com/)

---

# 🚀 The Problem

Financial audits today require companies to hand over **raw financial data** to external auditors in order to prove compliance.

This creates a dangerous paradox:
**the very process meant to verify security becomes a major attack surface.**

Industry studies estimate that **a large fraction of financial data breaches occur during audit data transfers.**

The system assumes trust where trust is weakest.

---

# 💡 The Solution — ZEROAUDIT

**ZEROAUDIT replaces data transfer with mathematical verification.**

Using **Zero-Knowledge Proof primitives** (Pedersen commitments on elliptic curves), auditors can verify compliance rules **without seeing the underlying financial records**.

Key principles:

• **Zero data exposure** – raw data never leaves the bank
• **Cryptographic integrity** – transactions are signed at origin
• **Streaming architecture** – real-time verification using CDC
• **Immutable ordering** – Kafka ensures tamper-evident event logs

Instead of trusting data visibility, auditors trust **cryptographic proofs**.

---

# 🏗 Architecture

```
        +------------------+
        |      Bank        |
        | Signed Txn Data  |
        +---------+--------+
                  |
                  v
        +------------------+
        |   PostgreSQL DB  |
        | (Logical Replication)
        +---------+--------+
                  |
                  | WAL (Write Ahead Log)
                  v
        +------------------+
        |   Debezium CDC   |
        +---------+--------+
                  |
                  v
        +------------------+
        |  Internal Kafka  |
        |  (Raw Stream)    |
        +---------+--------+
                  |
                  v
        +------------------+
        |    Prover Node   |
        |  Pedersen Commit |
        +---------+--------+
                  |
                  v
        +------------------+
        | Public Kafka     |
        | Commitments Only |
        +---------+--------+
                  |
                  v
        +------------------+
        | Auditor Dashboard|
        |  Verification UI |
        +------------------+
```

### System Flow

1. **Ingestion**
   Bank-signed transaction lands in PostgreSQL.

2. **Capture**
   Debezium reads the Write-Ahead Log (WAL) in real time.

3. **Verification**
   The Prover verifies the bank's **ECDSA signature**.

4. **Blinding**
   A **Pedersen commitment** is generated:

   `C = xG + rH`

   where
   `x = balance`
   `r = random blinding factor`

5. **Publishing**
   The commitment is pushed to a **public Kafka topic**.

6. **Auditing**
   Auditors verify compliance rules using **commitments only**.

No financial data is revealed.

---

# 🛠 Tech Stack

| Component         | Technology                              |
| ----------------- | --------------------------------------- |
| Database          | PostgreSQL (Logical Replication)        |
| CDC Pipeline      | Debezium                                |
| Stream Processing | Apache Kafka                            |
| Prover API        | FastAPI                                 |
| Cryptography      | ECDSA (SECP256k1), Pedersen Commitments |
| Auditor Dashboard | Streamlit                               |
| Containerization  | Docker / Docker Compose                 |

---

# 📦 Prerequisites

Before running the system ensure you have:

• **Docker & Docker Compose**
• **Python 3.10+**
• **Git**

---

# 🚦 Quick Start (Windows PowerShell)

### 1. Clone Repository

```powershell
git clone https://github.com/VishnuAravind-RG/zeroaudit.git
cd zeroaudit
```

### 2. Create Environment File

```powershell
copy .env.example .env
```

### 3. Start Infrastructure

```powershell
docker-compose up -d
```

Wait ~30 seconds for services to initialize.

---

### 4. Create Python Virtual Environment

```powershell
python -m venv venv
.\venv\Scripts\activate
```

---

### 5. Install Dependencies

```powershell
pip install -r requirements.txt
```

---

### 6. Configure Debezium Connector

```powershell
curl -X POST `
-H "Content-Type: application/json" `
--data @services/debezium/connector-config.json `
http://localhost:8083/connectors
```

---

### 7. Start the Prover API

```powershell
cd prover
uvicorn main:app --reload --port 8000
```

---

### 8. Start Auditor Dashboard

```powershell
cd verifier
streamlit run dashboard.py
```

---

### 9. Generate Sample Transactions (Optional)

```powershell
cd simulator
python generate.py
```

---

# 🔎 Core Cryptographic Primitive

ZEROAUDIT uses **Pedersen Commitments**, a cryptographic scheme that provides:

• **Binding** – data cannot be changed once committed
• **Hiding** – the committed value remains secret

Commitment formula:

```
C = xG + rH
```

Where:

• `x` = secret value (transaction or balance)
• `r` = random blinding factor
• `G,H` = elliptic curve generator points

This allows auditors to verify relationships like:

```
sum(inputs) = sum(outputs)
```

without revealing the underlying numbers.

---

# 📊 Future Enhancements

• Full **Zero-Knowledge Proof circuits (zk-SNARK / zk-STARK)**
• **Smart-contract audit proofs** on blockchain
• **Multi-bank federated audit network**
• **Real-time anomaly detection** on commitments
• **Automated compliance reporting**

---

# 👨‍💻 Author

**Vishnu Aravind R G**
PSG College of Technology

GitHub:
https://github.com/VishnuAravind-RG

---

# 📄 License

This project is licensed under the **MIT License**.

---

⭐ If this project interests you, consider starring the repository.
