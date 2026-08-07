import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Load env variables from .env
load_dotenv()

def verify_write_access():
    print("="*60)
    print("GOOGLE CALENDAR WRITE ACCESS TEST")
    print("="*60)

    credentials_path = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_PATH")
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")

    if not credentials_path or not calendar_id:
        print("FAIL: Environment configuration missing.")
        return

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as e:
        print(f"FAIL: Google client libraries missing: {e}")
        return

    # Set read/write scope
    scopes = ['https://www.googleapis.com/auth/calendar']
    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, 
            scopes=scopes
        )
        service = build('calendar', 'v3', credentials=creds)
    except Exception as e:
        print(f"FAIL: Authentication initialization failed: {e}")
        return

    # Step 1: Create an Event
    print("\n[Step 1] Attempting to create a test event (events.insert)...")
    
    start_time = datetime.now(timezone.utc) + timedelta(days=365) # 1 year in the future
    end_time = start_time + timedelta(minutes=5)
    
    event_body = {
        'summary': 'EchoSphere adapter write-test — safe to delete',
        'description': 'Automated connectivity test checking service account write permissions.',
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'UTC',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'UTC',
        }
    }

    event_id = None
    try:
        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        event_id = event.get('id')
        print(f"PASS: Event successfully created!")
        print(f"  Event ID: {event_id}")
        print(f"  Summary: {event.get('summary')}")
        print(f"  Start: {event.get('start', {}).get('dateTime')}")
    except HttpError as e:
        print("FAIL: Event creation failed.")
        print(f"  HTTP Status Code: {e.resp.status}")
        print(f"  API Response Details: {e.content.decode('utf-8')}")
        if e.resp.status == 403:
            print("  Diagnosis: 403 Forbidden. The calendar's sharing permissions for this service account email need to be upgraded from 'See all event details' to 'Make changes to events'.")
        return
    except Exception as e:
        print(f"FAIL: Unexpected error during event creation: {e}")
        return

    # Step 2: Delete the Event
    print("\n[Step 2] Attempting to delete the test event (events.delete)...")
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        print("PASS: Event successfully deleted and cleaned up!")
    except HttpError as e:
        print("FAIL: Event deletion failed.")
        print(f"  HTTP Status Code: {e.resp.status}")
        print(f"  API Response Details: {e.content.decode('utf-8')}")
        return
    except Exception as e:
        print(f"FAIL: Unexpected error during event deletion: {e}")
        return

    print("\n" + "="*60)
    print("WRITE AND DELETE CHECKS PASSED SUCCESSFULLY!")
    print("="*60)

if __name__ == "__main__":
    verify_write_access()
