"""
verifier/__main__.py - entry point for `python -m verifier`

The container runs `python -m verifier.dashboard`, which executes dashboard.py
directly with __name__ == "__main__", so that module starts uvicorn itself and
this file is not involved.

(An earlier revision claimed the opposite - that without this file the
`if __name__ == "__main__"` block in dashboard.py would never fire. That is
backwards: `python -m pkg.module` does set __name__ to "__main__". This file
only runs for `python -m verifier`.)

It exists so both invocations work.
"""

import os
import logging

import uvicorn

from prover.config.settings import settings


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format=settings.LOG_FORMAT,
    )
    uvicorn.run(
        "verifier.dashboard:app",
        host=settings.API_HOST,
        port=int(os.environ.get("VERIFIER_PORT", "8001")),
        reload=False,
    )


if __name__ == "__main__":
    main()
