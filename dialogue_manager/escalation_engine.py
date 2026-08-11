import logging
from datetime import datetime, timezone
from typing import Tuple, List, Optional
from dialogue_manager.models import SessionState

logger = logging.getLogger(__name__)

class EscalationPolicy:
    def __init__(
        self,
        deal_size_threshold_seats: int = 100,
        keyword_triggers: Optional[List[str]] = None,
        repeated_unresolved_objections_threshold: int = 3,
        guardrail_blocks_threshold: int = 3,
        frustration_sentiment_threshold: int = 4
    ):
        self.deal_size_threshold_seats = deal_size_threshold_seats
        self.keyword_triggers = keyword_triggers or [
            "representative", "human", "supervisor", "manager", 
            "transfer", "agent", "speak to someone", "operator"
        ]
        self.repeated_unresolved_objections_threshold = repeated_unresolved_objections_threshold
        self.guardrail_blocks_threshold = guardrail_blocks_threshold
        self.frustration_sentiment_threshold = frustration_sentiment_threshold

    def evaluate(
        self, 
        state: SessionState, 
        last_customer_text: str, 
        guardrail_failures_this_turn: int = 0
    ) -> Tuple[bool, Optional[str], str]:
        """
        Evaluates the session state against escalation trigger rules.
        Returns:
            (should_escalate, reason, mode)
        """
        text_lower = last_customer_text.lower()
        
        # Rule 1: Explicit Customer Request (Keyword Match)
        if any(kw in text_lower for kw in self.keyword_triggers):
            return True, "explicit_request", self.determine_escalation_mode(state)

        # Rule 2: Large Deal Size (team_size >= threshold)
        if state.qualification.team_size and state.qualification.team_size.value is not None:
            if state.qualification.team_size.value >= self.deal_size_threshold_seats:
                return True, "deal_size_threshold", self.determine_escalation_mode(state)

        # Rule 3: Repeated Unresolved Objections
        unresolved_objections = [obj for obj in state.objections if not obj.resolved]
        if len(unresolved_objections) >= self.repeated_unresolved_objections_threshold:
            return True, "repeated_unresolved_objections", self.determine_escalation_mode(state)

        # Rule 4: Repeated Guardrail-Blocked Responses in a single turn
        if guardrail_failures_this_turn >= self.guardrail_blocks_threshold:
            return True, "repeated_guardrail_blocks", self.determine_escalation_mode(state)

        # Rule 5: Frustration Sentiment / Sentiment Score Check
        frustration_keywords = ["angry", "frustrated", "terrible", "worst", "awful", "useless", "stupid"]
        if any(kw in text_lower for kw in frustration_keywords):
            return True, "frustration_detected", self.determine_escalation_mode(state)

        return False, None, "none"

    def determine_escalation_mode(self, state: SessionState) -> str:
        """
        Determines routing: warm_transfer for live business hours,
        async_handoff for weekends, off-hours, or low-urgency.
        """
        # Standard office hours: 9 AM to 5 PM local timezone (Asia/Kolkata)
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo("Asia/Kolkata")
            local_now = datetime.now(timezone.utc).astimezone(tz)
        except Exception:
            local_now = datetime.now(timezone.utc)
            
        is_weekday = local_now.weekday() < 5
        is_business_hours = 9 <= local_now.hour < 17

        # Urgent flag: large deal sizes (>= 50 seats) are treated as urgent
        is_urgent = False
        if state.qualification.team_size and state.qualification.team_size.value is not None:
            if state.qualification.team_size.value >= 50:
                is_urgent = True

        if not (is_weekday and is_business_hours) and not is_urgent:
            return "async_handoff"

        return "warm_transfer"
