from fastapi import FastAPI
import threading
import uvicorn
from prover.consumer import start_consumer

app = FastAPI(title="ZEROAUDIT Prover", description="Zero-Knowledge Proof Generator")

@app.on_event("startup")
async def startup_event():
    """Start the Kafka consumer in a background thread when FastAPI starts."""
    thread = threading.Thread(target=start_consumer, daemon=True)
    thread.start()
    print("✅ Prover consumer started in background.")

@app.get("/")
async def root():
    return {
        "service": "ZEROAUDIT Prover",
        "status": "running",
        "version": "1.0.0"
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)