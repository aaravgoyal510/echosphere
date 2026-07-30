from abc import ABC, abstractmethod
from typing import List
from dialogue_manager.models import AvailabilityWindow, MeetingBooking

class CalendarAdapter(ABC):
    @abstractmethod
    def get_calendar_availability(self, window_start: str, window_end: str, meeting_type: str) -> List[AvailabilityWindow]:
        """Fetch available time slots for standard or enterprise demos or follow-up calls."""
        pass

    @abstractmethod
    def book_meeting(self, lead_id: str, slot_start: str, slot_end: str, meeting_type: str, attendees: List[str]) -> MeetingBooking:
        """Book a meeting on the calendar and send an invite to attendees."""
        pass
