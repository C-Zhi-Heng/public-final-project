"""Audio capture (mic) and speech-to-text transcription using faster-whisper."""

import queue
import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from soap_app.config import CHANNELS, SAMPLE_RATE, WHISPER_MODEL_SIZE

# Loaded once per process; faster-whisper keeps this in memory for reuse across calls.
_model = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        # CPU/int8 keeps this runnable on machines without a CUDA GPU (swap to "cuda"/"float16" if available).
        _model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


class Recorder:
    """Records microphone audio to a WAV file between calls to start() and stop()."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status):
        self._queue.put(indata.copy())

    def start(self):
        self._queue = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", callback=self._callback
        )
        self._stream.start()

    def stop(self, output_path: str) -> str:
        """Stops recording and writes the captured audio to output_path as a 16-bit PCM WAV file."""
        self._stream.stop()
        self._stream.close()

        frames = []
        while not self._queue.empty():
            frames.append(self._queue.get())
        audio = np.concatenate(frames, axis=0) if frames else np.zeros((0, CHANNELS), dtype="int16")

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes/sample
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())

        return output_path


def transcribe_audio(audio_path: str) -> str:
    """Transcribes an audio file (mic recording or uploaded file, any format ffmpeg supports) to plain text."""
    model = _get_model()
    segments, _info = model.transcribe(audio_path, beam_size=5)
    return " ".join(segment.text.strip() for segment in segments)
