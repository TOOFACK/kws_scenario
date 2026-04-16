"""
RPi-клиент голосового помощника.

Логика:
  1. Захват микрофона (native rate), ресемпл до 16kHz mono.
  2. IDLE: wake-recognizer с grammar ["алиса", "[unk]"] ищет ключевое слово.
  3. CAPTURE: кормим кадры основному recognizer'у + webrtcvad.
     После ~700 мс тишины или 6 сек команды — закрываем буфер.
  4. WAV → сервер. Ответ пишется в лог.
  5. Возврат в IDLE.

Все события пишутся в streaming_asr/logs/client.log (ротируемый, 5MB × 5).
Зависимости: vosk, sounddevice, webrtcvad, numpy, scipy, requests
"""
import io
import json
import os
import queue
import sys
import time
import wave
from math import gcd
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
import webrtcvad
from scipy.signal import resample_poly
from vosk import KaldiRecognizer, Model

from streaming_asr._logging import setup_logger

HERE = Path(__file__).parent
SMALL_MODEL_PATH = os.environ.get(
    "VOSK_SMALL_MODEL",
    str(HERE / "models" / "vosk-model-small-ru-0.22"),
)
CLIENT_MODEL_PATH = os.environ.get("VOSK_CLIENT_MODEL", SMALL_MODEL_PATH)
SERVER_URL = os.environ.get("VOICE_SERVER_URL", "http://127.0.0.1:8001/voice_command")
INPUT_DEVICE = os.environ.get("INPUT_DEVICE")
DEBUG_PARTIALS = os.environ.get("DEBUG_PARTIALS", "1") == "1"

WAKE_WORD = os.environ.get("WAKE_WORD", "джарвис")

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

SILENCE_END_MS = 1000
MAX_COMMAND_MS = 10000
POST_WAKE_IGNORE_MS = 300   # не считать speech первые N мс после wake (ещё слышен хвост wake-слова)
VAD_AGGRESSIVENESS = 2

PARTIAL_PRINT_EVERY_MS = 400
PARTIAL_TAIL_WORDS = 4

log = setup_logger("voice.client", "client.log")


def _frames_to_wav_bytes(frames: list[bytes]) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


def _tail(text: str, n: int) -> str:
    return " ".join(text.split()[-n:])


def main():
    log.info("loading client Vosk from %s", CLIENT_MODEL_PATH)
    model = Model(CLIENT_MODEL_PATH)
    # Без grammar — ищем wake-слово в обычном transcript'е.
    # Grammar биасит recognizer в сторону wake-слова и даёт фолсы на шуме.
    wake_rec = KaldiRecognizer(model, SAMPLE_RATE)
    cmd_rec = KaldiRecognizer(model, SAMPLE_RATE)
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

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

    state = "IDLE"
    command_frames: list[bytes] = []
    silence_ms = 0
    command_ms = 0
    heard_speech = False
    last_partial = ""
    last_print_t = 0.0

    log.info("wake word: %s (open recognizer, no grammar)", WAKE_WORD)
    log.info("server: %s", SERVER_URL)
    log.info("input device: %s (%s)", device if device is not None else "default", dev_info.get("name"))
    log.info("ready. Say '%s' + команда.", WAKE_WORD)

    with stream:
        while True:
            chunk = audio_q.get()
            now = time.time()

            if state == "IDLE":
                finalized = wake_rec.AcceptWaveform(chunk)
                text = (
                    json.loads(wake_rec.Result()).get("text", "")
                    if finalized
                    else json.loads(wake_rec.PartialResult()).get("partial", "")
                ).strip()

                if WAKE_WORD in text:
                    log.info("[WAKE] triggered (%s) on %r", "final" if finalized else "partial", text)
                    wake_rec.Reset()
                    cmd_rec.Reset()
                    command_frames = []
                    silence_ms = 0
                    command_ms = 0
                    heard_speech = False
                    last_partial = ""
                    state = "CAPTURE"
                elif DEBUG_PARTIALS and text and text != last_partial and (now - last_print_t) * 1000 >= PARTIAL_PRINT_EVERY_MS:
                    log.info("[idle]  %s", text)
                    last_partial = text
                    last_print_t = now

            elif state == "CAPTURE":
                command_frames.append(chunk)
                command_ms += FRAME_MS

                if DEBUG_PARTIALS:
                    cmd_rec.AcceptWaveform(chunk)
                    partial = json.loads(cmd_rec.PartialResult()).get("partial", "").strip()
                    if partial and partial != last_partial and (now - last_print_t) * 1000 >= PARTIAL_PRINT_EVERY_MS:
                        log.info("[cmd]   %s", _tail(partial, PARTIAL_TAIL_WORDS))
                        last_partial = partial
                        last_print_t = now

                is_speech = vad.is_speech(chunk, SAMPLE_RATE)
                # В первые POST_WAKE_IGNORE_MS игнорим "речь" — это хвост wake-слова.
                if command_ms < POST_WAKE_IGNORE_MS:
                    pass
                elif is_speech:
                    silence_ms = 0
                    heard_speech = True
                elif heard_speech:
                    silence_ms += FRAME_MS

                if (heard_speech and silence_ms >= SILENCE_END_MS) or command_ms >= MAX_COMMAND_MS:
                    reason = "silence" if heard_speech and silence_ms >= SILENCE_END_MS else "max-len"
                    log.info("[capture] end by %s (%d ms)", reason, command_ms)
                    wav_bytes = _frames_to_wav_bytes(command_frames)
                    _send(wav_bytes)
                    state = "IDLE"
                    wake_rec.Reset()
                    cmd_rec.Reset()
                    last_partial = ""


def _send(wav_bytes: bytes) -> None:
    try:
        t0 = time.time()
        r = requests.post(
            SERVER_URL,
            files={"audio_file": ("command.wav", wav_bytes, "audio/wav")},
            timeout=10,
        )
        dt = (time.time() - t0) * 1000
        if r.ok:
            data = r.json()
            log.info(
                "[SERVER] %.0f ms | text=%r | scenario=%r | score=%.3f",
                dt, data.get("text"), data.get("scenario"), data.get("score") or 0.0,
            )
        else:
            log.warning("[SERVER] HTTP %d: %s", r.status_code, r.text)
    except Exception as e:
        log.error("[SERVER] error: %s", e)


if __name__ == "__main__":
    main()
