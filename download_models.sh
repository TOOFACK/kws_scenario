#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="$HERE/models"
mkdir -p "$MODELS_DIR"

SMALL_NAME="vosk-model-small-ru-0.22"
BIG_NAME="vosk-model-ru-0.42"

SMALL_URL="https://alphacephei.com/vosk/models/${SMALL_NAME}.zip"
BIG_URL="https://alphacephei.com/vosk/models/${BIG_NAME}.zip"

download_and_unzip() {
    local name="$1"
    local url="$2"
    local target="$MODELS_DIR/$name"

    if [ -d "$target" ]; then
        echo "[skip] $name already present"
        return
    fi

    echo "[get]  $name"
    tmp="$MODELS_DIR/${name}.zip"
    curl -L --fail -o "$tmp" "$url"
    unzip -q "$tmp" -d "$MODELS_DIR"
    rm "$tmp"
    echo "[done] $name -> $target"
}

download_and_unzip "$SMALL_NAME" "$SMALL_URL"
download_and_unzip "$BIG_NAME" "$BIG_URL"

echo
echo "Models ready in $MODELS_DIR"
