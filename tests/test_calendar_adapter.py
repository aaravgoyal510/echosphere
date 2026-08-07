import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from integrations.db_manager import DBManager
from integrations.calendar.google_calendar import GoogleCalendarAdapter

@pytest.fixture
def db_manager():
    db = DBManager(sqlite_path="echosphere_calendar_test.db")
    db.initialize_tables()
    
    # Pre-seed dynamic available slots in local DB for fallback testing
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM available_slots")
    cur.execute(
        """
        INSERT INTO available_slots (slot_id, slot_start, slot_end, meeting_type, status)
        VALUES 
        ('slot_1', '2026-08-10T10:00:00Z', '2026-08-10T10:30:00Z', 'standard_demo', 'available'),
        ('slot_2', '2026-08-10T14:00:00Z', '2026-08-10T14:30:00Z', 'standard_demo', 'available'),
        ('slot_3', '2026-08-11T11:00:00Z', '2026-08-11T11:30:00Z', 'enterprise_demo', 'available')
        """
    )
    conn.commit()
    conn.close()
    
    yield db
    
    # Cleanup DB file after test runs
    import os
    if os.path.exists("echosphere_calendar_test.db"):
        try:
            os.remove("echosphere_calendar_test.db")
        except Exception:
            pass


def test_calendar_availability_fallback(db_manager):
    """Verifies adapter falls back cleanly to local SQLite when API is not configured."""
    with patch.dict("os.environ", {"GOOGLE_CALENDAR_CREDENTIALS_PATH": "", "GOOGLE_CALENDAR_ID": ""}):
        adapter = GoogleCalendarAdapter(db_manager)
        
        # Test availability query
        slots = adapter.get_calendar_availability(
            window_start="2026-08-10T00:00:00Z",
            window_end="2026-08-10T23:59:59Z",
            meeting_type="standard_demo"
        )
        assert len(slots) == 2
        assert slots[0]["slot_start"] == "2026-08-10T10:00:00Z"
        assert slots[1]["slot_start"] == "2026-08-10T14:00:00Z"


def test_calendar_booking_fallback(db_manager):
    """Verifies local booking persistence and status update on fallback path."""
    with patch.dict("os.environ", {"GOOGLE_CALENDAR_CREDENTIALS_PATH": "", "GOOGLE_CALENDAR_ID": ""}):
        adapter = GoogleCalendarAdapter(db_manager)
        
        booking = adapter.book_meeting(
            lead_id="lead_987",
            slot_start="2026-08-10T10:00:00Z",
            slot_end="2026-08-10T10:30:00Z",
            meeting_type="standard_demo",
            notes="Testing fallback booking."
        )
        
        assert booking["status"] == "confirmed"
        assert booking["booking_id"].startswith("bk_")
        
        # Confirm slot status is updated to 'booked' in SQLite
        conn = db_manager.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT status FROM available_slots WHERE slot_id = 'slot_1'")
        row = cur.fetchone()
        assert row["status"] == "booked"
        
        # Confirm booking entry is saved in SQLite
        cur.execute("SELECT lead_id, meeting_type FROM bookings WHERE booking_id = ?", (booking["booking_id"],))
        b_row = cur.fetchone()
        assert b_row["lead_id"] == "lead_987"
        assert b_row["meeting_type"] == "standard_demo"
        conn.close()


@patch("googleapiclient.discovery.build")
def test_live_api_availability_generation(mock_build, db_manager):
    """Verifies live API event retrieval and business hours slot calculation."""
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    
    # Mock calendars().get() response (Timezone: UTC)
    mock_service.calendars().get().execute.return_value = {
        "id": "test_calendar_id",
        "timeZone": "UTC"
    }
    
    # Mock events().list().execute() response (one existing event in range)
    mock_service.events().list().execute.return_value = {
        "items": [
            {
                "id": "event_123",
                "summary": "Existing Meeting",
                "start": {"dateTime": "2026-08-10T10:00:00Z"},
                "end": {"dateTime": "2026-08-10T10:30:00Z"}
            }
        ]
    }
    
    with patch.dict("os.environ", {
        "GOOGLE_CALENDAR_CREDENTIALS_PATH": "dummy_path.json",
        "GOOGLE_CALENDAR_ID": "test_calendar_id"
    }), patch("os.path.exists", return_value=True), patch("google.oauth2.service_account.Credentials.from_service_account_file") as mock_creds:
        adapter = GoogleCalendarAdapter(db_manager)
        
        # Query availability for 9 AM to 11 AM (business hours)
        slots = adapter.get_calendar_availability(
            window_start="2026-08-10T09:00:00Z",
            window_end="2026-08-10T11:00:00Z",
            meeting_type="standard_demo"
        )
        
        # Candidate slots generated:
        # 1. 09:00 - 09:30 (Free)
        # 2. 09:30 - 10:00 (Free)
        # 3. 10:00 - 10:30 (Busy - existing event)
        # 4. 10:30 - 11:00 (Free)
        assert len(slots) == 3
        starts = [s["slot_start"] for s in slots]
        assert "2026-08-10T09:00:00+00:00" in starts
        assert "2026-08-10T09:30:00+00:00" in starts
        assert "2026-08-10T10:00:00+00:00" not in starts
        assert "2026-08-10T10:30:00+00:00" in starts
