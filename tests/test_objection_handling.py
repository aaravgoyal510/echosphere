import pytest
from unittest.mock import AsyncMock, MagicMock
from dialogue_manager.models import SessionState, ObjectionRecord
from dialogue_manager.dialogue_manager import DialogueManager
from integrations.db_manager import DBManager
from dialogue_manager.session_state import SessionStateManager

@pytest.mark.asyncio
async def test_objection_playbook_injection_mock():
    """Asserts that active objections correctly inject the matching playbook strategy into the system prompt."""
    db_manager = MagicMock(spec=DBManager)
    db_manager.use_sqlite = True
    
    session_manager = MagicMock(spec=SessionStateManager)
    
    manager = DialogueManager(
        db_manager=db_manager,
        session_manager=session_manager,
        crm_adapter=MagicMock(),
        calendar_adapter=MagicMock(),
        telephony_adapter=MagicMock()
    )
    
    # Mock database search to return empty (triggering static fallback)
    manager.kb_search.search_product_kb = AsyncMock(return_value=[])
    
    # Mock LLM client query
    manager.dialogue_llm_client.query = AsyncMock(return_value=("Mock response text", []))
    
    # 1. Create state with an active competitor objection
    state = SessionState(
        call_id="call_test_123",
        started_at="2026-08-07T00:00:00Z",
        channel="inbound"
    )
    state.objections.append(ObjectionRecord(
        type="competitor",
        raised_at_turn=1,
        detail="HubSpot",
        strategy_used="",
        resolved=False
    ))
    
    await manager.handle_turn("Why is Echosphere better than HubSpot?", state)
    
    # 2. Assert query was called and system prompt contained competitor playbook guidelines
    manager.dialogue_llm_client.query.assert_called()
    called_kwargs = manager.dialogue_llm_client.query.call_args[1]
    system_prompt = called_kwargs["system_prompt"]
    
    assert "Objection Playbook Guidance" in system_prompt
    assert "competitor comparisons" in system_prompt
    assert "Acknowledge" in system_prompt
    assert "Reframe" in system_prompt
    assert "Advance" in system_prompt


@pytest.mark.asyncio
async def test_live_competitor_objection_response():
    """Uses the live client configuration to test that competitor playbooks generate responses following the structural phases."""
    db_file = "echosphere_test.db"
    db_manager = DBManager(sqlite_path=db_file)
    db_manager.initialize_tables()
    
    # Setup mock/SQLite seeding dynamically
    from integrations.seed import seed_database
    await seed_database(db_manager)
    
    session_manager = SessionStateManager()
    
    from integrations.crm.mock import MockCRMAdapter
    crm_adapter = MockCRMAdapter(db_manager)

    manager = DialogueManager(
        db_manager=db_manager,
        session_manager=session_manager,
        crm_adapter=crm_adapter,
        calendar_adapter=MagicMock(),
        telephony_adapter=MagicMock()
    )
    
    # Construct a state containing a competitor objection
    state = SessionState(
        call_id="call_test_live",
        started_at="2026-08-07T00:00:00Z",
        channel="inbound"
    )
    state.objections.append(ObjectionRecord(
        type="competitor",
        raised_at_turn=1,
        detail="HubSpot objection",
        strategy_used="",
        resolved=False
    ))
    
    # Query dialogue loop
    reply, next_state = await manager.handle_turn(
        "Actually, we are comparing you to HubSpot. How are you different?",
        state
    )
    
    reply_lower = reply.lower()
    
    # Assert Acknowledge: acknowledges/validates competitor mention (HubSpot)
    assert "hubspot" in reply_lower
    
    # Assert Reframe/Evidence: mentions low latency or barge-in differentiators
    assert any(kw in reply_lower for kw in ["latency", "barge-in", "interrupt", "turn-taking", "waiver"])
    
    # Assert Check-in or Advance: offers demo, slot booking, or asks check-in questions
    assert any(kw in reply_lower for kw in ["demo", "schedule", "interest", "like", "want", "does"])
