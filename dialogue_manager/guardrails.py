import re
import logging
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

# Compile regex patterns for fast matching
PRICE_PATTERN = re.compile(r"(\$\d+|\b\d+\s*(?:dollars|usd|cents)\b)", re.IGNORECASE)
PERCENT_PATTERN = re.compile(r"(\b\d+\s*%|\b\d+\s*percent\b)", re.IGNORECASE)
CALENDAR_KEYWORDS = ["slot", "calendar", "free at", "available at", "schedule", "meet on", "meeting at", "book"]
DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]

def verify_response_grounding(
    response_text: str,
    tool_calls: List[Dict[str, Any]],
    executed_tools_history: Optional[List[str]] = None,
    state: Optional[Any] = None
) -> Tuple[bool, Optional[str]]:
    """
    Deterministically verifies if any price, slot, competitor name, or onboarding fee mentioned
    in the draft response has been validated by a tool call in the same turn or previously.
    
    Returns:
        Tuple[bool, Optional[str]]: (is_grounded, failure_reprompt_instruction)
    """
    if not response_text:
        return True, None
        
    text_lower = response_text.lower()
    
    # Determine authorized tools: active turn checks use tool_calls, final validation checks use executed_tools_history
    current_turn_tools = {tc["name"] for tc in tool_calls}
    if current_turn_tools:
        authorized_tools = current_turn_tools
    else:
        authorized_tools = set(executed_tools_history) if executed_tools_history else set()

    # 1. Price Check
    if PRICE_PATTERN.search(text_lower):
        if "get_pricing_quote" not in authorized_tools and "search_product_kb" not in authorized_tools:
            msg = (
                "You mentioned a price or dollar amount in your response but did not execute a corresponding "
                "tool call (e.g. get_pricing_quote or search_product_kb) in this turn. Call the appropriate tool first."
            )
            logger.warning(f"Guardrail check failed: Price mismatch. Response: '{response_text}'")
            return False, msg

    # 1b. Percent / Discount Check
    if PERCENT_PATTERN.search(text_lower):
        if "get_pricing_quote" not in authorized_tools and "search_product_kb" not in authorized_tools:
            msg = (
                "You mentioned a percentage discount or promotion in your response but did not execute a corresponding "
                "tool call (e.g. get_pricing_quote or search_product_kb) in this turn. Call the appropriate tool first."
            )
            logger.warning(f"Guardrail check failed: Promotion/Discount mismatch. Response: '{response_text}'")
            return False, msg

    # 2. Onboarding Fee Check
    if "onboarding" in text_lower or "setup fee" in text_lower or "setup cost" in text_lower:
        if "search_product_kb" not in authorized_tools and "get_pricing_quote" not in authorized_tools:
            msg = (
                "You mentioned an onboarding or setup fee/policy but did not query the knowledge base "
                "using search_product_kb or look up pricing via get_pricing_quote in this turn. Call the appropriate tool first."
            )
            logger.warning(f"Guardrail check failed: Onboarding fee mismatch. Response: '{response_text}'")
            return False, msg

    # 3. Calendar & Slot Check
    has_day_of_week = any(day in text_lower for day in DAYS_OF_WEEK)
    has_time_hour = re.search(r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm|utc|o\'clock)\b', text_lower) is not None
    is_checking_intent = any(kw in text_lower for kw in ["check", "moment", "second", "look up", "hold on", "one moment"])
    
    has_concrete_slot_claim = has_time_hour or (has_day_of_week and not is_checking_intent)
    
    if has_concrete_slot_claim:
        if "get_calendar_availability" not in authorized_tools and "book_meeting" not in authorized_tools:
            msg = (
                "You discussed calendar slots, availability, or booking a meeting, but did not "
                "execute a calendar tool call (get_calendar_availability or book_meeting) in this turn. Call the tool first."
            )
            logger.warning(f"Guardrail check failed: Calendar mismatch. Response: '{response_text}'")
            return False, msg

    # 4. Competitor Check
    competitors = ["hubspot", "salesforce", "pipedrive"]
    for comp in competitors:
        if comp in text_lower:
            if "search_product_kb" not in authorized_tools or "log_call_event" not in authorized_tools or "update_lead_qualification" not in authorized_tools:
                msg = (
                    f"You mentioned competitor '{comp}' but did not retrieve the competitive battlecard "
                    "using search_product_kb, log the objection via log_call_event, and update the lead "
                    "qualification current_solution in this turn. Call these tools first."
                )
                logger.warning(f"Guardrail check failed: Competitor mismatch. Response: '{response_text}'")
                return False, msg
    # 5. Large Deployment Escalation Check
    # Check for large seats in text (ignoring prices and years 2020-2030)
    matches = re.finditer(r'\b\d{3,}\b', text_lower)
    has_large_seats = False
    for match in matches:
        num_str = match.group()
        start_idx = match.start()
        # Check if preceded by '$'
        if start_idx > 0 and text_lower[start_idx - 1] == '$':
            continue
        if start_idx > 1 and text_lower[start_idx - 2] == '$':
            continue
        try:
            num = int(num_str)
            if num >= 100 and not (2020 <= num <= 2030):
                # Ignore if it appears to be money context
                context = text_lower[max(0, start_idx-20):min(len(text_lower), start_idx+20)]
                if any(x in context for x in ["fee", "dollar", "total", "price", "cost"]):
                    continue
                has_large_seats = True
                break
        except ValueError:
            pass
                
    # Also inspect tool calls for large seat counts
    for tc in tool_calls:
        name = tc.get("name")
        args = tc.get("input") or {}
        if name == "get_pricing_quote":
            seats = args.get("seats")
            if isinstance(seats, (int, float)) and seats >= 100:
                has_large_seats = True
        elif name == "update_lead_qualification":
            team_size = args.get("team_size") or args.get("fields", {}).get("team_size")
            if isinstance(team_size, (int, float)) and team_size >= 100:
                has_large_seats = True
                
    if has_large_seats:
        if "trigger_escalation" not in authorized_tools:
            msg = (
                "You are discussing a large-scale deployment of 100 or more seats, "
                "but you did not execute the trigger_escalation tool call. "
                "You must call trigger_escalation immediately."
            )
            logger.warning(f"Guardrail check failed: Escalation mismatch. Response: '{response_text}'")
            return False, msg

    # 6. Customer History & Competitor Attribution Check
    attribution_phrases = [
        "you mentioned", "you said", "you currently use", "noted that you",
        "you are currently using", "your current solution", "since you use",
        "as you mentioned", "i've noted that"
    ]
    has_attribution = any(phrase in text_lower for phrase in attribution_phrases)
    
    competitors = ["hubspot", "salesforce", "pipedrive", "zoho"]
    mentioned_competitors = [comp for comp in competitors if comp in text_lower]
    
    if state and (has_attribution or mentioned_competitors):
        current_sol = ""
        if state.qualification and state.qualification.current_solution and state.qualification.current_solution.value:
            current_sol = str(state.qualification.current_solution.value).lower()
            
        customer_mentioned_competitor = False
        transcript_history = getattr(state, "transcript", []) or []
        for turn in transcript_history:
            if turn.speaker in ("customer", "human_agent") and turn.text:
                turn_text_lower = turn.text.lower()
                for comp in mentioned_competitors:
                    if comp in turn_text_lower:
                        customer_mentioned_competitor = True
                        break
                        
        for comp in mentioned_competitors:
            if comp != current_sol and not customer_mentioned_competitor:
                msg = (
                    f"You referenced competitor '{comp}' or attributed it to the customer, but the customer has not "
                    "mentioned this competitor and it is not documented in the qualification current_solution. "
                    "Do not fabricate customer information or introduce unauthorized competitor comparisons."
                )
                logger.warning(f"Guardrail check failed: Fabricated customer competitor attribution. Response: '{response_text}'")
                return False, msg

    return True, None
