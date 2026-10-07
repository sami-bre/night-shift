"""Configuration: paths and env knobs for The Night Shift."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(os.environ.get("NIGHTSHIFT_RUNS_DIR", PROJECT_ROOT / "runs"))
WEB_DIR = PROJECT_ROOT / "web"

KUBE_CONTEXT = os.environ.get("NIGHTSHIFT_KUBE_CONTEXT", "k3d-night-shift")
CITY_NAMESPACE = os.environ.get("NIGHTSHIFT_NAMESPACE", "city")
INCIDENT_APP = os.environ.get("NIGHTSHIFT_INCIDENT_APP", "payments-api")

OPENROUTER_MODEL = os.environ.get("NIGHTSHIFT_MODEL", "google/gemini-3-flash-preview")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

MAX_STEPS = int(os.environ.get("NIGHTSHIFT_MAX_STEPS", "10"))

SERVER_PORT = int(os.environ.get("NIGHTSHIFT_PORT", "8808"))

# live-mode policy: live is the DEFAULT for visitors; mock is the fallback
# when limits hit. LIVE_ENABLED=false forces mock for everyone.
LIVE_ENABLED = os.environ.get("NIGHTSHIFT_LIVE_ENABLED", "true").lower() != "false"
DAILY_SPEND_CAP_USD = float(os.environ.get("NIGHTSHIFT_DAILY_SPEND_CAP_USD", "2.00"))

# rate limiting / caps
LIVE_DAILY_CAP = int(os.environ.get("NIGHTSHIFT_LIVE_DAILY_CAP", "20"))
LIVE_TOKEN = os.environ.get("NIGHTSHIFT_LIVE_TOKEN", "")  # optional: force-mock switch
IP_COOLDOWN_S = int(os.environ.get("NIGHTSHIFT_IP_COOLDOWN_S", "600"))

PRICES_PER_TOKEN = {"in": 0.50 / 1e6, "out": 3.00 / 1e6}  # google/gemini-3-flash-preview
