import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from integrations.db_manager import DBManager
from dialogue_manager.dialogue_manager import DialogueManager
from dialogue_manager.session_state import SessionStateManager
from dialogue_manager.models import SessionState, TranscriptTurn, ObjectionRecord, QualificationData, QualificationValue
from pipeline.pipeline_coordinator import PipelineCoordinator
from integrations.crm.mock import MockCRMAdapter
from integrations.calendar.mock import MockCalendarAdapter

@pytest.fixture
def stats_context():
    db_file = "echosphere_test_stats.db"
    if os.path.exists(db_file):
        os.remove(db_file)
    db = DBManager(sqlite_path=db_file)
    db.initialize_tables()
    
    session_manager = SessionStateManager()
    crm_adapter = MockCRMAdapter(db)
    calendar_adapter = MockCalendarAdapter()
    
    manager = DialogueManager(
        db_manager=db,
        session_manager=session_manager,
        crm_adapter=crm_adapter,
        calendar_adapter=calendar_adapter,
        telephony_adapter=MagicMock()
    )
    
    yield db, manager
    
    if os.path.exists(db_file):
        os.remove(db_file)

@pytest.mark.asyncio
async def test_call_stats_aggregation_on_end_call(stats_context):
    db, manager = stats_context
    
    # 1. Setup SessionState
    start_time = datetime.now(timezone.utc) - timedelta(minutes=5) # 5 minutes duration
    state = SessionState(
        call_id="call_stats_abc",
        started_at=start_time.isoformat(),
        channel="inbound"
    )
    
    # Set qualification
    state.qualification.team_size = QualificationValue(value=35, last_updated_turn=2)
    state.qualification.current_solution = QualificationValue(value="HubSpot", last_updated_turn=3)
    
    # Set objections (2 raised, 1 resolved)
    state.objections.append(ObjectionRecord(
        type="pricing",
        raised_at_turn=1,
        detail="Too expensive",
        strategy_used="reframe",
        resolved=True,
        resolved_at_turn=2
    ))
    state.objections.append(ObjectionRecord(
        type="competitor",
        raised_at_turn=3,
        detail="HubSpot has features",
        strategy_used="battlecard",
        resolved=False
    ))
    
    # Transcript
    state.transcript.append(TranscriptTurn(
        turn_id=1, speaker="customer", text="How much is Echosphere?", timestamp=start_time.isoformat()
    ))
    state.transcript.append(TranscriptTurn(
        turn_id=2, speaker="agent", text="We have customized plans.", timestamp=start_time.isoformat()
    ))
    
    # Guardrail triggers
    state.guardrail_trigger_count = 3
    state.outcome = "follow_up_scheduled"
    
    # Save session
    manager.session_manager.save_session(state)
    
    # Initialize coordinator
    coordinator = PipelineCoordinator(
        dialogue_manager=manager,
        stt=MagicMock(),
        tts=MagicMock(),
        turn_taking_manager=MagicMock()
    )
    coordinator.start_call("call_stats_abc")
    coordinator.current_state = state
    
    # 2. Trigger end call
    coordinator.end_call(graceful=True)
    
    # 3. Assert call_stats row exists and contains aggregated values
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM call_stats WHERE call_id = ?", ("call_stats_abc",))
        row = cur.fetchone()
        
        assert row is not None
        assert row["outcome"] == "follow_up_scheduled"
        assert row["objections_raised"] == 2
        assert row["objections_resolved"] == 1
        assert row["guardrail_triggers"] == 3
        assert row["team_size"] == 35
        assert row["competitors_mentioned"] == "HubSpot has features"
        assert row["duration_seconds"] >= 299 # approx 5 minutes
    finally:
        conn.close()
