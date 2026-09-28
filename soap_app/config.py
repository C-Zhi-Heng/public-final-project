"""Central constants for the SOAP note assistant, so model names/paths are changed in one place."""

from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

# Ollama model tags, must match `ollama list` exactly (pull them with `ollama pull <tag>` first)
WHISPER_MODEL_SIZE = "base"          # faster-whisper model size: base/small/medium/large-v3
MEDGEMMA_MODEL = "medgemma:latest"   # X-ray analysis (vision)
SOAP_MODEL = "phi3:mini"             # SOAP note generation (text-only)

# Audio recording settings
SAMPLE_RATE = 44100                  # standard mic rate; faster-whisper resamples to 16kHz internally on transcribe
CHANNELS = 1

# Local storage paths
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "encounters.db"
KEY_PATH = DATA_DIR / "secret.key"  # SQLCipher raw-hex encryption key (32 bytes as hex string)
RECORDING_PATH = DATA_DIR / "last_recording.wav"

# Auth (app_multipatient.py only): master account seeded on first run, dummy password is an MVP-level
# limitation (see report) — change MASTER_DUMMY_PASSWORD here if needed, there is no in-app way to do so.
MASTER_USERNAME = "master"
MASTER_DUMMY_PASSWORD = "ChangeMe123!"
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 5

# Idle sessions log out automatically after this many minutes, via a ticking st.fragment (see app_multipatient.py).
SESSION_TIMEOUT_MINUTES = 30
AUDIT_LOG_PAGE_SIZE = 200

