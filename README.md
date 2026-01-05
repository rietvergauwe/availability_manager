# Availability Manager
This script automates availability management within a Google Calendar. It fetches external room bookings via web scraping and automatically creates 'Free4Booking' slots based on a configurable work schedule and current room occupancy.

## Features
- **Web Scraping:** Automatically retrieves room reservations from an external web portal.
- **Synchronization:** Checks for existing bookings in Google Calendar to prevent duplicate events.
- **Dynamic Availability:** Creates "Free4Booking" events only if
  - The specific room is unoccupied.
  - Sufficient staff memebers (based on the work schedule) are available during that slot.
- **JSON-based Configuration:** Manage your work schedule, google calendar credentials, google calendar token, google calendar id, and scrape url easily through external JSON files.
- **Automation:** Fully integrated with GitHub Actions for daily automated execution.

## Installation & Setup
**1. Google API Configuration**
     1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
     2. Create a project and enable the **Google Calendar API**.
     3. Create OAuth 2.0 credentials and download the 'credentials.json' file.
     4. Run the script locally for the first time to generate a 'token.json' via browser authentication.
**2. Local Configuration**
  Ensure the following files are present in the root directory (these are ignored by Git via '.gitignore'):
  - 'credentials.json': Your Google API client secret.
  - 'token.json': Your personal access token.
  - 'calendar_id.json': Contains the ID of the target calendar.
  - 'scrape_url.json': The source URL for web scraping.
  - 'work_schedule.json': The team's weekly schedule.
**3. Work Schedule**
  The work schedule follows this structure:
  '''
  {"work_schedule": {
    "MAANDAG": {
      "Voormiddag": {"exp1": ["Name1"], "exp2": ["Name2"]},
      "Namiddag":   {"exp1": ["Name1"], "exp2": ["Name2"]}
    },
    ...
   }
  } 
  '''

## Automation with GitHub Actions
This project is designed to run in the cloud. Sensitive information is securely stored using GitHub Secrets:
   - 'GOOGLE_CREDENTIALS': Content of 'credentials.json'
   - 'GOOGLE_TOKEN': Content of 'token.json'
   - 'GOOGLE_CALENDAR_ID': Content of 'calendar_id.json'
   - 'SCRAPE_URL': Content of 'scrape_url.json'
   - 'WORK_SCHEDULE': Content of 'work_schedule.json'
The workflow (located in '.github/workflows/main.yml') reconstructs the necessary JSON files from the secrets before executing the Python script.


