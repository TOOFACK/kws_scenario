"""
Сервер голосовой команды.

POST /voice_command  — multipart WAV/MP3 любого sample-rate.
Возвращает: {"text": "...", "scenario": "...", "score": 0.87}

ASR-бэкенд выбирается env var'ом `ASR_BACKEND=vosk|gigaam` (default vosk).

Логи: kws_scenario/logs/server.log (ротация 5MB × 5) + stdout.
"""
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from select_scenario.compare_scenarios import ScenarioComparator
from kws_scenario._logging import setup_logger
from kws_scenario.backends import get_backend

HERE = Path(__file__).parent
SCENARIOS_PATH = HERE / "scenarios.json"

log = setup_logger("voice.server", "server.log")

app = FastAPI(title="Smart Home Voice Command API")

log.info("ASR backend: %s", os.environ.get("ASR_BACKEND", "vosk"))
_asr = get_backend()

log.info("loading scenarios from %s", SCENARIOS_PATH)
with open(SCENARIOS_PATH, encoding="utf-8") as f:
    SCENARIOS: list[str] = json.load(f)

log.info("loading sentence-transformer and computing scenario embeddings")
_comparator = ScenarioComparator()
_scenario_vectors = _comparator.get_scenario_embeddings(SCENARIOS)
log.info("ready: %d scenarios, vectors %s", len(SCENARIOS), _scenario_vectors.shape)


@app.post("/voice_command")
async def voice_command(audio_file: UploadFile = File(...)):
    try:
        t0 = time.time()
        raw = await audio_file.read()
        size_kb = len(raw) / 1024

        text = _asr.transcribe(raw)
        asr_ms = (time.time() - t0) * 1000

        if not text:
            log.info("req | size=%.1fKB | asr=%.0fms | text='' | no scenario", size_kb, asr_ms)
            return JSONResponse({"text": "", "scenario": None, "score": 0.0})

        t1 = time.time()
        scenario, score = _comparator.compare_scenarios(
            text, _scenario_vectors, SCENARIOS
        )
        match_ms = (time.time() - t1) * 1000
        total_ms = (time.time() - t0) * 1000

        log.info(
            "req | size=%.1fKB | asr=%.0fms | match=%.0fms | total=%.0fms "
            "| text=%r | scenario=%r | score=%.3f",
            size_kb, asr_ms, match_ms, total_ms, text, scenario, score,
        )

        return JSONResponse(
            {"text": text, "scenario": scenario, "score": float(score)}
        )
    except Exception as e:
        log.exception("request failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scenarios")
async def list_scenarios():
    return {"scenarios": SCENARIOS}


@app.get("/")
async def root():
    return {
        "status": "ok",
        "scenarios": len(SCENARIOS),
        "asr_backend": os.environ.get("ASR_BACKEND", "vosk"),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
