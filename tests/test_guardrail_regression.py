import pytest
from dialogue_manager.guardrails import verify_response_grounding

def test_competitor_guardrail_ungrounded():
    """Asserts that mentioning a competitor without the required tool calls is caught and rejected."""
    response = "Echosphere differs from HubSpot by offering sub-800ms latency."
    is_grounded, msg = verify_response_grounding(response, [], [])
    assert is_grounded is False
    assert msg is not None
    assert "competitor" in msg.lower()

def test_competitor_guardrail_grounded():
    """Asserts that mentioning a competitor with the correct tools passes the guardrail."""
    response = "Echosphere differs from HubSpot by offering sub-800ms latency."
    tool_calls = [
        {"name": "search_product_kb", "input": {"query": "competitor comparison"}},
        {"name": "log_call_event", "input": {"event_type": "objection_raised"}},
        {"name": "update_lead_qualification", "input": {"current_solution": "HubSpot"}}
    ]
    is_grounded, msg = verify_response_grounding(response, tool_calls, [])
    assert is_grounded is True
    assert msg is None

def test_price_guardrail_ungrounded():
    """Asserts that quoting prices without get_pricing_quote is rejected."""
    response = "Our Growth plan costs $30 per seat monthly."
    is_grounded, msg = verify_response_grounding(response, [], [])
    assert is_grounded is False
    assert "price" in msg.lower()

def test_price_guardrail_grounded():
    """Asserts that quoting prices with get_pricing_quote passes."""
    response = "Our Growth plan costs $30 per seat monthly."
    tool_calls = [{"name": "get_pricing_quote", "input": {"seats": 25}}]
    is_grounded, msg = verify_response_grounding(response, tool_calls, [])
    assert is_grounded is True
    assert msg is None

def test_percent_discount_guardrail_ungrounded():
    """Asserts that mentioning a percentage discount without get_pricing_quote is rejected."""
    response = "We can offer you a 10% summer discount."
    is_grounded, msg = verify_response_grounding(response, [], [])
    assert is_grounded is False
    assert "percentage" in msg.lower() or "discount" in msg.lower()

def test_percent_discount_guardrail_grounded():
    """Asserts that mentioning a percentage discount with get_pricing_quote passes."""
    response = "We can offer you a 10% summer discount."
    tool_calls = [{"name": "get_pricing_quote", "input": {"seats": 25}}]
    is_grounded, msg = verify_response_grounding(response, tool_calls, [])
    assert is_grounded is True
    assert msg is None

def test_competitor_guardrail_silent_pass_regression():
    """
    Specifically asserts that if a competitor is mentioned but only a subset of the 
    required tools are executed (e.g. search_product_kb is called, but update_lead_qualification 
    and log_call_event are missing), the guardrail correctly blocks the turn instead of silently passing.
    """
    response = "Echosphere differs from HubSpot."
    # Only search_product_kb is authorized, the other two are missing
    tool_calls = [{"name": "search_product_kb", "input": {"query": "competitor comparison"}}]
    is_grounded, msg = verify_response_grounding(response, tool_calls, [])
    assert is_grounded is False
    assert msg is not None
    assert "competitor" in msg.lower()
