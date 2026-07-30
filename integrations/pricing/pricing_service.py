import json
import logging
from typing import Optional, List
from dialogue_manager.models import PricingTier, Promotion
from integrations.db_manager import DBManager

logger = logging.getLogger(__name__)

class PricingService:
    def __init__(self, db_manager: DBManager):
        self.db = db_manager

    def get_pricing(self, team_size: int, check_promotions: bool = True) -> Optional[PricingTier]:
        """
        Gets the deterministic pricing tier and features for a given team size.
        """
        conn = self.db.get_connection()
        tier = None
        
        if self.db.use_sqlite:
            try:
                cur = conn.cursor()
                
                # Fetch matching tier by seats
                cur.execute(
                    """
                    SELECT tier_id, name, min_seats, max_seats, price_per_seat_monthly, included_features, onboarding_fee
                    FROM pricing_tiers
                    WHERE min_seats <= ? AND (max_seats IS NULL OR max_seats >= ?)
                    """,
                    (team_size, team_size)
                )
                row = cur.fetchone()
                
                if row:
                    included_features = json.loads(row["included_features"])
                    tier = PricingTier(
                        tier_id=row["tier_id"],
                        name=row["name"],
                        min_seats=row["min_seats"],
                        max_seats=row["max_seats"],
                        price_per_seat_monthly=row["price_per_seat_monthly"],
                        included_features=included_features,
                        onboarding_fee=row["onboarding_fee"],
                        active_promotions=[]
                    )
                    
                    if check_promotions:
                        # Pull promotions matching this tier
                        cur.execute("SELECT promo_id, description, discount_pct, valid_until, applies_to_tiers FROM promotions")
                        promo_rows = cur.fetchall()
                        for p_row in promo_rows:
                            applies = json.loads(p_row["applies_to_tiers"])
                            if tier.tier_id in applies:
                                promo = Promotion(
                                    promo_id=p_row["promo_id"],
                                    description=p_row["description"],
                                    discount_pct=p_row["discount_pct"],
                                    valid_until=p_row["valid_until"],
                                    applies_to_tiers=applies
                                )
                                tier.active_promotions.append(promo)
            finally:
                conn.close()
        else:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT tier_id, name, min_seats, max_seats, price_per_seat_monthly, included_features, onboarding_fee
                        FROM pricing_tiers
                        WHERE min_seats <= %s AND (max_seats IS NULL OR max_seats >= %s)
                        """,
                        (team_size, team_size)
                    )
                    row = cur.fetchone()
                    if row:
                        tier = PricingTier(
                            tier_id=row[0],
                            name=row[1],
                            min_seats=row[2],
                            max_seats=row[3],
                            price_per_seat_monthly=float(row[4]),
                            included_features=list(row[5]),
                            onboarding_fee=float(row[6]),
                            active_promotions=[]
                        )
                        
                        if check_promotions:
                            cur.execute("SELECT promo_id, description, discount_pct, valid_until, applies_to_tiers FROM promotions")
                            promo_rows = cur.fetchall()
                            for p_row in promo_rows:
                                applies = list(p_row[4])
                                if tier.tier_id in applies:
                                    promo = Promotion(
                                        promo_id=p_row[0],
                                        description=p_row[1],
                                        discount_pct=float(p_row[2]),
                                        valid_until=p_row[3],
                                        applies_to_tiers=applies
                                    )
                                    tier.active_promotions.append(promo)
            except Exception as e:
                logger.error(f"Error fetching pricing from PostgreSQL: {e}")
                
        return tier
