import os.path
import os
import json
import datetime
import re
import pytz
import requests
from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- Configuration ---

def load_config(env_name, file_name, key):
    # 1. Check whether the data exists in GitHub Secrets
    env_data = os.getenv(env_name)
    if env_data:
        return json.loads(env_data).get(key)
    
    # 2. Check whether the data is saved locally
    if os.path.exists(file_name):
        with open(file_name, 'r') as f:
            return json.load(f).get(key)
    return None

SCOPES        = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID   = load_config("GOOGLE_CALENDAR_ID", "calendar_id.json", "calendar_id")
URL           = load_config("SCRAPE_URL", "scrape_url.json", "url") # Configuration for adding events from web scraping
WORK_SCHEDULE = load_config("WORK_SCHEDULE", "work_schedule.json", "work_schedule")
TIMEZONE      = "Europe/Brussels"

# --- Days to check ---
DAYS_TO_CHECK = 20

# --- Web Scraping and Parsing Functions ---
def scrape_events_from_web(url):
    """
    Scrapes a webpage for event information and returns a list of events.
    """
    print(f"Fetching data from {url}...")
    try:
        data = requests.get(url).text
        soup = BeautifulSoup(data, 'html.parser')
        rows = soup.find('table').find_all('tr')
    except Exception as e:
        print(f"Error fetching or parsing webpage: {e}")
        return []

    events = []
    current_name = None
    tz = pytz.timezone(TIMEZONE)

    print("Parsing webpage content to find events...")
    for row in rows:
        b_tag = row.find('b')
        if b_tag:
            current_name = b_tag.get_text(strip=True)
        else:
            td_tag = row.find('td')
            if td_tag and current_name:
                booking_text = td_tag.get_text(strip=True)
                if 'lokaal FA1 (kooi van Faraday)' in booking_text:
                    match = re.search(r'(\d{2}/\d{2}/\d{4}) \[(\d{2}:\d{2})-(\d{2}:\d{2})\]', booking_text)
                    if match:
                        date_str, start_time_str, end_time_str = match.groups()

                        naive_start = datetime.datetime.strptime(f"{date_str} {start_time_str}", "%d/%m/%Y %H:%M")
                        naive_end = datetime.datetime.strptime(f"{date_str} {end_time_str}", "%d/%m/%Y %H:%M")

                        start_datetime = tz.localize(naive_start)
                        end_datetime = tz.localize(naive_end)
                        
                        events.append({
                            "summary": f"{current_name} (lokaal FA1)",
                            "start": {
                                "dateTime": start_datetime.isoformat(),
                                "timeZone": TIMEZONE,
                            },
                            "end": {
                                "dateTime": end_datetime.isoformat(),
                                "timeZone": TIMEZONE,
                            },
                        })
    return events
    
# --- Authentication Function ---
def authenticate_google_calendar():
    """
    Authenticates with the Google Calendar API.
    """
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)
    
# --- Event Management Functions for Free4Booking ---
def get_events_for_day(service, calendar_id, date_obj, timezone):
    """
    Fetches all events for a specific day from the given calendar.
    """
    local_tz = pytz.timezone(timezone)
    start_of_day = local_tz.localize(datetime.datetime(date_obj.year, date_obj.month, date_obj.day, 0, 0, 0))
    end_of_day = local_tz.localize(datetime.datetime(date_obj.year, date_obj.month, date_obj.day, 23, 59, 59))

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=start_of_day.isoformat(),
        timeMax=end_of_day.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    return events_result.get('items', [])

def parse_event_time(event_time_dict, timezone_str):
    """
    Parses event time dictionary into a localized datetime object.
    """
    local_tz = pytz.timezone(timezone_str)
    dt_str = event_time_dict.get('dateTime')
    if dt_str:
        dt_obj = datetime.datetime.fromisoformat(dt_str)
        if dt_obj.tzinfo is not None and dt_obj.tzinfo.utcoffset(dt_obj) is not None:
            return dt_obj.astimezone(local_tz)
        else:
            return local_tz.localize(dt_obj)
    date_str = event_time_dict.get('date')
    if date_str:
        return local_tz.localize(datetime.datetime.fromisoformat(date_str))
    return None

def check_person_availability(service, calendar_id, proposed_slot_start, proposed_slot_end, timezone, work_schedule, weekday_map):
    """
    Checks if at least one person for each required role (exp1, exp2) 
    from the work_schedule is available during the proposed slot.
    """
    
    # 1. Determine which staff are required for this slot
    day_of_week = proposed_slot_start.weekday()
    day_name    = weekday_map.get(day_of_week)
    
    if not day_name:
        print(f"    -> Skipping availability check: Not a mapped weekday ({day_of_week}).")
        return False # Not a workday in our map

    time_of_day = "Voormiddag" if proposed_slot_start.hour < 12 else "Namiddag"

    try:
        slot_schedule = work_schedule[day_name][time_of_day]
        required_exp1_names = slot_schedule["exp1"]
        required_exp2_names = slot_schedule["exp2"]
    except KeyError:
        print(f"    -> No schedule found for {day_name} {time_of_day}.")
        return False

    if not required_exp1_names or not required_exp2_names:
        print(f"    -> Slot {day_name} {time_of_day} is not fully staffed in the schedule.")
        return False # Slot is not staffed (e.g., Tuesday in your schedule)

    all_required_staff = set(name.lower() for name in required_exp1_names) | set(name.lower() for name in required_exp2_names)

    # 2. Get all events for the day
    day_start = proposed_slot_start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = proposed_slot_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])
    
    # 3. Find out which of the required staff are booked during the slot
    booked_staff_in_slot = set()
    for event in events:
        summary = event.get('summary', '').lower()
        event_start_dt = parse_event_time(event.get('start'), timezone)
        event_end_dt = parse_event_time(event.get('end'), timezone)

        # Skip events that are not confirmed or are Free4Booking
        if event.get('status') == 'cancelled' or 'free4booking' in summary:
            continue

        # Check for overlap with the proposed Free4Booking slot
        if max(event_start_dt, proposed_slot_start) < min(event_end_dt, proposed_slot_end):
            # This event overlaps. Check if any required staff are in the summary.
            for staff_name_lower in all_required_staff:
                if staff_name_lower in summary:
                    booked_staff_in_slot.add(staff_name_lower)
                    print(f"    -> {staff_name_lower.capitalize()} is booked. Event: '{event.get('summary')}'")

    # 4. Check if at least one person for each role is available
    
    # Check Role 1
    is_exp1_available = False
    for name in required_exp1_names:
        if name.lower() not in booked_staff_in_slot:
            is_exp1_available = True
            print(f"    -> Role 1 (Exp1) covered by: {name} (Available)")
            break
    
    if not is_exp1_available:
        print(f"    -> Role 1 (Exp1) NOT covered. Required: {required_exp1_names}, Booked: {booked_staff_in_slot}")

    # Check Role 2
    is_exp2_available = False
    for name in required_exp2_names:
        if name.lower() not in booked_staff_in_slot:
            is_exp2_available = True
            print(f"    -> Role 2 (Exp2) covered by: {name} (Available)")
            break

    if not is_exp2_available:
        print(f"    -> Role 2 (Exp2) NOT covered. Required: {required_exp2_names}, Booked: {booked_staff_in_slot}")

    # Both roles must be covered
    return is_exp1_available and is_exp2_available


def create_free4booking_event(service, calendar_id, start_time, end_time, timezone):
    """
    Creates a 'Free4Booking' event in the specified calendar.
    """
    event = {
        'summary': 'Free4Booking',
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': timezone,
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': timezone,
        },
        'transparency': 'transparent',
        'description': 'Automatisch aangemaakt Free4Booking event. Gecheckt op basis van werkschema.',
    }
    try:
        event = service.events().insert(calendarId=calendar_id, body=event).execute()
        print(f"    Event created: {event.get('htmlLink')}")
    except HttpError as error:
        print(f"    An error occurred while creating event: {error}")

def delete_free4booking_events_for_day(service, calendar_id, date_obj, timezone):
    """
    Deletes all 'Free4Booking' events for a specific day.
    """
    print(f"    Checking for existing 'Free4Booking' events to delete for {date_obj.strftime('%Y-%m-%d')}.")
    events_for_day = get_events_for_day(service, calendar_id, date_obj, timezone)
    deleted_count = 0
    for event in events_for_day:
        if 'free4booking' in event.get('summary', '').lower():
            try:
                service.events().delete(calendarId=calendar_id, eventId=event['id']).execute()
                print(f"    Deleted existing 'Free4Booking' event: {event.get('summary')} at {event.get('start', {}).get('dateTime')}")
                deleted_count += 1
            except HttpError as error:
                print(f"    An error occurred while deleting event {event.get('id')}: {error}")
    if deleted_count == 0:
        print("    No 'Free4Booking' events found to delete for this day.")

# --- Main Logic ---
def add_fa1_bookings_to_calendar(service):
    """
    Reads event data from a web page, checks for duplicates in Google Calendar,
    and creates any new events.
    """
    print("\n--- Running FA1 Bookings Import ---")
    
    events_to_create = scrape_events_from_web(URL)

    if not events_to_create:
        print("\nNo events to create after filtering.")
        print("\nFA1 Bookings Import finished.")
        return

    print(f"\nFound {len(events_to_create)} potential events from web scrape. Checking against Google Calendar...")

    try:
        min_time = min(e['start']['dateTime'] for e in events_to_create)
        max_time = max(e['end']['dateTime'] for e in events_to_create)

        print(f"Fetching existing events between {min_time} and {max_time}...")
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=min_time,
            timeMax=max_time,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        existing_events = events_result.get('items', [])

        existing_signatures = set()
        for event in existing_events:
            if 'dateTime' in event['start'] and 'dateTime' in event['end']:
                signature = (event['summary'], event['start']['dateTime'], event['end']['dateTime'])
                existing_signatures.add(signature)

        print(f"Found {len(existing_signatures)} existing events in this time range.")

        for event_data in events_to_create:
            event_signature = (
                event_data['summary'],
                event_data['start']['dateTime'],
                event_data['end']['dateTime']
            )

            if event_signature in existing_signatures:
                print(f"Skipping already existing event: {event_data['summary']} on {event_data['start']['dateTime']}")
            else:
                print(f"Creating event: {event_data['summary']} on {event_data['start']['dateTime']}")
                event = service.events().insert(calendarId=CALENDAR_ID, body=event_data).execute()
                print(f"    -> Event created: {event.get('htmlLink')}")
                existing_signatures.add(event_signature)

        print("\nFA1 Bookings Import finished.")

    except HttpError as error:
        print(f"An error occurred during FA1 bookings import: {error}")


def manage_free4booking_events(service):
    """
    Manages 'Free4Booking' events on the calendar based on existing events
    and the defined WORK_SCHEDULE. This function assumes FA1 events are already in the calendar.
    """
    print("\n--- Running Free4Booking Event Management ---")
    local_tz = pytz.timezone(TIMEZONE)
    
    # Map Python's weekday() (0=Mon, 1=Tue...) to the Dutch names in the schedule
    WEEKDAY_MAP_NL = {
        0: "MAANDAG",
        1: "DINSDAG",
        2: "WOENSDAG",
        3: "DONDERDAG",
        4: "VRIJDAG"
    }

    start_date = datetime.date.today()
    end_date   = start_date + datetime.timedelta(days=DAYS_TO_CHECK)
    
    current_date = start_date
    while current_date <= end_date:
        print(f"\nProcessing day: {current_date.strftime('%Y-%m-%d')}")

        if current_date.weekday() >= 5: # Saturday is 5, Sunday is 6
            print(f"    {current_date.strftime('%Y-%m-%d')} is a weekend. Skipping Free4Booking creation.")
            current_date += datetime.timedelta(days=1)
            continue
        
        # Always delete existing Free4Booking events first to prevent duplicates
        delete_free4booking_events_for_day(service, CALENDAR_ID, current_date, TIMEZONE)

        # Define the possible Free4Booking slots
        morning_slot_start = local_tz.localize(datetime.datetime(current_date.year, current_date.month, current_date.day, 9, 0, 0))
        morning_slot_end   = local_tz.localize(datetime.datetime(current_date.year, current_date.month, current_date.day, 12, 0, 0))
        
        afternoon_slot_start = local_tz.localize(datetime.datetime(current_date.year, current_date.month, current_date.day, 13, 0, 0))
        afternoon_slot_end   = local_tz.localize(datetime.datetime(current_date.year, current_date.month, current_date.day, 17, 0, 0))
        
        slots_to_check = [
            (morning_slot_start, morning_slot_end, "Morning"),
            (afternoon_slot_start, afternoon_slot_end, "Afternoon")
        ]
        
        for start_time, end_time, slot_name in slots_to_check:
            print(f"    Checking {slot_name} slot ({start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')})...")
            
            # Condition 1: Check if the FA1 room is available based on existing calendar events.
            events_for_day = get_events_for_day(service, CALENDAR_ID, current_date, TIMEZONE)
            is_fa1_booked = False
            for event in events_for_day:
                summary = event.get('summary', '').lower()
                if 'lokaal fa1' in summary and 'free4booking' not in summary:
                    event_start = parse_event_time(event.get('start'), TIMEZONE)
                    event_end = parse_event_time(event.get('end'), TIMEZONE)
                    if max(event_start, start_time) < min(event_end, end_time):
                        is_fa1_booked = True
                        print(f"        FA1 room is booked during this slot by event: '{event['summary']}'")
                        break
                        
            # Condition 2: Check persons availability based on the WORK_SCHEDULE
            can_create_event = check_person_availability(service, CALENDAR_ID, start_time, end_time, TIMEZONE, WORK_SCHEDULE, WEEKDAY_MAP_NL)

            if is_fa1_booked:
                print(f"    {slot_name} slot blocked: FA1 room is already booked.")
            elif not can_create_event:
                 print(f"    {slot_name} slot blocked: Required staff not available according to schedule.")
            else:
                print(f"    {slot_name} slot is free! Creating event.")
                create_free4booking_event(service, CALENDAR_ID, start_time, end_time, TIMEZONE)
                        
        current_date += datetime.timedelta(days=1)
    print("\nFree4Booking Event Management finished.")


def main():
    service = authenticate_google_calendar()
    if not service:
        return

    print("Successfully connected to Google Calendar API.")
    add_fa1_bookings_to_calendar(service)
    manage_free4booking_events(service)
    print("\nAll script operations completed.")

if __name__ == "__main__":
    main()
