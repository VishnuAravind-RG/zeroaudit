# ZEROAUDIT – Presentation Script

---

# Slide 1 — Title

**ZEROAUDIT**
Zero-Knowledge Proof Audit System

Team **INFERNO**
MIT Chromepet

### What to Say

Today we’re presenting **ZEROAUDIT**, a privacy-preserving financial audit system.

Instead of sharing sensitive financial data with auditors, we use **cryptography to prove compliance without revealing the data itself**.

Our tagline captures the core idea:

**“Prove compliance. Reveal nothing.”**

---

# Slide 2 — The Problem

### On the Slide

• **60% of enterprise data breaches happen during audit data transfers**
• FinTechs and NBFCs must share **raw financial data** with auditors
• The process itself becomes a **security risk**

### What to Say

Traditional audits require companies to hand over full datasets to third-party auditors.

This includes transaction logs, balances, and customer records.

Ironically, the process meant to ensure compliance becomes a major **attack surface**.

This creates a paradox:

**The cure — auditing — becomes the disease.**

---

# Slide 3 — The Solution

### On the Slide

• Replace data transfer with **mathematical verification**
• Use **Zero-Knowledge Proof primitives**
• Auditor verifies rules **without seeing raw data**

**“Prove compliance. Reveal nothing.”**

### What to Say

ZEROAUDIT eliminates the need to transfer sensitive data.

Instead of sharing the data, we share **cryptographic commitments**.

These commitments allow auditors to verify financial rules while the underlying data stays private.

Think of it like verifying a locked safe contains money **without opening the safe**.

---

# Slide 4 — Architecture

### On the Slide

```
PostgreSQL WAL
      ↓
Debezium CDC
      ↓
Internal Kafka
      ↓
Prover Node
      ↓
Commitments Kafka
      ↓
Auditor Dashboard
```

### What to Say

Here’s the pipeline.

1. Transactions are stored in **PostgreSQL with WAL enabled**.
2. **Debezium** reads database changes directly from the WAL log.
3. Data flows into an **internal Kafka stream**.
4. The **Prover node verifies signatures and generates commitments**.
5. Commitments are published to a **public Kafka topic**.
6. The **auditor dashboard verifies commitments without raw data**.

---

# Slide 5 — Key Innovations

### On the Slide

**Zero-Trust Internal Network**
CDC intercepts data before database administrators can modify it.

**Origin Verification**
Bank ECDSA signatures prevent fake transaction injection.

**Cryptographic Blinding**
Pedersen commitments hide the underlying values.

**Immutable Ordering**
Kafka provides a tamper-evident transaction stream.

### What to Say

The system ensures both **security and privacy**.

First, transactions are cryptographically signed by the bank.

Second, the prover verifies those signatures before processing.

Third, we apply **Pedersen commitments**, which hide the data while preserving mathematical relationships.

Finally, Kafka ensures that the sequence of events is **tamper-evident and immutable**.

---

# Slide 6 — Live Demo

### On the Slide

Components running:

• PostgreSQL database
• Debezium CDC
• Kafka topics
• Prover service (FastAPI)
• Auditor dashboard (Streamlit)

### Demo Flow

1. Simulator generates new transactions
2. Debezium streams them instantly
3. Prover verifies signatures
4. Commitments are created
5. Dashboard shows verified audit proofs

### What to Say

During the demo, our simulator will generate random transactions.

You’ll see the prover verifying the digital signatures and generating commitments in real time.

The dashboard updates automatically.

Notice that **the auditor sees only proofs, never the raw transaction data.**

---

# Slide 7 — Real-World Impact

### On the Slide

Target users:

• FinTech startups
• NBFCs
• SaaS financial platforms

Benefits:

• Eliminates audit-related data leaks
• Reduces compliance overhead
• Improves privacy and trust

### What to Say

This system is designed for modern financial platforms.

Instead of building new audit infrastructure from scratch, companies can deploy ZEROAUDIT as a **containerized microservice pipeline**.

This reduces both **security risk and compliance complexity**.

---

# Slide 8 — Team INFERNO

### On the Slide

**Team INFERNO**

• Your Name — Prover API & Cryptography
• Friend 1 — Auditor Dashboard
• Friend 2 — Data Simulator & Integration

### What to Say

Our team divided the system into three main components:

The **Prover**, the **Auditor Dashboard**, and the **Transaction Simulator**.

Each member focused on a specific subsystem to build the full pipeline.

---

# Slide 9 — Q&A

### On the Slide

**Thank You**

“Prove compliance. Reveal nothing.”

### What to Say

That concludes our presentation.

We’d be happy to answer any questions about the architecture, cryptography, or real-world applications.
