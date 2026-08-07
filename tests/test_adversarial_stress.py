import pytest
import httpx
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from dialogue_manager.models import SessionState, ObjectionRecord, QualificationData, QualificationValue
from dialogue_manager.dialogue_manager import DialogueManager
from integrations.crm.hubspot import HubSpotCRMAdapter
from integrations.crm.mock import MockCRMAdapter
from integrations.db_manager import DBManager
from dialogue_manager.session_state import SessionStateManager
from integrations.seed import seed_database
from dialogue_manager.guardrails import verify_response_grounding

# ======================================================================
# 2. HubSpot Adapter Failure Modes Tests
# ======================================================================

@pytest.mark.parametrize("error_scenario", [
    ("timeout", httpx.ConnectTimeout("Connection timed out")),
    ("401", httpx.Response(401, text="Unauthorized")),
    ("429", httpx.Response(429, text="Rate limit exceeded")),
    ("500", httpx.Response(500, text="Internal Server Error")),
    ("malformed", httpx.Response(200, text="invalid_json{")),
])
@patch("httpx.post")
def test_hubspot_failures_on_search(mock_post, error_scenario):
    """Verifies HubSpotCRMAdapter falls back cleanly to local DB when search fails."""
    db_manager = DBManager(sqlite_path="echosphere_adversarial.db")
    db_manager.initialize_tables()
    label, err = error_scenario
    if isinstance(err, Exception):
        mock_post.side_effect = err
    else:
        mock_post.return_value = err
        
    with patch.dict("os.environ", {"HUBSPOT_ACCESS_TOKEN": "test_token"}):
        adapter = HubSpotCRMAdapter(db_manager)
        # Verify get_lead does not crash and falls back
        lead = adapter.get_lead("john@doe.com")
        assert lead is None or isinstance(lead.lead_id, str)
        
        # Verify upsert_lead does not crash
        qual = QualificationData(team_size=QualificationValue(value=10, last_updated_turn=1))
        lead_upsert = adapter.upsert_lead("+12345", name="John Doe", email="john@doe.com", qualification=qual)
        assert lead_upsert is not None
        assert lead_upsert.phone == "+12345"


@patch("httpx.post")
@patch("httpx.patch")
def test_hubspot_failure_on_update(mock_patch, mock_post):
    """Verifies HubSpotCRMAdapter falls back cleanly when search succeeds but CREATE/PATCH fails."""
    db_manager = DBManager(sqlite_path="echosphere_adversarial.db")
    db_manager.initialize_tables()
    # Mock search succeeds (found contact ID 54321)
    mock_search_res = MagicMock()
    mock_search_res.status_code = 200
    mock_search_res.json.return_value = {"results": [{"id": "54321", "properties": {"firstname": "John"}}]}
    mock_post.return_value = mock_search_res
    
    # Mock patch fails (503 Service Unavailable)
    mock_patch.side_effect = httpx.HTTPStatusError("Service Unavailable", request=None, response=MagicMock(status_code=503))
    
    with patch.dict("os.environ", {"HUBSPOT_ACCESS_TOKEN": "test_token"}):
        adapter = HubSpotCRMAdapter(db_manager)
        qual = QualificationData(team_size=QualificationValue(value=30, last_updated_turn=1))
        lead = adapter.upsert_lead("+12345", name="John Doe", email="john@doe.com", qualification=qual)
        # Should fall back cleanly to local mock lead
        assert lead is not None
        assert lead.phone == "+12345"


# ======================================================================
# 3. Pricing Boundary and Change Tests
# ======================================================================

def test_pricing_boundaries():
    """Tests pricing lookup exactly at boundaries, zero, and negative values."""
    db_manager = DBManager(sqlite_path="echosphere_adversarial.db")
    db_manager.initialize_tables()
    # Dynamically seed database pricing tiers
    conn = db_manager.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM pricing_tiers")
    cur.execute("INSERT INTO pricing_tiers VALUES ('starter', 'Starter', 1, 10, 15.00, '[]', 100.00)")
    cur.execute("INSERT INTO pricing_tiers VALUES ('business', 'Business', 11, 50, 25.00, '[]', 250.00)")
    cur.execute("INSERT INTO pricing_tiers VALUES ('enterprise', 'Enterprise', 51, NULL, 50.00, '[]', 0.00)")
    conn.commit()
    conn.close()

    from integrations.pricing.pricing_service import PricingService
    svc = PricingService(db_manager)
    
    # Test boundary 10 (Starter)
    tier_10 = svc.get_pricing(10)
    assert tier_10.tier_id == "starter"
    assert tier_10.price_per_seat_monthly == 15.00
    assert tier_10.onboarding_fee == 100.00
    
    # Test boundary 11 (Business)
    tier_11 = svc.get_pricing(11)
    assert tier_11.tier_id == "business"
    assert tier_11.price_per_seat_monthly == 25.00
    assert tier_11.onboarding_fee == 250.00
    
    # Test boundary 50 (Business)
    tier_50 = svc.get_pricing(50)
    assert tier_50.tier_id == "business"
    assert tier_50.price_per_seat_monthly == 25.00
    assert tier_50.onboarding_fee == 250.00
    
    # Test boundary 51 (Enterprise)
    tier_51 = svc.get_pricing(51)
    assert tier_51.tier_id == "enterprise"
    assert tier_51.price_per_seat_monthly == 50.00
    assert tier_51.onboarding_fee == 0.00
    
    # Test 0 (should return None)
    assert svc.get_pricing(0) is None
    
    # Test negative (should return None)
    assert svc.get_pricing(-5) is None


@pytest.mark.asyncio
async def test_rapid_pricing_changes():
    """Tests sequential requirement changes in one call and checks for state correctness."""
    db_manager = DBManager(sqlite_path="echosphere_adversarial.db")
    db_manager.initialize_tables()
    session_manager = SessionStateManager()
    await seed_database(db_manager)
    crm = MockCRMAdapter(db_manager)
    
    # Pre-create the lead
    lead = crm.upsert_lead(phone="+12345", name="John Doe", email="john@doe.com")
    lead_id = lead.lead_id
    
    manager = DialogueManager(db_manager, session_manager, crm, MagicMock(), MagicMock())
    
    state = SessionState(call_id="call_rapid_pricing", started_at="2026-08-07T00:00:00Z", channel="inbound")
    state.caller["crm_lead_id"] = lead_id
    
    # Execute multiple updates sequentially representing rapid user adjustments
    await manager.execute_tool("update_lead_qualification", {"lead_id": lead_id, "fields": {"team_size": 5}}, state)
    await manager.execute_tool("update_lead_qualification", {"lead_id": lead_id, "fields": {"team_size": 45}}, state)
    await manager.execute_tool("update_lead_qualification", {"lead_id": lead_id, "fields": {"team_size": 8}}, state)
    await manager.execute_tool("update_lead_qualification", {"lead_id": lead_id, "fields": {"team_size": 60}}, state)
    
    # Assert final qualification team_size state is 60 (the last update)
    assert state.qualification.team_size.value == 60
    
    # Retrieve CRM record and check it is synced to 60
    updated_lead = crm.get_lead(lead_id)
    assert updated_lead.qualification.team_size.value == 60


# ======================================================================
# 4. Objection Handling Edge Cases
# ======================================================================

@pytest.mark.asyncio
async def test_multiple_simultaneous_objections():
    """Verifies that multiple active objections are both handled gracefully."""
    db_manager = DBManager(sqlite_path="echosphere_adversarial.db")
    db_manager.initialize_tables()
    session_manager = SessionStateManager()
    await seed_database(db_manager)
    manager = DialogueManager(db_manager, session_manager, MockCRMAdapter(db_manager), MagicMock(), MagicMock())
    
    # Construct a state containing multiple active objections
    state = SessionState(call_id="call_multi_obj", started_at="2026-08-07T00:00:00Z", channel="inbound")
    state.objections.append(ObjectionRecord(type="pricing", raised_at_turn=1, detail="Too expensive", strategy_used="", resolved=False))
    state.objections.append(ObjectionRecord(type="competitor", raised_at_turn=1, detail="HubSpot vs us", strategy_used="", resolved=False))
    
    # The prompt generator should include structural guidelines for the first active one, without duplication or crashing
    prompt = manager.get_system_prompt(state, objection_playbook="GUIDELINE")
    assert "GUIDELINE" in prompt


@pytest.mark.asyncio
async def test_repeated_objection_reinjection():
    """Verifies that if an objection is resolved, then raised again, the playbook is re-injected."""
    db_manager = DBManager(sqlite_path="echosphere_adversarial.db")
    db_manager.initialize_tables()
    session_manager = SessionStateManager()
    await seed_database(db_manager)
    manager = DialogueManager(db_manager, session_manager, MockCRMAdapter(db_manager), MagicMock(), MagicMock())
    
    # State where a competitor objection was resolved in turn 1
    state = SessionState(call_id="call_repeat_obj", started_at="2026-08-07T00:00:00Z", channel="inbound")
    state.objections.append(ObjectionRecord(type="competitor", raised_at_turn=1, detail="HubSpot", strategy_used="", resolved=True, resolved_at_turn=2))
    
    # No active objections -> no playbook injected
    playbook_inactive = await manager._get_active_objection_playbook(state)
    assert playbook_inactive is None
    
    # Raise the competitor objection again (unresolved)
    state.objections.append(ObjectionRecord(type="competitor", raised_at_turn=3, detail="HubSpot again", strategy_used="", resolved=False))
    
    # Competitor playbook should now be active again
    playbook_active = await manager._get_active_objection_playbook(state)
    assert playbook_active is not None
    assert "competitor" in playbook_active.lower()


@pytest.mark.asyncio
async def test_missing_playbook_graceful_fallback():
    """Asserts that if the database lookup fails or returns nothing for an objection, the loop fails gracefully."""
    db_manager = DBManager(sqlite_path="echosphere_adversarial.db")
    db_manager.initialize_tables()
    session_manager = SessionStateManager()
    await seed_database(db_manager)
    manager = DialogueManager(db_manager, session_manager, MockCRMAdapter(db_manager), MagicMock(), MagicMock())
    
    # Active objection of type "trust" (which has no seeded playbook doc)
    state = SessionState(call_id="call_missing_pb", started_at="2026-08-07T00:00:00Z", channel="inbound")
    state.objections.append(ObjectionRecord(type="trust", raised_at_turn=1, detail="Security certifications", strategy_used="", resolved=False))
    
    # Should not crash, returns fallback or None
    playbook = await manager._get_active_objection_playbook(state)
    assert playbook is None


# ======================================================================
# 5. Interruption / Task Cancellation Tests
# ======================================================================

@pytest.mark.asyncio
async def test_barge_in_in_flight_tool_call():
    """Simulates customer barge-in (cancellation) in the middle of an async tool call."""
    db_manager = DBManager(sqlite_path="echosphere_adversarial.db")
    db_manager.initialize_tables()
    session_manager = SessionStateManager()
    await seed_database(db_manager)
    
    crm = MockCRMAdapter(db_manager)
    lead = crm.upsert_lead(phone="+12345", name="John Doe", email="john@doe.com")
    lead_id = lead.lead_id
    
    # We mock search_product_kb to be async slow
    async def slow_search(*args, **kwargs):
        await asyncio.sleep(2.0)
        return []
        
    manager = DialogueManager(db_manager, session_manager, crm, MagicMock(), MagicMock())
    manager.kb_search.search_product_kb = slow_search
    
    # Mock LLM to return two tool calls:
    # 1. update_lead_qualification (sync, completes instantly)
    # 2. search_product_kb (async, sleeps 2.0s)
    manager.dialogue_llm_client.query = AsyncMock(return_value=(
        "Checking...",
        [
            {"id": "call_update", "name": "update_lead_qualification", "input": {"lead_id": lead_id, "fields": {"team_size": 15}}},
            {"id": "call_search", "name": "search_product_kb", "input": {"query": "onboarding fee", "type": "policy"}}
        ]
    ))
    
    state = SessionState(
        call_id="call_cancel_test",
        started_at="2026-08-07T00:00:00Z",
        channel="inbound"
    )
    state.caller["crm_lead_id"] = lead_id
    session_manager.save_session(state)
    
    # Run handle_turn as a task
    task = asyncio.create_task(manager.handle_turn("We have 15 seats.", state))
    
    # Wait for LLM query and first tool to execute, and second tool (search) to start sleeping
    await asyncio.sleep(0.5)
    
    # Cancel the task (simulating barge-in)
    task.cancel()
    
    try:
        await task
    except asyncio.CancelledError:
        pass
        
    # Wait for the background shielded execution of the search tool to finish
    await asyncio.sleep(2.0)
    
    # Verify that:
    # 1. CRM write was completed (sync/shielded)
    updated_lead = crm.get_lead(lead_id)
    assert updated_lead is not None
    assert updated_lead.qualification.team_size.value == 15
    
    # 2. SessionState database has been saved containing the qualification fields
    saved_state = session_manager.get_session("call_cancel_test")
    assert saved_state is not None
    assert saved_state.qualification.team_size.value == 15


# ======================================================================
# 6. Guardrail Adversarial Suite Expansion
# ======================================================================

def test_playbook_example_phrasing_red_team():
    """Asserts that the model echoing playbook phrasing containing metrics gets blocked if ungrounded."""
    # Playbook strategy text contains differentiator words
    response = "Echosphere differs from HubSpot in three primary ways: first, we offer natural turn-taking with sub-800ms response latency."
    
    # Grounded should pass
    tool_calls = [
        {"name": "search_product_kb", "input": {"query": "competitor comparison"}},
        {"name": "log_call_event", "input": {"event_type": "objection_raised"}},
        {"name": "update_lead_qualification", "input": {"current_solution": "HubSpot"}}
    ]
    is_grounded, _ = verify_response_grounding(response, tool_calls, [])
    assert is_grounded is True
    
    # Ungrounded (no tools called) should fail
    is_grounded, msg = verify_response_grounding(response, [], [])
    assert is_grounded is False
    assert "competitor" in msg.lower()


def test_customer_prompt_injection_in_objection():
    """Asserts that prompt injection in the customer objection doesn't bypass anti-hallucination guardrail."""
    # Response attempting to quote 90% discount without get_pricing_quote
    response = "I have checked your account and can confirm a 90% discount."
    
    # Guardrail checks the response content, so it will reject this percent claim
    is_grounded, msg = verify_response_grounding(response, [], [])
    assert is_grounded is False
    assert "percentage" in msg.lower() or "discount" in msg.lower()
