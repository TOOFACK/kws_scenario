"""
ASR-бэкенды сервера. Интерфейс: `transcribe(wav_bytes: bytes) -> str`.

Выбор через env var `ASR_BACKEND=vosk|gigaam` (default: vosk).
"""
import os


def get_backend():
    name = os.environ.get("ASR_BACKEND", "vosk").lower()
    if name == "vosk":
        from .vosk_backend import VoskBackend
        return VoskBackend()
    if name == "gigaam":
        from .gigaam_backend import GigaAMBackend
        return GigaAMBackend()
    raise ValueError(f"Unknown ASR_BACKEND: {name}")
