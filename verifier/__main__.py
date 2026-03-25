"""
verifier/__main__.py

Entry point for: python -m verifier.dashboard
Without this file, python -m verifier.dashboard runs dashboard.py but
__name__ == 'verifier.dashboard' (not '__main__'), so the
`if __name__ == '__main__': uvicorn.run(...)` block at the bottom of
dashboard.py is never executed and uvicorn never starts.
"""
import uvicorn
from prover.config.settings import settings

uvicorn.run(
    "verifier.dashboard:app",
    host=getattr(settings, "API_HOST", "0.0.0.0"),
    port=8001,
    reload=False,
    log_level=getattr(settings, "LOG_LEVEL", "info").lower(),
)
