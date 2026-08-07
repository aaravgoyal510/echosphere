import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Load env variables from .env
load_dotenv()

def verify_connectivity():
    print("="*60)
    print("GOOGLE CALENDAR CONNECTIVITY SMOKE TEST")
    print("="*60)

    credentials_path = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_PATH")
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")

    # Step 1: Check Env Vars
    print("\n[Step 1] Verifying Environment Configuration...")
    if not credentials_path:
        print("FAIL: GOOGLE_CALENDAR_CREDENTIALS_PATH environment variable is not set.")
        return
    if not calendar_id:
        print("FAIL: GOOGLE_CALENDAR_ID environment variable is not set.")
        return
    
    print(f"  Credentials Path: {credentials_path}")
    print(f"  Calendar ID: {calendar_id}")
    print("PASS: Environment configuration present.")

    # Step 2: Check Credentials File Exists
    print("\n[Step 2] Verifying Credentials JSON file existence...")
    if not os.path.exists(credentials_path):
        print(f"FAIL: Key file not found at path: '{credentials_path}'")
        return
    print("PASS: Credentials JSON file exists locally.")

    # Step 3: Authenticate and Initialize Client
    print("\n[Step 3] Authenticating Service Account...")
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as e:
        print(f"FAIL: Google client libraries missing: {e}")
        print("Ensure 'google-auth' and 'google-api-python-client' are installed in the virtual environment.")
        return

    scopes = ['https://www.googleapis.com/auth/calendar.readonly']
    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, 
            scopes=scopes
        )
        service = build('calendar', 'v3', credentials=creds)
        print("PASS: Service Account authenticated successfully.")
    except Exception as e:
        print(f"FAIL: Service account initialization failed: {e}")
        return

    # Step 4: Fetch Calendar Metadata (Read Check)
    print("\n[Step 4] Retrieving Calendar Metadata (calendars.get)...")
    try:
        calendar_metadata = service.calendars().get(calendarId=calendar_id).execute()
        summary = calendar_metadata.get('summary', 'No summary')
        time_zone = calendar_metadata.get('timeZone', 'No timezone')
        print(f"PASS: Metadata retrieved successfully!")
        print(f"  Summary: {summary}")
        print(f"  Timezone: {time_zone}")
    except HttpError as e:
        print(f"FAIL: Failed to fetch calendar metadata.")
        print(f"  HTTP Status Code: {e.resp.status}")
        print(f"  API Response Details: {e.content.decode('utf-8')}")
        if e.resp.status == 404:
            print("  Diagnosis: Calendar ID not found. Double-check GOOGLE_CALENDAR_ID in .env.")
        elif e.resp.status == 403:
            print("  Diagnosis: Access Forbidden. Ensure the calendar is shared with the service account email.")
        return
    except Exception as e:
        print(f"FAIL: Unexpected error during metadata retrieval: {e}")
        return

    # Step 5: Query Free-Busy / Event List (Range Check)
    print("\n[Step 5] Checking Event List for a small range (events.list)...")
    try:
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=2)).isoformat()
        
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            maxResults=5
        ).execute()
        
        events = events_result.get('items', [])
        print(f"PASS: Event range list query succeeded!")
        print(f"  Found {len(events)} events in the next 48 hours.")
        for idx, event in enumerate(events):
            start = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
            print(f"    {idx+1}. Start: {start} | Summary: {event.get('summary', '(No Title)')}")
    except HttpError as e:
        print(f"FAIL: Failed to perform range query.")
        print(f"  HTTP Status Code: {e.resp.status}")
        print(f"  API Response Details: {e.content.decode('utf-8')}")
        return
    except Exception as e:
        print(f"FAIL: Unexpected error during range query: {e}")
        return

    print("\n" + "="*60)
    print("ALL CONNECTIVITY CHECKS PASSED SUCCESSFULLY!")
    print("The service account has valid credentials and read access to the calendar.")
    print("="*60)

if __name__ == "__main__":
    verify_connectivity()
