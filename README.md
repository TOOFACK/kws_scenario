# kws_scenario

Первый этап интеграции для умного дома:
маленький Vosk на Raspberry Pi непрерывно слушает микрофон,
ловит wake-word и пишет в лог момент распознавания триггер-фразы.

## Структура

```
kws_scenario/
├── server.py                  серверная часть (пока не используется в первом этапе)
├── client.py                  mic + wake-word + логирование trigger phrase
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

```text
RPi (клиент)
────────────
микрофон 16kHz/mono, 30мс чанки
  │
  ├── маленькая Vosk: ищет wake-word в partial
  │       partial содержит "салют" → LOG
  │
  └── вернулись в IDLE
```

## Логи

Каждое событие пишется с timestamp в `logs/server.log` / `logs/client.log`,
плюс дублируется в stdout. Ротация: 5MB × 5 файлов.

Пример `client.log`:
```
2026-04-17 00:22:22  INFO   voice.client  [WAKE] triggered (partial) on 'салют'
2026-04-17 00:22:24  INFO   voice.client  [WAKE] trigger phrase detected (partial) on 'салют'
```

Посмотреть live:
```bash
tail -f kws_scenario/logs/client.log
```

## Клиент — быстрый тест на RPi

```bash
cd kws_scenario
./rpi_quickstart.sh
```

Скрипт:
- поставит `libportaudio2`, `ffmpeg` через apt
- создаст venv, поставит `requirements_client.txt`
- скачает маленькую Vosk (~45 МБ)
- покажет список микрофонов, попросит выбрать индекс
- запустит клиент

Ручной запуск (если RPi/десктоп уже настроен):
```bash
cd ..  # важно: из родительской директории, чтобы работал `-m kws_scenario.client`
INPUT_DEVICE=4 WAKE_WORD=салют \
    python -m kws_scenario.client
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
| `WAKE_COOLDOWN_MS`  | `1500`                                            |
| `INPUT_DEVICE`      | default (иначе индекс из `python -c "import sounddevice as sd; print(sd.query_devices())"`) |
| `VOICE_LOG_DIR`     | `./logs`                                          |
| `DEBUG_PARTIALS`    | `1` — партиалы в IDLE, `0` — выкл                 |

## Настройки в `client.py`

- `WAKE_WORD` — слово, срабатывает как подстрока в partial recognizer'а.
  Short & distinct лучше всего («салют», «робот», «компьютер»). «джарвис»
  не работает — OOV в русском словаре.
- `WAKE_COOLDOWN_MS` — защита от повторных логов сразу после одной и той же wake-фразы.

## Известные ограничения

- **Wake-word OOV.** Если слово отсутствует в словаре Vosk (например
  «джарвис»), маленький recognizer его не распознает.
