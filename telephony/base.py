from abc import ABC, abstractmethod
from typing import Dict, Any

class TelephonyAdapter(ABC):
    @abstractmethod
    def initiate_warm_transfer(self, call_id: str, human_phone_or_sip: str, briefing_card: Dict[str, Any]) -> bool:
        """
        Bridges the current call to a human agent and sends a briefing card.
        Returns True if successful, False otherwise.
        """
        pass

    @abstractmethod
    def disconnect_call(self, call_id: str) -> None:
        """Gracefully disconnects/ends the active call."""
        pass
