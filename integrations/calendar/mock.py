import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

from dialogue_manager.models import AvailabilityWindow, MeetingBooking
from integrations.calendar.base import CalendarAdapter

logger = logging.getLogger(__name__)

class MockCalendarAdapter(CalendarAdapter):
    def __init__(self):
        # In-memory store for meeting bookings
        self.bookings: Dict[str, MeetingBooking] = {}

    def get_calendar_availability(
        self,
        window_start: str,
        window_end: str,
        meeting_type: str
    ) -> List[AvailabilityWindow]:
        """
        Generates realistic dummy availability slots (weekdays 10:00, 11:30, 14:00, 15:30)
        within the requested search window.
        """
        logger.info(f"CALENDAR: Fetching availability window from {window_start} to {window_end} for type '{meeting_type}'")
        
        try:
            start_dt = datetime.fromisoformat(window_start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(window_end.replace('Z', '+00:00'))
        except Exception:
            # Fallback to current time + 7 days
            start_dt = datetime.now(timezone.utc)
            end_dt = start_dt + timedelta(days=7)
            
        slots = []
        curr = start_dt
        
        # Check every day in the window
        while curr <= end_dt:
            # Only weekday slots (Monday-Friday)
            if curr.weekday() < 5:
                # 3 standard slots per day: 10:00, 13:00, 15:00 UTC/local
                hours = [10, 13, 15]
                for hr in hours:
                    slot_start = curr.replace(hour=hr, minute=0, second=0, microsecond=0)
                    slot_end = slot_start + timedelta(minutes=30)
                    
                    if start_dt <= slot_start <= end_dt:
                        # Check if already booked
                        start_str = slot_start.isoformat()
                        booked = any(b.start_time == start_str for b in self.bookings.values())
                        
                        if not booked:
                            slots.append(AvailabilityWindow(
                                start=slot_start.isoformat(),
                                end=slot_end.isoformat()
                            ))
            curr += timedelta(days=1)
            
        # Limit to first 5 slots for clean presentation
        return slots[:5]

    def book_meeting(
        self,
        lead_id: str,
        slot_start: str,
        slot_end: str,
        meeting_type: str,
        notes: Optional[str] = None,
        attendees: Optional[List[str]] = None
    ) -> MeetingBooking:
        """Book a meeting on the calendar in-memory."""
        meeting_id = f"mtg_{int(datetime.now(timezone.utc).timestamp())}"
        booking = MeetingBooking(
            meeting_id=meeting_id,
            lead_id=lead_id,
            attendees=attendees or ["customer@example.com"],
            start_time=slot_start,
            end_time=slot_end,
            meeting_type=meeting_type,
            calendar_event_id=f"evt_{meeting_id}",
            confirmation_sent=True
        )
        
        self.bookings[meeting_id] = booking
        logger.info(f"CALENDAR: Meeting booked successfully: {booking}")
        return booking
