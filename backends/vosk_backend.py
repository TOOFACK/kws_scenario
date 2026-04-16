"""Vosk-бэкенд: распаковываем WAV через ffmpeg → Kaldi-recognizer."""
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from vosk import KaldiRecognizer, Model

log = logging.getLogger("voice.server")

SAMPLE_RATE = 16000


class VoskBackend:
    def __init__(self):
        model_path = os.environ.get(
            "VOSK_BIG_MODEL",
            str(Path(__file__).resolve().parent.parent / "models" / "vosk-model-ru-0.42"),
        )
        log.info("VoskBackend: loading %s", model_path)
        self.model = Model(model_path)

    def transcribe(self, wav_bytes: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            tmp_path = f.name
        try:
            proc = subprocess.Popen(
                [
                    "ffmpeg", "-loglevel", "quiet", "-i", tmp_path,
                    "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le", "-",
                ],
                stdout=subprocess.PIPE,
            )
            rec = KaldiRecognizer(self.model, SAMPLE_RATE)
            while True:
                chunk = proc.stdout.read(4000)
                if not chunk:
                    break
                rec.AcceptWaveform(chunk)
            return json.loads(rec.FinalResult()).get("text", "").strip()
        finally:
            os.unlink(tmp_path)
