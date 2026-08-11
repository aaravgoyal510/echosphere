import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from dialogue_manager.models import (
    SessionState, QualificationData, QualificationValue, 
    ObjectionRecord, SessionEscalationState, CallLogEntry
)
from dialogue_manager.escalation_engine import EscalationPolicy
from dialogue_manager.dialogue_manager import DialogueManager
from dialogue_manager.session_state import SessionStateManager
from integrations.db_manager import DBManager
from integrations.crm.mock import MockCRMAdapter
from integrations.calendar.google_calendar import GoogleCalendarAdapter
from telephony.mock import MockTelephonyAdapter
from pipeline.pipeline_coordinator import PipelineCoordinator

@pytest.fixture
def test_context():
    db = DBManager(sqlite_path="echosphere_policy_test.db")
    db.initialize_tables()
    
    session_manager = SessionStateManager()
    crm_adapter = MockCRMAdapter(db)
    calendar_adapter = GoogleCalendarAdapter(db)
    telephony_adapter = MockTelephonyAdapter()
    
    manager = DialogueManager(
        db_manager=db,
        session_manager=session_manager,
        crm_adapter=crm_adapter,
        calendar_adapter=calendar_adapter,
        telephony_adapter=telephony_adapter
    )
    
    # Pre-create CRM lead
    lead = crm_adapter.upsert_lead(phone="+15551111", name="Sarah Connor")
    
    state = SessionState(
        call_id="call_policy_test",
        started_at="2026-08-07T10:00:00Z",
        channel="inbound"
    )
    state.caller["crm_lead_id"] = lead.lead_id
    state.caller["phone"] = "+15551111"
    session_manager.save_session(state)
    
    yield db, session_manager, manager, state
    
    # Cleanup DB
    import os
    if os.path.exists("echosphere_policy_test.db"):
        try:
            os.remove("echosphere_policy_test.db")
        except Exception:
            pass


# ======================================================================
# 1. Escalation Triggers Policy Verification
# ======================================================================

def test_escalation_explicit_request():
    policy = EscalationPolicy()
    state = SessionState(call_id="c1", started_at="2026-08-07T10:00:00Z", channel="inbound")
    
    # Check keyword triggers human request
    should_esc, reason, mode = policy.evaluate(state, "I want to speak with a human agent, please.")
    assert should_esc is True
    assert reason == "explicit_request"


def test_escalation_deal_size():
    policy = EscalationPolicy(deal_size_threshold_seats=100)
    state = SessionState(call_id="c1", started_at="2026-08-07T10:00:00Z", channel="inbound")
    state.qualification.team_size = QualificationValue(value=120, last_updated_turn=1)
    
    should_esc, reason, mode = policy.evaluate(state, "We have a large team.")
    assert should_esc is True
    assert reason == "deal_size_threshold"


def test_escalation_repeated_objections():
    policy = EscalationPolicy(repeated_unresolved_objections_threshold=3)
    state = SessionState(call_id="c1", started_at="2026-08-07T10:00:00Z", channel="inbound")
    state.objections.append(ObjectionRecord(type="pricing", raised_at_turn=1, detail="Too costly", strategy_used="", resolved=False))
    state.objections.append(ObjectionRecord(type="competitor", raised_at_turn=2, detail="Hubspot", strategy_used="", resolved=False))
    state.objections.append(ObjectionRecord(type="timing", raised_at_turn=3, detail="Next month", strategy_used="", resolved=False))
    
    should_esc, reason, mode = policy.evaluate(state, "We're not ready.")
    assert should_esc is True
    assert reason == "repeated_unresolved_objections"


def test_escalation_guardrail_blocks():
    policy = EscalationPolicy(guardrail_blocks_threshold=3)
    state = SessionState(call_id="c1", started_at="2026-08-07T10:00:00Z", channel="inbound")
    
    should_esc, reason, mode = policy.evaluate(state, "Fine.", guardrail_failures_this_turn=3)
    assert should_esc is True
    assert reason == "repeated_guardrail_blocks"


def test_escalation_frustration():
    policy = EscalationPolicy()
    state = SessionState(call_id="c1", started_at="2026-08-07T10:00:00Z", channel="inbound")
    
    should_esc, reason, mode = policy.evaluate(state, "This conversation is useless, you stupid machine.")
    assert should_esc is True
    assert reason == "frustration_detected"


# ======================================================================
# 2. Time and Urgency-Based Escalation Routing
# ======================================================================

def test_escalation_routing_business_hours():
    policy = EscalationPolicy()
    state = SessionState(call_id="c1", started_at="2026-08-07T10:00:00Z", channel="inbound")
    
    # Mock datetime to a weekday at 11 AM UTC (Wednesday)
    with patch('dialogue_manager.escalation_engine.datetime') as mock_date:
        mock_date.now.return_value = datetime(2026, 8, 12, 11, 0, 0, tzinfo=timezone.utc)
        mode = policy.determine_escalation_mode(state)
        assert mode == "warm_transfer"


def test_escalation_routing_off_hours():
    policy = EscalationPolicy()
    state = SessionState(call_id="c1", started_at="2026-08-07T10:00:00Z", channel="inbound")
    
    # Mock datetime to a weekend (Sunday 11 AM UTC)
    with patch('dialogue_manager.escalation_engine.datetime') as mock_date:
        mock_date.now.return_value = datetime(2026, 8, 16, 11, 0, 0, tzinfo=timezone.utc)
        mode = policy.determine_escalation_mode(state)
        assert mode == "async_handoff"


def test_escalation_routing_off_hours_but_urgent():
    policy = EscalationPolicy()
    state = SessionState(call_id="c1", started_at="2026-08-07T10:00:00Z", channel="inbound")
    state.qualification.team_size = QualificationValue(value=75, last_updated_turn=1)  # Urgent deal size
    
    # Mock datetime to a weekend
    with patch('dialogue_manager.escalation_engine.datetime') as mock_date:
        mock_date.now.return_value = datetime(2026, 8, 16, 11, 0, 0, tzinfo=timezone.utc)
        mode = policy.determine_escalation_mode(state)
        assert mode == "warm_transfer"  # Remains warm transfer because it's urgent


# ======================================================================
# 3. Call Outcome and CallLogEntry Writing Tests (All 4 Outcome Paths)
# ======================================================================

@pytest.mark.asyncio
async def test_outcome_meeting_booked(test_context):
    db, session_manager, manager, state = test_context
    coordinator = PipelineCoordinator(manager, MagicMock(), MagicMock(), MagicMock())
    coordinator.start_call(state.call_id)
    coordinator.current_state = state

    # Trigger booking tool execution (leads to meeting_booked)
    await manager.execute_tool("book_meeting", {
        "lead_id": state.caller["crm_lead_id"],
        "slot_start": "2026-08-10T10:00:00Z",
        "slot_end": "2026-08-10T10:30:00Z",
        "meeting_type": "standard_demo"
    }, state)
    
    # Verify outcome is modified
    assert state.outcome == "meeting_booked"
    
    # End call and verify log
    coordinator.end_call()
    
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT outcome, lead_id FROM call_log_entries WHERE call_id = ?", (state.call_id,))
    row = cur.fetchone()
    assert row is not None
    assert row["outcome"] == "meeting_booked"
    assert row["lead_id"] == state.caller["crm_lead_id"]
    conn.close()


@pytest.mark.asyncio
async def test_outcome_follow_up_scheduled(test_context):
    db, session_manager, manager, state = test_context
    coordinator = PipelineCoordinator(manager, MagicMock(), MagicMock(), MagicMock())
    coordinator.start_call(state.call_id)
    coordinator.current_state = state

    # Trigger follow-up task tool (leads to follow_up_scheduled)
    await manager.execute_tool("create_follow_up_task", {
        "lead_id": state.caller["crm_lead_id"],
        "reason": "Not ready to buy yet",
        "priority": "medium",
        "context_summary": "Prospect wants to evaluate features further."
    }, state)
    
    assert state.outcome == "follow_up_scheduled"
    
    coordinator.end_call()
    
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT outcome FROM call_log_entries WHERE call_id = ?", (state.call_id,))
    row = cur.fetchone()
    assert row["outcome"] == "follow_up_scheduled"
    conn.close()


@pytest.mark.asyncio
async def test_outcome_disqualified(test_context):
    db, session_manager, manager, state = test_context
    coordinator = PipelineCoordinator(manager, MagicMock(), MagicMock(), MagicMock())
    coordinator.start_call(state.call_id)
    coordinator.current_state = state
    
    # End call without booking or escalation (defaults to disqualified)
    coordinator.end_call(default_outcome="disqualified")
    
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT outcome FROM call_log_entries WHERE call_id = ?", (state.call_id,))
    row = cur.fetchone()
    assert row["outcome"] == "disqualified"
    conn.close()


@pytest.mark.asyncio
async def test_outcome_technical_failure_resolves_to_escalated(test_context):
    db, session_manager, manager, state = test_context
    coordinator = PipelineCoordinator(manager, MagicMock(), MagicMock(), MagicMock())
    coordinator.start_call(state.call_id)
    coordinator.current_state = state
    
    # End call ungracefully (simulating connection drop / technical timeout)
    coordinator.end_call(graceful=False)
    
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT outcome, escalation_reason FROM call_log_entries WHERE call_id = ?", (state.call_id,))
    row = cur.fetchone()
    assert row["outcome"] == "escalated"
    assert "Abrupt" in row["escalation_reason"]
    conn.close()


@pytest.mark.asyncio
async def test_outcome_escalated_and_context_transfer(test_context):
    db, session_manager, manager, state = test_context
    coordinator = PipelineCoordinator(manager, MagicMock(), MagicMock(), MagicMock())
    coordinator.start_call(state.call_id)
    coordinator.current_state = state

    # Trigger escalation directly
    await manager.execute_tool("trigger_escalation", {
        "call_id": state.call_id,
        "reason": "explicit_request",
        "mode": "async_handoff"
    }, state)
    
    assert state.outcome == "escalated"
    assert state.escalation.triggered is True
    assert state.escalation.reason == "explicit_request"
    assert state.escalation.mode == "async_handoff"
    
    # End call and verify entry
    coordinator.end_call()
    
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT outcome, escalation_reason FROM call_log_entries WHERE call_id = ?", (state.call_id,))
    row = cur.fetchone()
    assert row["outcome"] == "escalated"
    assert row["escalation_reason"] == "explicit_request"
    
    # Verify that the FollowUpTask was correctly written to CRM
    tasks = manager.crm_adapter.tasks
    assert len(tasks) == 1
    task = list(tasks.values())[0]
    assert task.reason == "explicit_request"
    assert task.lead_id == state.caller["crm_lead_id"]  # Asserts CRM linkage context transferred
    conn.close()
