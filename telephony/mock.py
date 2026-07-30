import logging
from typing import Dict, Any
from telephony.base import TelephonyAdapter

logger = logging.getLogger(__name__)

class MockTelephonyAdapter(TelephonyAdapter):
    def __init__(self):
        self.transferred_calls: Dict[str, Dict[str, Any]] = {}
        self.disconnected_calls: Dict[str, bool] = {}

    def initiate_warm_transfer(
        self,
        call_id: str,
        human_phone_or_sip: str,
        briefing_card: Dict[str, Any]
    ) -> bool:
        """
        Simulates bridging the current call to a human agent and logging the briefing card.
        """
        logger.info(f"TELEPHONY: Initiating warm transfer for Call={call_id} to Agent={human_phone_or_sip}")
        logger.info(f"TELEPHONY: Briefing Card sent: {briefing_card}")
        
        self.transferred_calls[call_id] = {
            "agent": human_phone_or_sip,
            "briefing_card": briefing_card
        }
        return True

    def disconnect_call(self, call_id: str) -> None:
        """Simulates disconnecting the call."""
        logger.info(f"TELEPHONY: Disconnecting Call={call_id}")
        self.disconnected_calls[call_id] = True
