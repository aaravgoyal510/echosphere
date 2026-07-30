import pytest
import asyncio
from dialogue_manager.models import SessionState, KBDocument
from dialogue_manager.session_state import SessionStateManager
from integrations.db_manager import DBManager
from integrations.pricing.pricing_service import PricingService
from integrations.kb.kb_search import KBSearchService
from integrations.seed import seed_database

@pytest.fixture(scope="module")
def db_manager():
    # Use a separate test SQLite database for test runs
    manager = DBManager(sqlite_path="echosphere_test.db")
    # Initialize and seed it
    asyncio.run(seed_database(manager))
    yield manager
    
    # Cleanup test db file after test module finishes
    try:
        import os
        if os.path.exists("echosphere_test.db"):
            os.remove("echosphere_test.db")
    except Exception:
        pass


def test_pricing_lookup(db_manager):
    pricing = PricingService(db_manager)
    
    # Starter plan check (e.g. 5 seats)
    tier = pricing.get_pricing(5)
    assert tier is not None
    assert tier.tier_id == "starter"
    assert tier.price_per_seat_monthly == 15.00
    assert tier.onboarding_fee == 100.00
    assert "Core CRM integration" in tier.included_features
    # Check that promotion is applied
    assert len(tier.active_promotions) > 0
    assert tier.active_promotions[0].promo_id == "SUMMER10"
    
    # Business plan check (e.g. 25 seats)
    tier = pricing.get_pricing(25)
    assert tier is not None
    assert tier.tier_id == "business"
    assert tier.price_per_seat_monthly == 25.00
    assert tier.onboarding_fee == 250.00
    assert "HubSpot integration" in tier.included_features
    
    # Enterprise plan check (e.g. 50 seats)
    tier = pricing.get_pricing(50)
    assert tier is not None
    assert tier.tier_id == "enterprise"
    assert tier.price_per_seat_monthly == 50.00
    assert tier.onboarding_fee == 0.00
    assert "Human-in-the-loop escalation" in tier.included_features
    # No promotion for enterprise
    assert len(tier.active_promotions) == 0


@pytest.mark.asyncio
async def test_kb_vector_search(db_manager):
    kb = KBSearchService(db_manager)
    
    # Search for competitor comparison
    results = await kb.search_product_kb("how do we compare to Competitor X?", competitor_name="Competitor X")
    assert len(results) > 0
    assert results[0].doc_id == "kb_competitor_comparison"
    assert "barge-in" in results[0].content
    
    # Search for onboarding fee policy
    results = await kb.search_product_kb("do I have to pay an onboarding fee?")
    assert len(results) > 0
    assert results[0].doc_id == "kb_onboarding_fee_policy"
    assert "Starter" in results[0].content


def test_session_state_persistence():
    state_manager = SessionStateManager()
    
    # Create test session
    session = SessionState(
        call_id="call_test_123",
        started_at="2026-07-29T10:00:00Z",
        channel="inbound"
    )
    
    # Save session
    state_manager.save_session(session)
    
    # Retrieve session
    retrieved = state_manager.get_session("call_test_123")
    assert retrieved is not None
    assert retrieved.call_id == "call_test_123"
    assert retrieved.channel == "inbound"
    assert retrieved.outcome == "in_progress"
    
    # Non-existent session
    assert state_manager.get_session("call_non_existent") is None
