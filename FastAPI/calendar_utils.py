import os
import datetime
from typing import List, Optional, Tuple
import re
import dateparser
import pytz
import logging

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from database import save_booking, get_last_booking, cancel_booking, update_booking, list_bookings

# If modifying these SCOPES, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar']
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

class GoogleCalendarUtils:
    """
    Utility class for authenticating with Google Calendar, checking availability, and booking events.
    """
    def __init__(self):
        self.creds = None
        self.service = None
        self.authenticate()

    def authenticate(self):
        """
        Authenticate the user with Google OAuth2 and initialize the Calendar API service.
        """
        if os.path.exists(TOKEN_FILE):
            self.creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                self.creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, 'w') as token:
                token.write(self.creds.to_json())
        self.service = build('calendar', 'v3', credentials=self.creds)

    def get_free_busy(self, time_min: datetime.datetime, time_max: datetime.datetime, timezone: str = 'UTC') -> List[Tuple[datetime.datetime, datetime.datetime]]:
        """
        Query the user's Google Calendar for busy slots between time_min and time_max.
        Returns a list of (start, end) tuples for busy periods.
        """
        try:
            body = {
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "timeZone": timezone,
                "items": [{"id": 'primary'}]
            }
            eventsResult = self.service.freebusy().query(body=body).execute()
            busy_times = eventsResult['calendars']['primary'].get('busy', [])
            busy_periods = []
            for period in busy_times:
                start = datetime.datetime.fromisoformat(period['start'].replace('Z', '+00:00'))
                end = datetime.datetime.fromisoformat(period['end'].replace('Z', '+00:00'))
                busy_periods.append((start, end))
            return busy_periods
        except Exception as e:
            print(f"Error fetching free/busy: {e}")
            return []

    def find_available_slots(self, time_min: datetime.datetime, time_max: datetime.datetime, duration_minutes: int, timezone: str = 'UTC') -> List[Tuple[datetime.datetime, datetime.datetime]]:
        """
        Find available time slots of a given duration between time_min and time_max.
        Returns a list of (start, end) tuples for available periods.
        """
        busy_periods = self.get_free_busy(time_min, time_max, timezone)
        slots = []
        current = time_min
        busy_periods = sorted(busy_periods)
        for busy_start, busy_end in busy_periods:
            if (busy_start - current).total_seconds() >= duration_minutes * 60:
                slots.append((current, busy_start))
            current = max(current, busy_end)
        # Check for slot after last busy period
        if (time_max - current).total_seconds() >= duration_minutes * 60:
            slots.append((current, time_max))
        return slots

    def create_event(self, start: datetime.datetime, end: datetime.datetime, summary: str, description: Optional[str] = None, attendees: Optional[List[str]] = None, timezone: str = 'UTC') -> dict:
        """
        Create a new event in the user's Google Calendar.
        Returns the created event resource.
        """
        event = {
            'summary': summary,
            'description': description or '',
            'start': {
                'dateTime': start.isoformat(),
                'timeZone': timezone,
            },
            'end': {
                'dateTime': end.isoformat(),
                'timeZone': timezone,
            },
        }
        if attendees:
            event['attendees'] = [{'email': email} for email in attendees]
        try:
            created_event = self.service.events().insert(calendarId='primary', body=event).execute()
            return created_event
        except Exception as e:
            print(f"Error creating event: {e}")
            return {'error': str(e)}

    def check_availability(self, start: datetime.datetime, end: datetime.datetime) -> List[Tuple[datetime.datetime, datetime.datetime]]:
        """Return busy slots between start and end."""
        return self.get_free_busy(start, end)

    def book_event(self, summary: str, start: datetime.datetime, end: datetime.datetime) -> dict:
        """Book an event and return the event details."""
        event = {
            'summary': summary,
            'start': {
                'dateTime': start.isoformat(),
            },
            'end': {
                'dateTime': end.isoformat(),
            },
        }
        try:
            created_event = self.service.events().insert(calendarId='primary', body=event).execute()
            return created_event
        except Exception as e:
            print(f"Error creating event: {e}")
            return {'error': str(e)}

def extract_intent(user_msg):
    msg = user_msg.lower()
    if any(w in msg for w in ["cancel", "delete", "remove"]):
        return "cancel"
    if any(w in msg for w in ["edit", "reschedule", "move", "change"]):
        return "edit"
    if any(w in msg for w in ["book", "schedule", "set up", "add"]):
        return "book"
    if any(w in msg for w in ["list", "show", "what", "upcoming", "meetings"]):
        return "list"
    if any(w in msg for w in ["free", "available", "slots"]):
        return "check"
    if any(w in msg for w in ["help", "how"]):
        return "help"
    return "unknown"

def extract_slots(user_msg):
    # Use regex and dateparser for time, duration, summary, timezone
    # Return dict with all slots and ambiguity flag
    found = dateparser.search.search_dates(user_msg, settings={"RETURN_AS_TIMEZONE_AWARE": True, "DATE_ORDER": "DMY"})
    dt = None
    if found:
        dt = found[0][1]
        # If time is missing, try to extract it manually
        if dt.hour == 0 and dt.minute == 0:
            time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', user_msg, re.I)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                ampm = time_match.group(3).lower()
                if ampm == 'pm' and hour != 12:
                    hour += 12
                elif ampm == 'am' and hour == 12:
                    hour = 0
                dt = dt.replace(hour=hour, minute=minute)
    duration = 30
    if re.search(r"1 ?hour", user_msg, re.I):
        duration = 60
    elif re.search(r"(\d+)\s*min", user_msg, re.I):
        duration = int(re.search(r"(\d+)\s*min", user_msg, re.I).group(1))
    elif re.search(r"(\d+)\s*hour", user_msg, re.I):
        duration = int(re.search(r"(\d+)\s*hour", user_msg, re.I).group(1)) * 60
    summary = "Meeting"
    tz_match = re.search(r"([A-Za-z]+/[A-Za-z_]+)", user_msg)
    timezone = tz_match.group(1) if tz_match else "UTC"
    ambiguity = dt is None
    # Logging for debugging
    logging.info(f"Parsed datetime: {dt}, duration: {duration}, timezone: {timezone}, ambiguity: {ambiguity}")
    return {
        "datetime": dt.isoformat() if dt else None,
        "duration": duration,
        "summary": summary,
        "timezone": timezone,
        "ambiguity": ambiguity
    }

def handle_user_message(user_msg):
    intent = extract_intent(user_msg)
    slots = extract_slots(user_msg)
    # Use intent and slots to call DB functions and return response
    # Example: if intent == "cancel", call cancel_booking(...)
    # Return a dict with the result and a user-friendly message 