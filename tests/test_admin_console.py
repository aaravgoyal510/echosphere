import os
import pytest
from integrations.db_manager import DBManager
from dialogue_manager.models import KBDocument
from integrations.kb.kb_search import KBSearchService

@pytest.fixture
def temp_db():
    db_file = "echosphere_test_admin.db"
    if os.path.exists(db_file):
        os.remove(db_file)
    db = DBManager(sqlite_path=db_file)
    db.initialize_tables()
    yield db
    if os.path.exists(db_file):
        os.remove(db_file)

def test_pricing_tier_crud(temp_db):
    # 1. Add Tier
    temp_db.save_pricing_tier(
        tier_id="test_premium",
        name="Test Premium",
        min_seats=10,
        max_seats=49,
        price_per_seat_monthly=89.99,
        included_features=["Feature A", "Feature B"],
        onboarding_fee=150.0
    )
    
    tiers = temp_db.get_all_pricing_tiers()
    assert len(tiers) == 1
    t = tiers[0]
    assert t["tier_id"] == "test_premium"
    assert t["name"] == "Test Premium"
    assert t["min_seats"] == 10
    assert t["max_seats"] == 49
    assert t["price_per_seat_monthly"] == 89.99
    assert t["onboarding_fee"] == 150.0
    assert "Feature A" in t["included_features"]
    
    # 2. Update Tier
    temp_db.save_pricing_tier(
        tier_id="test_premium",
        name="Updated Premium",
        min_seats=10,
        max_seats=99,
        price_per_seat_monthly=79.99,
        included_features=["Feature A", "Feature C"],
        onboarding_fee=100.0
    )
    
    tiers = temp_db.get_all_pricing_tiers()
    assert len(tiers) == 1
    t = tiers[0]
    assert t["name"] == "Updated Premium"
    assert t["max_seats"] == 99
    assert t["price_per_seat_monthly"] == 79.99
    assert t["onboarding_fee"] == 100.0
    assert "Feature C" in t["included_features"]
    assert "Feature B" not in t["included_features"]

    # 3. Delete Tier
    temp_db.delete_pricing_tier("test_premium")
    tiers = temp_db.get_all_pricing_tiers()
    assert len(tiers) == 0


@pytest.mark.asyncio
async def test_kb_document_crud(temp_db):
    kb_search = KBSearchService(temp_db)
    
    # 1. Add Doc
    doc = KBDocument(
        doc_id="doc_test_1",
        type="feature_doc",
        title="Test Feature Document",
        content="This doc describes test feature capabilities.",
        competitor_name=None,
        updated_at="2026-08-08T00:00:00Z"
    )
    await kb_search.add_document(doc)
    
    docs = temp_db.get_all_kb_documents()
    assert len(docs) == 1
    d = docs[0]
    assert d["doc_id"] == "doc_test_1"
    assert d["title"] == "Test Feature Document"
    assert d["type"] == "feature_doc"
    assert d["competitor_name"] is None
    
    # 2. Delete Doc
    temp_db.delete_kb_document("doc_test_1")
    docs = temp_db.get_all_kb_documents()
    assert len(docs) == 0
