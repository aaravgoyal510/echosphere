import json
import logging
import asyncio
from dialogue_manager.models import KBDocument
from integrations.db_manager import DBManager
from integrations.kb.kb_search import KBSearchService

logger = logging.getLogger(__name__)

async def seed_database(db_manager: DBManager):
    db_manager.initialize_tables()
    conn = db_manager.get_connection()
    
    # 1. Seed Pricing Tiers
    tiers = [
        ("starter", "Starter Plan", 1, 10, 15.00, ["Core CRM integration", "Basic Call Analytics", "Email support"], 100.00),
        ("business", "Business Plan", 11, 40, 25.00, ["Advanced turn-taking", "HubSpot integration", "Custom VAD tuning", "Priority support"], 250.00),
        ("enterprise", "Enterprise Plan", 41, None, 50.00, ["Dedicated instance", "Custom LLM training", "Human-in-the-loop escalation", "24/7 SLA support"], 0.00)
    ]
    
    if db_manager.use_sqlite:
        try:
            cur = conn.cursor()
            for tid, name, mn, mx, price, features, fee in tiers:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO pricing_tiers (tier_id, name, min_seats, max_seats, price_per_seat_monthly, included_features, onboarding_fee)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tid, name, mn, mx, price, json.dumps(features), fee)
                )
            
            # Seed Promo
            cur.execute(
                """
                INSERT OR REPLACE INTO promotions (promo_id, description, discount_pct, valid_until, applies_to_tiers)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("SUMMER10", "Summer discount", 10.0, "2026-09-30", json.dumps(["starter", "business"]))
            )
            conn.commit()
        finally:
            conn.close()
    else:
        try:
            with conn.cursor() as cur:
                for tid, name, mn, mx, price, features, fee in tiers:
                    cur.execute(
                        """
                        INSERT INTO pricing_tiers (tier_id, name, min_seats, max_seats, price_per_seat_monthly, included_features, onboarding_fee)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tier_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        min_seats = EXCLUDED.min_seats,
                        max_seats = EXCLUDED.max_seats,
                        price_per_seat_monthly = EXCLUDED.price_per_seat_monthly,
                        included_features = EXCLUDED.included_features,
                        onboarding_fee = EXCLUDED.onboarding_fee
                        """,
                        (tid, name, mn, mx, price, features, fee)
                    )
                
                cur.execute(
                    """
                    INSERT INTO promotions (promo_id, description, discount_pct, valid_until, applies_to_tiers)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (promo_id) DO UPDATE SET
                    description = EXCLUDED.description,
                    discount_pct = EXCLUDED.discount_pct,
                    valid_until = EXCLUDED.valid_until,
                    applies_to_tiers = EXCLUDED.applies_to_tiers
                    """,
                    ("SUMMER10", "Summer discount", 10.0, "2026-09-30", ["starter", "business"])
                )
        except Exception as e:
            logger.error(f"Postgres pricing seeding error: {e}")

    # 2. Seed KB Documents
    # We import KBSearchService locally to avoid circular dependencies
    from integrations.kb.kb_search import KBSearchService
    kb = KBSearchService(db_manager)
    
    docs = [
        KBDocument(
            doc_id="kb_competitor_comparison",
            type="competitive_battlecard",
            title="Comparison with Competitor X",
            content="Our sales agent platform differs from Competitor X in three main ways: first, we offer natural turn-taking with sub-800ms response latency. Second, our system supports natural barge-in, meaning the agent halts within 200ms when interrupted. Third, our pricing features custom onboarding fee waivers for larger teams, whereas Competitor X charges flat onboarding fees.",
            competitor_name="Competitor X",
            updated_at="2026-07-29T10:00:00Z"
        ),
        KBDocument(
            doc_id="kb_onboarding_fee_policy",
            type="policy",
            title="Onboarding Fee Policy",
            content="We charge an onboarding fee to cover custom integrations and network setup. For the Starter tier, the onboarding fee is $100. For the Business tier (11 to 40 seats), the onboarding fee is $250. For the Enterprise tier (41+ seats), the onboarding fee is completely waived.",
            updated_at="2026-07-29T10:00:00Z"
        ),
        KBDocument(
            doc_id="kb_features_general",
            type="feature_doc",
            title="General Product Features",
            content="Our voice sales agent integrates directly with CRM databases (like HubSpot) to write qualification logs. It checks Google Calendar live for available demo slots, books slots directly, and handles warm transfer handoffs to human reps.",
            updated_at="2026-07-29T10:00:00Z"
        )
    ]
    
    for doc in docs:
        await kb.add_document(doc)
    logger.info("Database seeded successfully with default values.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = DBManager()
    asyncio.run(seed_database(db))
