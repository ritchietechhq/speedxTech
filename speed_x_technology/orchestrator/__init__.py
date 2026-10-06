"""
speed_x_technology.orchestrator

Session orchestrator package.

The FastAPI application lives in speed_x_technology.orchestrator.app.
Start it with:
    uvicorn speed_x_technology.orchestrator.app:app --host 0.0.0.0 --port 8080
"""
from speed_x_technology.orchestrator.exit_codes import (
    EXIT_CONSENT_REJECTION,
    EXIT_MISSING_IDENTITY,
    EXIT_OK,
    EXIT_STARTUP_FAILURE,
)

__all__ = [
    "EXIT_OK",
    "EXIT_STARTUP_FAILURE",
    "EXIT_CONSENT_REJECTION",
    "EXIT_MISSING_IDENTITY",
]
