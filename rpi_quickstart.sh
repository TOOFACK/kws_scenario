#!/usr/bin/env bash
#
# Быстрый старт клиента на Raspberry Pi (или любом Linux-устройстве с микрофоном).
#
# Делает:
#   1) apt: portaudio, ffmpeg, python3-venv
#   2) venv в ./venv/, ставит requirements_client.txt
#   3) качает маленькую Vosk-модель в ./models/
#   4) показывает список микрофонов, предлагает выбрать
#   5) запускает client.py
#
# Переменные окружения (можно задать заранее):
#   VOICE_SERVER_URL  — адрес сервера, по умолчанию http://127.0.0.1:8001/voice_command
#   INPUT_DEVICE      — индекс устройства (если уже знаешь)
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

say() { printf '\n\033[1;36m[quickstart]\033[0m %s\n' "$*"; }

# ---- 1) системные зависимости ----
if ! dpkg -s libportaudio2 >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then
    say "ставлю системные зависимости (sudo)"
    sudo apt-get update
    sudo apt-get install -y libportaudio2 portaudio19-dev ffmpeg python3-venv python3-pip unzip curl
fi

# ---- 2) venv ----
if [ ! -d "venv" ]; then
    say "создаю venv"
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
say "ставлю python-зависимости"
pip install --upgrade pip >/dev/null
pip install -r requirements_client.txt

# ---- 3) модель ----
MODEL_NAME="vosk-model-small-ru-0.22"
if [ ! -d "models/${MODEL_NAME}" ]; then
    say "качаю ${MODEL_NAME} (~45 МБ)"
    mkdir -p models
    curl -L --fail -o "models/${MODEL_NAME}.zip" \
        "https://alphacephei.com/vosk/models/${MODEL_NAME}.zip"
    unzip -q "models/${MODEL_NAME}.zip" -d models
    rm "models/${MODEL_NAME}.zip"
fi

# ---- 4) выбор устройства ----
if [ -z "${INPUT_DEVICE:-}" ]; then
    say "доступные микрофоны:"
    python3 -c "import sounddevice as sd
for i, d in enumerate(sd.query_devices()):
    if d['max_input_channels'] > 0:
        print(f'  {i:2d}  {d[\"name\"]} (sr={int(d[\"default_samplerate\"])})')"
    echo
    read -rp "введи индекс устройства (Enter = default): " INPUT_DEVICE
fi

# ---- 5) сервер ----
: "${VOICE_SERVER_URL:=http://127.0.0.1:8001/voice_command}"
say "server: $VOICE_SERVER_URL"
say "device: ${INPUT_DEVICE:-default}"
say "логи пишутся в $HERE/logs/client.log"
say "запускаю клиент. Ctrl+C для выхода."
echo

# Запуск из родительской директории, чтобы работал `python -m streaming_asr.client`
cd ..
INPUT_DEVICE="$INPUT_DEVICE" VOICE_SERVER_URL="$VOICE_SERVER_URL" \
    python -u -m streaming_asr.client
