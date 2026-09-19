import json
from langchain_core.tools import tool
from database import get_all_notifications, get_workshop_by_id, get_all_workshops

@tool
def get_notifications_tool() -> str:
    """Tool to fetch latest WDC announcements and notifications from SQLite database."""
    notifications = get_all_notifications()
    return json.dumps(notifications)

@tool
def get_workshop_details_tool(workshop_id: str = "ws-101") -> str:
    """Tool to fetch workshop schedule and seat details from SQLite database."""
    workshop = get_workshop_by_id(workshop_id) or get_workshop_by_id("ws-101")
    return json.dumps(workshop)

@tool
def open_workshop_form_tool(workshop_id: str = "ws-101") -> str:
    """Tool to open the registration form for a specific workshop ID from SQLite database."""
    workshop = get_workshop_by_id(workshop_id) or get_workshop_by_id("ws-101")
    return json.dumps({
        "action": "open_form",
        "target_workshop": workshop
    })
