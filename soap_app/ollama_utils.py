"""Makes sure a local Ollama server is reachable before any model calls, starting it if needed."""

import subprocess
import time

import ollama


def ensure_ollama_running(timeout: float = 15.0) -> bool:
    """Returns True once Ollama responds; launches `ollama serve` in the background first if it's down."""
    try:
        ollama.list()  # cheap request that only succeeds if a server is already listening
        return True
    except Exception:
        pass

    try:
        # CREATE_NO_WINDOW keeps this invisible, matching how Ollama already runs as a background service.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except FileNotFoundError:
        return False  # ollama isn't installed / not on PATH

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ollama.list()
            return True
        except Exception:
            time.sleep(0.5)
    return False
