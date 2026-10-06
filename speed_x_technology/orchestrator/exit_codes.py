"""
speed_x_technology/orchestrator/exit_codes.py

Canonical exit code constants for the facefusion session subprocess.

Both the subprocess (streamer.py) and the orchestrator (app.py) import
these so the meaning of each code is never duplicated or guessed.

  EXIT_OK                 0   Clean shutdown — user requested stop or
                              stream ended naturally.
  EXIT_STARTUP_FAILURE    1   Facefusion could not start: camera not
                              found, model download failed, CUDA OOM,
                              invalid argument, etc.
  EXIT_CONSENT_REJECTION  2   Consent gate rejected the identity:
                              not in 'approved' status, missing from DB,
                              no sample paths, or sample file deleted.
  EXIT_MISSING_IDENTITY   3   speed_x_technology_identity_id was never set in state /
                              env before the stream started.  Distinct
                              from a known rejection so operators can
                              tell "orchestrator bug" from "consent
                              policy violation" in logs.

Negative return codes (e.g. -15 for SIGTERM) indicate the process was
killed by a signal.  The orchestrator maps those to SessionStatus.killed.
"""

EXIT_OK                = 0
EXIT_STARTUP_FAILURE   = 1
EXIT_CONSENT_REJECTION = 2
EXIT_MISSING_IDENTITY  = 3
