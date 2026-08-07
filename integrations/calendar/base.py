from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class CalendarAdapter(ABC):
    @abstractmethod
    def get_calendar_availability(
        self, 
        window_start: str, 
        window_end: str, 
        meeting_type: str
    ) -> List[Dict[str, str]]:
        """
        Query available booking slots inside the window window_start to window_end.
        Returns a list of slots, e.g. [{"slot_start": "...", "slot_end": "..."}].
        """
        pass

    @abstractmethod
    def book_meeting(
        self,
        lead_id: str,
        slot_start: str,
        slot_end: str,
        meeting_type: str,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Book a slot for the specified lead.
        Returns a dictionary representing the booked meeting event details.
        """
        pass
