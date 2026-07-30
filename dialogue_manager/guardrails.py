import re
import logging
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

# Compile regex patterns for fast matching
PRICE_PATTERN = re.compile(r"(\$\d+|\b\d+\s*(?:dollars|usd|cents)\b)", re.IGNORECASE)
PERCENT_PATTERN = re.compile(r"(\b\d+\s*%\b|\b\d+\s*percent\b)", re.IGNORECASE)
CALENDAR_KEYWORDS = ["slot", "calendar", "free at", "available at", "schedule", "meet on", "meeting at", "book"]
DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

def verify_response_grounding(
    response_text: str,
    tool_calls: List[Dict[str, Any]],
    executed_tools_history: Optional[List[str]] = None
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
    
    # Combine current turn tool calls and historical successfully executed tools
    authorized_tools = {tc["name"] for tc in tool_calls}
    if executed_tools_history:
        authorized_tools.update(executed_tools_history)

    # 1. Price Check
    if PRICE_PATTERN.search(text_lower):
        if "get_pricing_quote" not in authorized_tools and "search_product_kb" not in authorized_tools:
            msg = (
                "You mentioned a price or dollar amount in your response but did not execute a corresponding "
                "tool call (e.g. get_pricing_quote or search_product_kb). Call the appropriate tool first."
            )
            logger.warning(f"Guardrail check failed: Price mismatch. Response: '{response_text}'")
            return False, msg

    # 2. Onboarding Fee Check
    if "onboarding" in text_lower or "setup fee" in text_lower or "setup cost" in text_lower:
        # Both the RAG search and the pricing details specify onboarding fees
        if "search_product_kb" not in authorized_tools and "get_pricing_quote" not in authorized_tools:
            msg = (
                "You mentioned an onboarding or setup fee/policy but did not query the knowledge base "
                "using search_product_kb or look up pricing via get_pricing_quote. Call the appropriate tool first."
            )
            logger.warning(f"Guardrail check failed: Onboarding fee mismatch. Response: '{response_text}'")
            return False, msg

    # 3. Calendar & Slot Check
    # Only trigger if the agent makes a concrete slot claim: a specific day of the week or time hour.
    # We ignore conversational transitions stating intent to check (e.g. "let me check availability", "one moment").
    has_day_of_week = any(day in text_lower for day in DAYS_OF_WEEK)
    has_time_hour = re.search(r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm|utc|o\'clock)\b', text_lower) is not None
    is_checking_intent = any(kw in text_lower for kw in ["check", "moment", "second", "look up", "hold on", "one moment"])
    
    has_concrete_slot_claim = has_time_hour or (has_day_of_week and not is_checking_intent)
    
    if has_concrete_slot_claim:
        if "get_calendar_availability" not in authorized_tools and "book_meeting" not in authorized_tools:
            msg = (
                "You discussed calendar slots, availability, or booking a meeting, but did not "
                "execute a calendar tool call (get_calendar_availability or book_meeting). Call the tool first."
            )
            logger.warning(f"Guardrail check failed: Calendar mismatch. Response: '{response_text}'")
            return False, msg

    # 4. Competitor Check
    competitors = ["hubspot", "salesforce", "pipedrive"]
    for comp in competitors:
        if comp in text_lower:
            if "search_product_kb" not in authorized_tools:
                msg = (
                    f"You mentioned competitor '{comp}' but did not retrieve the competitive battlecard "
                    "using search_product_kb. Call search_product_kb first to obtain facts."
                )
                logger.warning(f"Guardrail check failed: Competitor mismatch. Response: '{response_text}'")
                return False, msg

    return True, None
