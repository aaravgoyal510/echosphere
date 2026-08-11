import os
import sys
import json
import logging
import asyncio
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure import paths resolve properly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from integrations.db_manager import DBManager
from integrations.crm.mock import MockCRMAdapter
from integrations.calendar.mock import MockCalendarAdapter
from integrations.kb.kb_search import KBSearchService
from integrations.pricing.pricing_service import PricingService
from dialogue_manager.dialogue_manager import DialogueManager
from dialogue_manager.session_state import SessionStateManager
from dialogue_manager.models import SessionState, KBDocument
from pipeline.pipeline_coordinator import PipelineCoordinator
from pipeline.whisper_adapter import WhisperSTTAdapter
from pipeline.piper_adapter import PiperTTSAdapter
from telephony.base import TelephonyAdapter
from unittest.mock import MagicMock

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("web_server")

app = FastAPI(title="EchoSphere Voice Agent Demo Dashboard")

# Initialize database manager and search services
db = DBManager()
db.initialize_tables()

session_manager = SessionStateManager()
crm_adapter = MockCRMAdapter(db)
calendar_adapter = MockCalendarAdapter()
telephony_adapter = MagicMock(spec=TelephonyAdapter)
kb_search = KBSearchService(db)

# Seed initial database values if empty
from integrations.seed import seed_database
asyncio.run(seed_database(db))

# VAD Threshold for barge-in detection on raw PCM input
VAD_ENERGY_THRESHOLD = 0.015

class PricingTierModel(BaseModel):
    tier_id: str
    name: str
    min_seats: int
    max_seats: Optional[int] = None
    price_per_seat_monthly: float
    included_features: List[str]
    onboarding_fee: float

class KBDocumentModel(BaseModel):
    doc_id: str
    type: str
    title: str
    content: str
    competitor_name: Optional[str] = None

@app.get("/api/pricing")
def get_pricing():
    return db.get_all_pricing_tiers()

@app.post("/api/pricing")
def save_pricing(tier: PricingTierModel):
    db.save_pricing_tier(
        tier.tier_id, tier.name, tier.min_seats, tier.max_seats,
        tier.price_per_seat_monthly, tier.included_features, tier.onboarding_fee
    )
    return {"status": "success"}

@app.delete("/api/pricing/{tier_id}")
def delete_pricing(tier_id: str):
    db.delete_pricing_tier(tier_id)
    return {"status": "success"}

@app.get("/api/kb")
def get_kb():
    return db.get_all_kb_documents()

@app.post("/api/kb")
def save_kb(doc: KBDocumentModel):
    kb_doc = KBDocument(
        doc_id=doc.doc_id,
        type=doc.type,
        title=doc.title,
        content=doc.content,
        competitor_name=doc.competitor_name,
        updated_at=datetime.now(timezone.utc).isoformat()
    )
    kb_search.add_document(kb_doc)
    return {"status": "success"}

@app.delete("/api/kb/{doc_id}")
def delete_kb(doc_id: str):
    db.delete_kb_document(doc_id)
    return {"status": "success"}

@app.get("/api/stats")
def get_stats():
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        if db.use_sqlite:
            cur.execute("SELECT outcome, COUNT(*) as count FROM call_stats GROUP BY outcome")
            outcomes = {row["outcome"]: row["count"] for row in cur.fetchall()}
            
            cur.execute("SELECT AVG(duration_seconds) as avg_duration FROM call_stats")
            avg_dur = cur.fetchone()["avg_duration"] or 0.0
            
            cur.execute("SELECT SUM(objections_raised) as raised, SUM(objections_resolved) as resolved, SUM(guardrail_triggers) as guardrails FROM call_stats")
            totals = cur.fetchone()
            raised = totals["raised"] or 0
            resolved = totals["resolved"] or 0
            guardrails = totals["guardrails"] or 0
        else:
            cur.execute("SELECT outcome, COUNT(*) as count FROM call_stats GROUP BY outcome")
            outcomes = {row[0]: row[1] for row in cur.fetchall()}
            
            cur.execute("SELECT AVG(duration_seconds) FROM call_stats")
            avg_dur = cur.fetchone()[0] or 0.0
            
            cur.execute("SELECT SUM(objections_raised), SUM(objections_resolved), SUM(guardrail_triggers) FROM call_stats")
            totals = cur.fetchone()
            raised = totals[0] or 0
            resolved = totals[1] or 0
            guardrails = totals[2] or 0
            
        return {
            "outcomes": outcomes,
            "avg_duration_seconds": round(avg_dur, 2),
            "total_objections_raised": raised,
            "total_objections_resolved": resolved,
            "total_guardrail_triggers": guardrails
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if db.use_sqlite:
            conn.close()

@app.post("/api/learning/distill")
async def distill_patterns():
    from scripts.batch_distillation import run_distillation
    try:
        await run_distillation()
        report_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "distillation_report.md")
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                content = f.read()
            return {"status": "success", "report": content}
        return {"status": "success", "report": "No report was generated."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/{call_id}")
async def websocket_call(websocket: WebSocket, call_id: str):
    await websocket.accept()
    logger.info(f"WebSocket client connected for call: {call_id}")

    loop = asyncio.get_running_loop()
    audio_queue = asyncio.Queue()

    # Define audio chunk callback that queues output raw PCM to be sent over WS
    def on_audio_chunk(chunk: bytes):
        loop.call_soon_threadsafe(audio_queue.put_nowait, chunk)

    # Initialize the real adapters
    stt = WhisperSTTAdapter()
    tts = PiperTTSAdapter(on_audio_chunk=on_audio_chunk)

    manager = DialogueManager(
        db_manager=db,
        session_manager=session_manager,
        crm_adapter=crm_adapter,
        calendar_adapter=calendar_adapter,
        telephony_adapter=telephony_adapter
    )

    coordinator = PipelineCoordinator(
        dialogue_manager=manager,
        stt=stt,
        tts=tts,
        turn_taking_manager=MagicMock()  # Mock TurnTakingManager as we drive it directly
    )

    # Resume call if state already exists
    state = session_manager.get_session(call_id)
    if state:
        logger.info(f"Resuming existing call session {call_id}")
    else:
        state = SessionState(
            call_id=call_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            channel="inbound"
        )
        session_manager.save_session(state)

    coordinator.start_call(call_id)
    coordinator.current_state = state

    # Define transcript callbacks to update WebSocket client
    async def on_stt_final(text: str):
        logger.info(f"[WS-STT] Final text detected: '{text}'")
        await websocket.send_json({
            "type": "transcript_update",
            "speaker": "customer",
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        # Execute turn
        await coordinator.process_customer_utterance(text)
        # Send post-turn state update
        fresh_state = session_manager.get_session(call_id)
        await websocket.send_json({
            "type": "state_update",
            "state": fresh_state.model_dump()
        })

    async def on_stt_interim(text: str):
        await websocket.send_json({
            "type": "interim_update",
            "text": text
        })

    # Start speech recognition
    await stt.start_listening(on_interim=on_stt_interim, on_final=on_stt_final)

    # Send initial state update
    await websocket.send_json({
        "type": "state_update",
        "state": state.model_dump()
    })

    # Task to handle sending audio chunks from queue to WebSocket
    async def send_audio_loop():
        try:
            while True:
                chunk = await audio_queue.get()
                if chunk == b"[TTS_STOPPED_MID_SENTENCE]":
                    logger.info("[WS-TTS] Cancel audio signal sent to client")
                    await websocket.send_json({"type": "cancel_audio"})
                else:
                    await websocket.send_bytes(chunk)
                audio_queue.task_done()
        except asyncio.CancelledError:
            pass

    send_task = asyncio.create_task(send_audio_loop())

    try:
        while True:
            # Expecting binary PCM audio bytes (16kHz, 16-bit mono)
            data = await websocket.receive()
            if "bytes" in data:
                chunk = data["bytes"]
                
                # VAD Interruption Check: calculate Root Mean Square energy of input PCM chunk
                samples = np.frombuffer(chunk, dtype=np.int16)
                samples_float = samples.astype(np.float32) / 32768.0
                energy = float(np.sqrt(np.mean(samples_float ** 2))) if len(samples_float) > 0 else 0.0

                if energy > VAD_ENERGY_THRESHOLD and tts.speaking:
                    logger.info(f"[VAD] Speech energy {energy:.4f} exceeds threshold. Triggering agent interruption.")
                    # Run interruption path
                    await coordinator.on_interruption()
                    await websocket.send_json({"type": "barge_in"})

                # Forward mic input chunk to local STT adapter
                await stt.receive_audio_chunk(chunk)
                
            elif "text" in data:
                # Handle any text controls (e.g. manual hangup)
                msg = json.loads(data["text"])
                if msg.get("type") == "hangup":
                    logger.info("Client requested hangup")
                    break
                elif msg.get("type") == "chat_message":
                    text = msg.get("text", "").strip()
                    if text:
                        logger.info(f"[WS-Chat] Received text turn: '{text}'")
                        await websocket.send_json({
                            "type": "transcript_update",
                            "speaker": "customer",
                            "text": text,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                        await coordinator.process_customer_utterance(text)
                        fresh_state = session_manager.get_session(call_id)
                        await websocket.send_json({
                            "type": "state_update",
                            "state": fresh_state.model_dump()
                        })
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected abruptly")
        # Ungraceful disconnect updates outcome to escalated per specs
        coordinator.end_call(graceful=False)
    except Exception as e:
        logger.error(f"Error in WebSocket handler: {e}")
        # Outage fallback sets state to escalated
        if coordinator.current_state:
            coordinator.current_state.outcome = "escalated"
            coordinator.current_state.escalation.triggered = True
            coordinator.current_state.escalation.reason = f"Outage exception: {str(e)}"
            session_manager.save_session(coordinator.current_state)
            await websocket.send_json({
                "type": "state_update",
                "state": coordinator.current_state.model_dump()
            })
    finally:
        # Stop listening and clean up
        await stt.stop_listening()
        await tts.stop_speaking()
        send_task.cancel()
        
        # Safe Graceful Call Close
        if coordinator.current_state and coordinator.current_state.outcome == "in_progress":
            coordinator.end_call(graceful=True)
            
        logger.info(f"WebSocket session complete for call: {call_id}")

# Mount static folder
os.makedirs(os.path.join(os.path.dirname(__file__), "static"), exist_ok=True)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

@app.get("/")
def read_root():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
