"""
RPi-клиент первого этапа интеграции.

Логика:
  1. Захват микрофона (native rate), ресемпл до 16kHz mono.
  2. Маленький Vosk непрерывно ищет ключевую фразу.
  3. При обнаружении wake-word пишет событие в лог.
  4. Возвращается в IDLE и продолжает слушать дальше.

Все события пишутся в kws_scenario/logs/client.log (ротируемый, 5MB × 5).
Зависимости: vosk, sounddevice, numpy, scipy
"""
import json
import os
import queue
import time
from math import gcd
from pathlib import Path

import numpy as np
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

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

PARTIAL_PRINT_EVERY_MS = 400

log = setup_logger("voice.client", "client.log")


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
                wake_cooldown_until = now + (WAKE_COOLDOWN_MS / 1000.0)
                continue

            if DEBUG_PARTIALS and text and text != last_partial and (now - last_print_t) * 1000 >= PARTIAL_PRINT_EVERY_MS:
                log.info("[idle]  %s", text)
                last_partial = text
                last_print_t = now


if __name__ == "__main__":
    main()
