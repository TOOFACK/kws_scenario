# streaming_asr

Голосовая активация для умного дома:
маленький Vosk на Raspberry Pi ловит wake-word, webrtcvad отрезает паузы,
WAV с командой улетает на сервер, где большой ASR (Vosk big или GigaAM)
распознаёт текст и SentenceTransformer матчит ближайший сценарий.

## Структура

```
streaming_asr/
├── server.py                  FastAPI: /voice_command, выбирает ASR backend
├── client.py                  mic + wake-word + VAD + POST
├── _logging.py                общий логгер (stdout + файл с ротацией)
├── backends/
│   ├── __init__.py            фабрика ASR-бэкендов (ASR_BACKEND env)
│   ├── vosk_backend.py        Vosk большая модель
│   └── gigaam_backend.py      GigaAM v2 от Сбера
├── scenarios.json             список сценариев (редактируй под себя)
├── download_models.sh         качает обе Vosk-модели (server + client)
├── rpi_quickstart.sh          one-click запуск клиента на RPi
├── requirements.txt           всё (сервер + клиент)
├── requirements_client.txt    только для клиента (без torch)
├── models/                    (создаётся) скачанные модели (в .gitignore)
└── logs/                      (создаётся) server.log, client.log (ротируются)
```

## Архитектура

```
RPi (клиент)                              сервер
────────────                              ──────
микрофон 16kHz/mono, 30мс чанки
  │
  ├── маленькая Vosk: ищет wake-word в partial
  │       partial содержит "салют" → CAPTURE
  │
  ├── CAPTURE:
  │       webrtcvad режет паузы,
  │       silence > 1с или max > 10с → закрыть
  │       POST wav ────────────────────────→ ASR (Vosk big / GigaAM)
  │                                          ↓
  │                                          SentenceTransformer
  │                                          cosine-sim со scenarios.json
  │       {text, scenario, score} ←──────────
  │
  └── вернулись в IDLE
```

## Логи

Каждое событие пишется с timestamp в `logs/server.log` / `logs/client.log`,
плюс дублируется в stdout. Ротация: 5MB × 5 файлов.

Пример `client.log`:
```
2026-04-17 00:22:22  INFO   voice.client  [WAKE] triggered (partial) on 'салют'
2026-04-17 00:22:24  INFO   voice.client  [capture] end by silence (1860 ms)
2026-04-17 00:22:24  INFO   voice.client  [SERVER] 268 ms | text='выключи свет' | scenario='Выключи свет' | score=1.000
```

`server.log`:
```
2026-04-17 00:22:24  INFO   voice.server  req | size=32.4KB | asr=218ms | match=50ms | total=268ms | text='выключи свет' | scenario='Выключи свет' | score=1.000
```

Посмотреть live:
```bash
tail -f streaming_asr/logs/client.log streaming_asr/logs/server.log
```

## Сервер

```bash
cd streaming_asr
./download_models.sh                            # ~1.9 ГБ для Vosk
pip install -r requirements.txt
cd ..

# Vosk (default)
python -m streaming_asr.server                   # слушает 0.0.0.0:8001

# GigaAM (качественнее и быстрее на CPU, первый запуск качает ~1 ГБ весов)
ASR_BACKEND=gigaam python -m streaming_asr.server
```

## Клиент — быстрый тест на RPi

```bash
cd streaming_asr
./rpi_quickstart.sh
```

Скрипт:
- поставит `libportaudio2`, `ffmpeg` через apt
- создаст venv, поставит `requirements_client.txt`
- скачает маленькую Vosk (~45 МБ)
- покажет список микрофонов, попросит выбрать индекс
- запустит клиент

С сервером на другой машине:
```bash
VOICE_SERVER_URL=http://192.168.1.50:8001/voice_command ./rpi_quickstart.sh
```

Ручной запуск (если RPi/десктоп уже настроен):
```bash
cd ..  # важно: из родительской директории, чтобы работал `-m streaming_asr.client`
INPUT_DEVICE=4 WAKE_WORD=салют VOICE_SERVER_URL=http://192.168.1.50:8001/voice_command \
    python -m streaming_asr.client
```

## Переменные окружения

| Переменная          | По умолчанию                                      |
|---------------------|---------------------------------------------------|
| `ASR_BACKEND`       | `vosk` (альтернатива: `gigaam`) — только сервер   |
| `GIGAAM_VARIANT`    | `v2_ctc` (альтернатива: `v2_rnnt`)                |
| `VOSK_BIG_MODEL`    | `./models/vosk-model-ru-0.42` — сервер            |
| `VOSK_SMALL_MODEL`  | `./models/vosk-model-small-ru-0.22` — клиент      |
| `VOSK_CLIENT_MODEL` | = `VOSK_SMALL_MODEL` (переопределить на big для ПК) |
| `WAKE_WORD`         | `джарвис` (в сессии использовали `салют`)         |
| `VOICE_SERVER_URL`  | `http://127.0.0.1:8001/voice_command`             |
| `INPUT_DEVICE`      | default (иначе индекс из `python -c "import sounddevice as sd; print(sd.query_devices())"`) |
| `VOICE_LOG_DIR`     | `./logs`                                          |
| `DEBUG_PARTIALS`    | `1` — партиалы в IDLE/CAPTURE, `0` — выкл         |

## Настройки в `client.py`

- `WAKE_WORD` — слово, срабатывает как подстрока в partial recognizer'а.
  Short & distinct лучше всего («салют», «робот», «компьютер»). «джарвис»
  не работает — OOV в русском словаре.
- `SILENCE_END_MS` — сколько тишины = конец команды (1000 мс).
- `MAX_COMMAND_MS` — максимум записи после wake (10 с).
- `POST_WAKE_IGNORE_MS` — игнорируем «речь» первые N мс (хвост wake-слова).
- `VAD_AGGRESSIVENESS` — 0..3, как жёстко резать паузы.

## Известные ограничения

- **Wake без паузы.** Если «салют» произнесён посреди длинной фразы,
  CAPTURE начнётся сразу после — в запись попадёт только хвост фразы,
  не отдельная команда. Лучше делать явную паузу перед wake-word.
- **Low-score false matches.** SentenceTransformer всегда возвращает
  ближайший сценарий. При мусорном тексте это бывает похоже на ложное
  срабатывание. Можно добавить порог `score < 0.75 → None` на сервере.
- **Wake-word OOV.** Если слово отсутствует в словаре Vosk (например
  «джарвис»), маленький recognizer его не распознает.
