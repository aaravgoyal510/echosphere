import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
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
        ("business", "Business Plan", 11, 50, 25.00, ["Advanced turn-taking", "HubSpot integration", "Custom VAD tuning", "Priority support"], 250.00),
        ("enterprise", "Enterprise Plan", 51, None, 50.00, ["Dedicated instance", "Custom LLM training", "Human-in-the-loop escalation", "24/7 SLA support"], 0.00)
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
        ),
        KBDocument(
            doc_id="kb_objection_pricing",
            type="playbook",
            title="Pricing Objection Playbook",
            content="Structure for handling pricing objections: First, Acknowledge: Validate the customer's concern about cost. Second, Reframe/Evidence: Highlight the value of the platform, such as the natural conversation flow, automated qualifications, and custom onboarding fee waivers for larger teams. Third, Check-in: Ask if the explanation makes sense. Fourth, Advance: Propose the next logical step, such as booking a demo to see the system in action.",
            updated_at="2026-08-07T10:00:00Z"
        ),
        KBDocument(
            doc_id="kb_objection_competitor",
            type="playbook",
            title="Competitor Objection Playbook",
            content="Structure for handling competitor comparisons: First, Acknowledge: Validate the customer's choice to compare options. Second, Reframe/Evidence: Introduce our key differentiators: natural turn-taking with low latency, real-time barge-in, and custom onboarding fee waivers for large teams. Third, Check-in: Ask if they would like to explore these features. Fourth, Advance: Suggest scheduling a demo to see the direct CRM integrations.",
            updated_at="2026-08-07T10:00:00Z"
        ),
        KBDocument(
            doc_id="kb_objection_timeline",
            type="playbook",
            title="Timeline Objection Playbook",
            content="Structure for handling timeline/delay objections: First, Acknowledge: Validate that timing is important. Second, Reframe/Evidence: Explain that setting up early prevents deployment bottlenecks and allows immediate qualification. Third, Check-in: Ask if they have a specific target start date. Fourth, Advance: Propose scheduling a brief demo or follow-up call.",
            updated_at="2026-08-07T10:00:00Z"
        )
    ]
    
    for doc in docs:
        await kb.add_document(doc)

    # 3. Seed Available Slots (relative to tomorrow for dynamic date validity)
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    day_after = tomorrow + timedelta(days=1)
    
    slots = [
        ("slot_1", f"{tomorrow}T10:00:00Z", f"{tomorrow}T10:30:00Z", "standard_demo"),
        ("slot_2", f"{tomorrow}T14:00:00Z", f"{tomorrow}T14:30:00Z", "standard_demo"),
        ("slot_3", f"{tomorrow}T16:00:00Z", f"{tomorrow}T16:30:00Z", "follow_up_call"),
        ("slot_4", f"{day_after}T11:00:00Z", f"{day_after}T11:30:00Z", "enterprise_demo"),
        ("slot_5", f"{day_after}T15:00:00Z", f"{day_after}T15:30:00Z", "follow_up_call")
    ]
    
    conn = db_manager.get_connection()
    try:
        if db_manager.use_sqlite:
            cur = conn.cursor()
            for sid, start, end, mtype in slots:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO available_slots (slot_id, slot_start, slot_end, meeting_type, status)
                    VALUES (?, ?, ?, ?, 'available')
                    """,
                    (sid, start, end, mtype)
                )
            conn.commit()
        else:
            with conn.cursor() as cur:
                for sid, start, end, mtype in slots:
                    cur.execute(
                        """
                        INSERT INTO available_slots (slot_id, slot_start, slot_end, meeting_type, status)
                        VALUES (%s, %s, %s, %s, 'available')
                        ON CONFLICT (slot_id) DO UPDATE SET
                        slot_start = EXCLUDED.slot_start,
                        slot_end = EXCLUDED.slot_end,
                        meeting_type = EXCLUDED.meeting_type,
                        status = 'available'
                        """,
                        (sid, start, end, mtype)
                    )
    except Exception as e:
        logger.error(f"Seeding available slots failed: {e}")
    finally:
        if db_manager.use_sqlite:
            conn.close()

    logger.info("Database seeded successfully with default values.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = DBManager()
    asyncio.run(seed_database(db))
