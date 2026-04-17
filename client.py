"""
RPi-клиент первого этапа интеграции.

Логика:
  1. Захват микрофона (native rate), ресемпл до 16kHz mono.
  2. Маленький Vosk непрерывно ищет ключевую фразу.
  3. При обнаружении wake-word захватывает следующую фразу.
  4. Останавливает захват по длинной тишине или таймауту 3 секунды.
  5. Упаковывает post-wake PCM в WAV и отправляет в локальный агент.
  6. Возвращается в IDLE и продолжает слушать дальше.

Все события пишутся в kws_scenario/logs/client.log (ротируемый, 5MB × 5).
Зависимости: vosk, sounddevice, numpy, scipy
"""
import json
import os
import queue
import time
import wave
from io import BytesIO
from math import gcd
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
from scipy.signal import resample_poly
from vosk import KaldiRecognizer, Model

from kws_scenario._logging import setup_logger

HERE = Path(__file__).parent
SMALL_MODEL_PATH = os.environ.get(
    "VOSK_SMALL_MODEL",
    str(HERE / "models" / "vosk-model-small-ru-0.22"),
)
CLIENT_MODEL_PATH = os.environ.get("VOSK_CLIENT_MODEL", SMALL_MODEL_PATH)
INPUT_DEVICE = os.environ.get("INPUT_DEVICE")
DEBUG_PARTIALS = os.environ.get("DEBUG_PARTIALS", "1") == "1"

WAKE_WORD = os.environ.get("WAKE_WORD", "джарвис")
WAKE_COOLDOWN_MS = int(os.environ.get("WAKE_COOLDOWN_MS", "1500"))
VOICE_COMMAND_URL = os.environ.get("VOICE_COMMAND_URL", "").strip()
POST_WAKE_SILENCE_SECONDS = float(os.environ.get("POST_WAKE_SILENCE_SECONDS", "1.5"))
POST_WAKE_MAX_SECONDS = float(os.environ.get("POST_WAKE_MAX_SECONDS", "3.0"))
POST_WAKE_RMS_THRESHOLD = float(os.environ.get("POST_WAKE_RMS_THRESHOLD", "0.02"))
VOICE_COMMAND_TIMEOUT_SECONDS = float(os.environ.get("VOICE_COMMAND_TIMEOUT_SECONDS", "10.0"))

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

PARTIAL_PRINT_EVERY_MS = 400

log = setup_logger("voice.client", "client.log")


def calc_rms(chunk: bytes) -> float:
    samples = np.frombuffer(chunk, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    normalized = samples.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(normalized * normalized)))


def pcm_to_wav_bytes(pcm_data: bytes) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_data)
    return buf.getvalue()


def capture_followup_command(audio_q: "queue.Queue[bytes]") -> bytes:
    deadline = time.time() + POST_WAKE_MAX_SECONDS
    speech_chunks: list[bytes] = []
    pending_silence: list[bytes] = []
    speech_started = False
    silence_started_at = None
    stopped_by_silence = False

    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break

        try:
            chunk = audio_q.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue

        rms = calc_rms(chunk)
        if rms >= POST_WAKE_RMS_THRESHOLD:
            speech_started = True
            silence_started_at = None
            if pending_silence:
                speech_chunks.extend(pending_silence)
                pending_silence.clear()
            speech_chunks.append(chunk)
            continue

        if not speech_started:
            continue

        pending_silence.append(chunk)
        if silence_started_at is None:
            silence_started_at = time.time()
            continue

        if time.time() - silence_started_at >= POST_WAKE_SILENCE_SECONDS:
            stopped_by_silence = True
            break

    if speech_chunks and pending_silence and not stopped_by_silence:
        speech_chunks.extend(pending_silence)

    return b"".join(speech_chunks)


def send_command_audio(audio_pcm: bytes) -> None:
    if not audio_pcm:
        log.info("[COMMAND] empty audio after wake-word, skipping upload")
        return
    if not VOICE_COMMAND_URL:
        log.warning("[COMMAND] VOICE_COMMAND_URL is not configured, dropping %d bytes", len(audio_pcm))
        return

    audio_wav = pcm_to_wav_bytes(audio_pcm)
    duration_sec = len(audio_pcm) / (SAMPLE_RATE * 2)
    try:
        response = requests.post(
            VOICE_COMMAND_URL,
            data=audio_wav,
            headers={
                "Content-Type": "audio/wav",
                "X-Wakeword-Source": "kws_scenario",
            },
            timeout=VOICE_COMMAND_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        log.info(
            "[COMMAND] uploaded post-wake audio: pcm=%dB wav=%dB duration=%.2fs status=%d",
            len(audio_pcm),
            len(audio_wav),
            duration_sec,
            response.status_code,
        )
    except requests.RequestException as exc:
        log.exception("[COMMAND] failed to upload post-wake audio: %s", exc)


def main():
    log.info("loading client Vosk from %s", CLIENT_MODEL_PATH)
    model = Model(CLIENT_MODEL_PATH)
    # Без grammar — ищем wake-слово в обычном transcript'е.
    # Grammar биасит recognizer в сторону wake-слова и даёт фолсы на шуме.
    wake_rec = KaldiRecognizer(model, SAMPLE_RATE)

    device = INPUT_DEVICE
    if device is not None and device.isdigit():
        device = int(device)

    dev_info = sd.query_devices(device, "input")
    native_rate = int(dev_info["default_samplerate"])
    need_resample = native_rate != SAMPLE_RATE
    if need_resample:
        g = gcd(native_rate, SAMPLE_RATE)
        up = SAMPLE_RATE // g
        down = native_rate // g
        native_block = native_rate * FRAME_MS // 1000
        log.info("resampling %dHz -> %dHz (up=%d, down=%d)", native_rate, SAMPLE_RATE, up, down)
    else:
        up = down = 1
        native_block = FRAME_SAMPLES

    audio_q: "queue.Queue[bytes]" = queue.Queue()

    def audio_cb(indata, frames, time_info, status):
        if status:
            log.warning("audio status: %s", status)
        if need_resample:
            arr = np.frombuffer(bytes(indata), dtype=np.int16)
            arr = resample_poly(arr.astype(np.float32), up, down)
            arr = np.clip(arr, -32768, 32767).astype(np.int16)
            audio_q.put(arr.tobytes())
        else:
            audio_q.put(bytes(indata))

    stream = sd.RawInputStream(
        samplerate=native_rate,
        blocksize=native_block,
        dtype="int16",
        channels=1,
        callback=audio_cb,
        device=device,
    )

    last_partial = ""
    last_print_t = 0.0
    wake_cooldown_until = 0.0

    log.info("wake word: %s (open recognizer, no grammar)", WAKE_WORD)
    log.info("input device: %s (%s)", device if device is not None else "default", dev_info.get("name"))
    log.info("ready. Say '%s'.", WAKE_WORD)

    with stream:
        while True:
            chunk = audio_q.get()
            now = time.time()
            if now < wake_cooldown_until:
                continue

            finalized = wake_rec.AcceptWaveform(chunk)
            text = (
                json.loads(wake_rec.Result()).get("text", "")
                if finalized
                else json.loads(wake_rec.PartialResult()).get("partial", "")
            ).strip()

            if WAKE_WORD in text:
                log.info("[WAKE] trigger phrase detected (%s) on %r", "final" if finalized else "partial", text)
                wake_rec.Reset()
                last_partial = ""
                log.info(
                    "[COMMAND] capturing follow-up audio: silence=%.2fs timeout=%.2fs threshold=%.4f",
                    POST_WAKE_SILENCE_SECONDS,
                    POST_WAKE_MAX_SECONDS,
                    POST_WAKE_RMS_THRESHOLD,
                )
                command_audio = capture_followup_command(audio_q)
                if command_audio:
                    log.info(
                        "[COMMAND] captured follow-up audio: %d bytes (%.2fs)",
                        len(command_audio),
                        len(command_audio) / (SAMPLE_RATE * 2),
                    )
                send_command_audio(command_audio)
                wake_cooldown_until = time.time() + (WAKE_COOLDOWN_MS / 1000.0)
                continue

            if DEBUG_PARTIALS and text and text != last_partial and (now - last_print_t) * 1000 >= PARTIAL_PRINT_EVERY_MS:
                log.info("[idle]  %s", text)
                last_partial = text
                last_print_t = now


if __name__ == "__main__":
    main()
