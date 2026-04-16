"""
GigaAM-бэкенд (Sber). На CPU работает, но медленнее Vosk.
На первом запуске скачает веса (~1 ГБ) в кэш Hugging Face.

Варианты модели:
  v2_ctc   — быстрее, чуть хуже
  v2_rnnt  — медленнее, чуть лучше
"""
import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("voice.server")

GIGAAM_VARIANT = os.environ.get("GIGAAM_VARIANT", "v2_ctc")


class GigaAMBackend:
    def __init__(self):
        import gigaam  # импорт внутри, чтобы vosk-инсталл мог обойтись без torch
        log.info("GigaAMBackend: loading %s (первый запуск качает ~1 ГБ)", GIGAAM_VARIANT)
        self.model = gigaam.load_model(GIGAAM_VARIANT)
        log.info("GigaAMBackend: ready")

    def transcribe(self, wav_bytes: bytes) -> str:
        # GigaAM API хочет путь к файлу (16 kHz mono). Переконвертим через ffmpeg.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            src_path = f.name
        dst_path = src_path + ".16k.wav"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "quiet", "-y", "-i", src_path,
                    "-ar", "16000", "-ac", "1", dst_path,
                ],
                check=True,
            )
            return (self.model.transcribe(dst_path) or "").strip()
        finally:
            for p in (src_path, dst_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
