from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import sys
import os
import base64
import hashlib
import hmac
import json
import time
from datetime import date, datetime, timezone, timedelta
sys.path.append(os.path.dirname(__file__))

def get_ist_today_str() -> str:
    """Returns current date in Indian Standard Time (IST) as YYYY-MM-DD."""
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return ist_now.strftime("%Y-%m-%d")

from agent import process_agent_query
from database import (
    init_db,
    get_all_workshops,
    get_all_notifications,
    get_all_students,
    register_student_db,
    add_workshop,
    update_workshop_seats,
    update_workshop,
    delete_workshop,
    add_notification,
    delete_notification,
    get_club_stats,
    get_workshop_by_id,
    update_student_access,
    student_login,
    get_student_dashboard_data,
    get_workshop_resources,
    add_workshop_resource,
    delete_workshop_resource,
    get_workshop_attendance,
    save_workshop_attendance,
    get_workshop_attendance_dates,
    has_attendance_for_date,
    is_student_present_for_date,
    get_workshop_tests,
    save_test,
    toggle_test_live,
    toggle_test_publish,
    delete_test,
    submit_test_answers,
    get_student_test_submissions,
    get_test_submission_detail,
    delete_test_submission,
    get_all_team_members,
    add_team_member,
    delete_team_member,
    end_workshop,
    submit_workshop_feedback,
    get_workshop_feedbacks,
    get_student_feedbacks,
    update_workshop_feedback_prompt,
    get_all_blogs,
    get_blog_by_id,
    add_blog,
    delete_blog,
    verify_admin_credentials,
    get_workshop_credentials,
    update_workshop_credentials,
    get_test_leaderboard,
    get_workshop_overall_leaderboard,
    get_student_workshop_id,
    get_resource_workshop_id,
    get_test_workshop_id,
    is_student_registered_for_workshop,
    add_subscriber,
    get_all_subscribers,
    get_all_gallery_media,
    add_gallery_media,
    delete_gallery_media,
)
from test_generator import generate_ai_test_questions

app = FastAPI(
    title="Web Development Club (WDC) AI Agent API",
    description="FastAPI & PostgreSQL Backend for WDC AI Concierge & Admin Panel with Full CRUD"
)

@app.on_event("startup")
def startup_event():
    try:
        init_db()
        print("[OK] Database initialized & connected successfully on startup.")
    except Exception as e:
        print(f"[WARN] Startup DB warning: {e}")

from fastapi.middleware.gzip import GZipMiddleware

# CORS middleware setup for React frontend (Vercel + localhost dev)
_cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
_frontend_url = os.getenv("FRONTEND_URL", "").strip()
if _frontend_url:
    _cors_origins.append(_frontend_url.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _frontend_url else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gzip Compression Middleware for 5x faster network transfers
app.add_middleware(GZipMiddleware, minimum_size=500)

def _token_secret():
    return os.getenv("ADMIN_SESSION_SECRET") or os.getenv("ADMIN_PASSWORD", "wdcadmin2026")

def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

def create_admin_token(session: dict) -> str:
    payload = {
        "role": session["role"],
        "workshop_id": session.get("workshop_id", ""),
        "workshop_title": session.get("workshop_title", ""),
        "iat": int(time.time()),
    }
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_token_secret().encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"wdc_admin_token_{payload_b64}.{sig}"

def read_admin_session(authorization: Optional[str] = None):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "", 1).strip()
    if not token.startswith("wdc_admin_token_"):
        return None
    try:
        body = token.replace("wdc_admin_token_", "", 1)
        payload_b64, sig = body.split(".", 1)
        expected = hmac.new(_token_secret().encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
        if int(time.time()) - int(payload.get("iat", 0)) > 60 * 60 * 12:
            return None
        return payload
    except Exception:
        return None

def require_admin(authorization: Optional[str] = Header(None)):
    session = read_admin_session(authorization)
    if not session:
        raise HTTPException(status_code=401, detail="Admin authentication required")
    return session

def require_super_admin(authorization: Optional[str] = Header(None)):
    session = require_admin(authorization)
    if session.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return session

def assert_workshop_access(session: dict, workshop_id: str):
    if session.get("role") == "super_admin":
        return
    if session.get("role") == "workshop_admin" and session.get("workshop_id") == workshop_id:
        return
    raise HTTPException(status_code=403, detail="This workshop is outside your admin scope")

# --- UPTIMEROBOT & RENDER HEALTH CHECK ENDPOINTS ---
@app.api_route("/health", methods=["GET", "HEAD", "OPTIONS", "POST"])
@app.api_route("/api/health", methods=["GET", "HEAD", "OPTIONS", "POST"])
def health_check():
    """Ultra-fast health check endpoint for UptimeRobot, Render, and Keep-Alive pings."""
    return {"status": "ok", "service": "WDC Portal API", "online": True}

# --- Pydantic Models ---

class AgentChatRequest(BaseModel):
    user_name: str = "Guest"
    query: str
    current_context: str = "main_menu"

class StudentRegistrationRequest(BaseModel):
    name: str
    email: str
    phone: str = ""
    workshop_id: str = "ws-101"

class AddWorkshopRequest(BaseModel):
    title: str
    mentor: str = "WDC Lead Mentor"
    date: str
    time: str = "18:00 IST"
    seats: int = 50
    topics: str = ""  # Comma-separated string OR list
    color: str = "#00f2fe"
    admin_username: Optional[str] = ""
    admin_password: Optional[str] = ""

class UpdateWorkshopSeatsRequest(BaseModel):
    seats: int

class UpdateWorkshopRequest(BaseModel):
    title: Optional[str] = None
    mentor: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    seats: Optional[int] = None
    topics: Optional[str] = None
    color: Optional[str] = None

class AddNotificationRequest(BaseModel):
    title: str
    category: str = "Announcement"
    related_workshop_id: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None

class StudentAccessUpdateRequest(BaseModel):
    allowed: int = 1
    password: Optional[str] = ""

class UserLoginRequest(BaseModel):
    email: str
    password: str

class AddResourceRequest(BaseModel):
    title: str
    resource_type: str = "Notes"
    link_url: Optional[str] = ""
    description: Optional[str] = ""

class AttendanceSaveRequest(BaseModel):
    date: str
    records: List[dict]

class AITestGenerateRequest(BaseModel):
    topic: str
    num_questions: int = 5
    level: str = "Intermediate"
    question_type: str = "single_correct"

class SaveTestRequest(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = ""
    level: Optional[str] = "Intermediate"
    type: Optional[str] = "single_correct"
    duration_mins: Optional[int] = 15
    total_questions: Optional[int] = 5
    questions: List[dict]
    status: Optional[str] = "Draft"
    is_live: Optional[int] = 0

class ToggleLiveRequest(BaseModel):
    is_live: int

class TogglePublishRequest(BaseModel):
    status: str

class TestSubmitRequest(BaseModel):
    student_email: str
    student_name: Optional[str] = "Student"
    answers: dict
    time_taken_seconds: Optional[int] = 0

# --- Routes ---

@app.get("/")
def read_root():
    dist_index = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dist", "index.html")
    if os.path.exists(dist_index):
        return FileResponse(dist_index)
    return {
        "status": "online",
        "club": "Web Development Club (WDC)",
        "database": "Neon PostgreSQL",
        "version": "3.0"
    }

@app.post("/api/agent/chat")
async def chat_with_agent(req: AgentChatRequest):
    """AI Concierge Chat — reads all live DB data to answer user queries."""
    result = process_agent_query(user_name=req.user_name, query=req.query)
    return result

# --- WORKSHOPS CRUD ---

@app.get("/api/workshops")
def fetch_workshops():
    """Returns all workshops from SQLite DB (live, always fresh)."""
    return get_all_workshops()

@app.get("/api/workshops/{workshop_id}")
def fetch_workshop_by_id(workshop_id: str):
    """Returns a single workshop by ID."""
    ws = get_workshop_by_id(workshop_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workshop not found")
    return ws

@app.post("/api/workshops")
def create_workshop(req: AddWorkshopRequest, authorization: Optional[str] = Header(None)):
    """Creates a new workshop in SQLite DB. Admin panel uses this."""
    require_super_admin(authorization)
    if not req.title or not req.date:
        raise HTTPException(status_code=400, detail="Title and date are required")
    
    result = add_workshop(
        title=req.title,
        mentor=req.mentor,
        date=req.date,
        time=req.time,
        seats=req.seats,
        topics=req.topics,
        color=req.color,
        admin_username=req.admin_username or "",
        admin_password=req.admin_password or "",
    )
    return result

@app.patch("/api/workshops/{workshop_id}/seats")
def change_workshop_seats(workshop_id: str, req: UpdateWorkshopSeatsRequest, authorization: Optional[str] = Header(None)):
    """Allows Super Admin or Scoped Workshop Admin to increase/update workshop seat capacity."""
    session = require_admin(authorization)
    assert_workshop_access(session, workshop_id)
    if req.seats < 0:
        raise HTTPException(status_code=400, detail="Seats must be a positive number")
    updated = update_workshop_seats(workshop_id, req.seats)
    if not updated:
        raise HTTPException(status_code=404, detail="Workshop not found")
    return updated

@app.put("/api/workshops/{workshop_id}")
def edit_workshop(workshop_id: str, req: UpdateWorkshopRequest, authorization: Optional[str] = Header(None)):
    """Allows Super Admin to update workshop details including seat capacity."""
    require_super_admin(authorization)
    updated = update_workshop(
        workshop_id=workshop_id,
        title=req.title,
        mentor=req.mentor,
        date=req.date,
        time=req.time,
        seats=req.seats,
        topics=req.topics,
        color=req.color
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Workshop not found")
    return updated

@app.delete("/api/workshops/{workshop_id}")
def remove_workshop(workshop_id: str, authorization: Optional[str] = Header(None)):
    """Deletes a workshop by ID from SQLite DB."""
    require_super_admin(authorization)
    success = delete_workshop(workshop_id)
    if not success:
        raise HTTPException(status_code=404, detail="Workshop not found")
    return {"success": True, "message": f"Workshop {workshop_id} deleted from DB"}

# --- NOTIFICATIONS CRUD ---

@app.get("/api/notifications")
def fetch_notifications():
    """Returns all notifications from SQLite DB (live, always fresh)."""
    return get_all_notifications()

@app.post("/api/notifications")
def create_notification(req: AddNotificationRequest, authorization: Optional[str] = Header(None)):
    """Creates a new notification in SQLite DB. Admin panel uses this."""
    require_super_admin(authorization)
    if not req.title:
        raise HTTPException(status_code=400, detail="Title is required")
    
    notif_id = add_notification(
        title=req.title,
        category=req.category,
        related_workshop_id=req.related_workshop_id,
        date=req.date,
        time=req.time,
    )
    return {"success": True, "id": notif_id, "message": "Notification broadcast sent!"}

@app.delete("/api/notifications/{notif_id}")
def remove_notification(notif_id: str, authorization: Optional[str] = Header(None)):
    """Deletes a notification by ID from SQLite DB."""
    require_super_admin(authorization)
    success = delete_notification(notif_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"success": True, "message": f"Notification {notif_id} deleted from DB"}

# --- STUDENTS CRUD ---

@app.get("/api/students")
def fetch_students(workshop_id: str = "all", authorization: Optional[str] = Header(None)):
    """Returns all enrolled students from SQLite DB."""
    session = require_admin(authorization)
    if session.get("role") == "workshop_admin":
        workshop_id = session.get("workshop_id", "")
    return get_all_students(workshop_id)

@app.post("/api/students/register")
def register_student(req: StudentRegistrationRequest):
    """Registers a student for a workshop in SQLite DB."""
    result = register_student_db(req.name, req.email, req.phone, req.workshop_id)
    if not result:
        raise HTTPException(status_code=400, detail="Workshop not found or registration failed")
    return result


class SubscribeRequest(BaseModel):
    email: str

class AddGalleryMediaRequest(BaseModel):
    title: str
    url: str
    media_type: str = "video"
    category: str = "Highlight"

# --- STATS ---

@app.get("/api/stats")
def fetch_stats():
    """Returns aggregate WDC club stats from DB."""
    return get_club_stats()

@app.post("/api/subscribe")
def subscribe_email_route(req: SubscribeRequest):
    """Subscribes an email address to receive upcoming workshop & notification alerts."""
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    return add_subscriber(req.email)

@app.get("/api/subscribers")
def fetch_subscribers(authorization: Optional[str] = Header(None)):
    """Returns all email subscribers from DB."""
    require_super_admin(authorization)
    return get_all_subscribers()

# --- GALLERY MEDIA ---

@app.get("/api/gallery")
def fetch_gallery_media():
    """Returns all gallery media items from DB."""
    return get_all_gallery_media()

@app.post("/api/gallery")
def create_gallery_media(req: AddGalleryMediaRequest, authorization: Optional[str] = Header(None)):
    """Adds a new gallery media item (video or image URL) to DB."""
    require_super_admin(authorization)
    if not req.url or not req.title:
        raise HTTPException(status_code=400, detail="Title and URL are required")
    return add_gallery_media(req.title, req.url, req.media_type, req.category)

@app.delete("/api/gallery/{media_id}")
def remove_gallery_media(media_id: str, authorization: Optional[str] = Header(None)):
    """Deletes a gallery media item by ID."""
    require_super_admin(authorization)
    return delete_gallery_media(media_id)

# --- STUDENT ACCESS & WORKSHOP RESOURCES ROUTES ---

@app.put("/api/students/{student_id}/access")
def update_access(student_id: str, req: StudentAccessUpdateRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to allow/disallow workshop access and set student password."""
    session = require_admin(authorization)
    student_workshop_id = get_student_workshop_id(student_id)
    if not student_workshop_id:
        raise HTTPException(status_code=404, detail="Student registration not found")
    assert_workshop_access(session, student_workshop_id)
    result = update_student_access(student_id, req.allowed, req.password or "")
    if not result:
        raise HTTPException(status_code=404, detail="Student registration not found")
    return result

@app.post("/api/user/login")
def login_user(req: UserLoginRequest):
    """Student login endpoint using email and admin-assigned password."""
    result = student_login(req.email, req.password)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid email or password. Please contact Admin if access is not yet allowed.")
    return result

@app.get("/api/user/dashboard")
def fetch_user_dashboard(email: str):
    """Returns student's registered workshops and allowed workshop resources."""
    if not email:
        raise HTTPException(status_code=400, detail="Email query parameter is required")
    return get_student_dashboard_data(email)

@app.get("/api/workshops/{workshop_id}/resources")
def fetch_workshop_resources(workshop_id: str, authorization: Optional[str] = Header(None)):
    """Returns all resources (notes, tests, code) for a workshop."""
    assert_workshop_access(require_admin(authorization), workshop_id)
    return get_workshop_resources(workshop_id)

@app.post("/api/workshops/{workshop_id}/resources")
def create_workshop_resource(workshop_id: str, req: AddResourceRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to add a resource (Notes, Test, Assignment, Video, Code) to a workshop."""
    assert_workshop_access(require_admin(authorization), workshop_id)
    if not req.title:
        raise HTTPException(status_code=400, detail="Resource title is required")
    return add_workshop_resource(
        workshop_id=workshop_id,
        title=req.title,
        resource_type=req.resource_type,
        link_url=req.link_url or "",
        description=req.description or ""
    )

@app.delete("/api/workshops/resources/{resource_id}")
def remove_workshop_resource(resource_id: str, authorization: Optional[str] = Header(None)):
    """Admin endpoint to delete a workshop resource."""
    workshop_id = get_resource_workshop_id(resource_id)
    if not workshop_id:
        raise HTTPException(status_code=404, detail="Resource not found")
    assert_workshop_access(require_admin(authorization), workshop_id)
    return delete_workshop_resource(resource_id)

# --- DAILY ATTENDANCE SYSTEM ENDPOINTS ---

@app.get("/api/workshops/{workshop_id}/attendance")
def fetch_attendance(workshop_id: str, date: str, authorization: Optional[str] = Header(None)):
    """Admin endpoint to fetch daily student attendance for a workshop on a date."""
    assert_workshop_access(require_admin(authorization), workshop_id)
    if not date:
        raise HTTPException(status_code=400, detail="Date parameter required (YYYY-MM-DD)")
    return get_workshop_attendance(workshop_id, date)

@app.get("/api/workshops/{workshop_id}/attendance-dates")
def fetch_attendance_dates(workshop_id: str, authorization: Optional[str] = Header(None)):
    """Admin endpoint to fetch list of past dates on which attendance was recorded."""
    assert_workshop_access(require_admin(authorization), workshop_id)
    return get_workshop_attendance_dates(workshop_id)

@app.post("/api/workshops/{workshop_id}/attendance")
def save_attendance(workshop_id: str, req: AttendanceSaveRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to save student attendance for a workshop on a date."""
    assert_workshop_access(require_admin(authorization), workshop_id)
    return save_workshop_attendance(workshop_id, req.date, req.records)

# --- AI TEST MAKER ASSISTANT & ONLINE TEST ENDPOINTS ---

@app.post("/api/ai/generate-test")
def generate_test_ai(req: AITestGenerateRequest, authorization: Optional[str] = Header(None)):
    """
    AI Assistant Endpoint: Uses LangChain PromptTemplate to generate
    structured test questions with 4 options and marked correct answer keys.
    """
    require_admin(authorization)
    questions = generate_ai_test_questions(
        topic=req.topic,
        num_questions=req.num_questions,
        level=req.level,
        question_type=req.question_type
    )
    return {
        "topic": req.topic,
        "num_questions": len(questions),
        "level": req.level,
        "question_type": req.question_type,
        "questions": questions
    }

@app.get("/api/workshops/{workshop_id}/tests")
def fetch_workshop_tests(workshop_id: str, for_student: bool = False, authorization: Optional[str] = Header(None)):
    """Fetches tests for a workshop."""
    if not for_student:
        assert_workshop_access(require_admin(authorization), workshop_id)
    return get_workshop_tests(workshop_id, for_student=for_student)

@app.post("/api/workshops/{workshop_id}/tests")
def create_or_save_test(workshop_id: str, req: SaveTestRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to save/publish a test with questions and answer keys."""
    assert_workshop_access(require_admin(authorization), workshop_id)
    if not req.title:
        raise HTTPException(status_code=400, detail="Test title is required")
    return save_test(
        workshop_id=workshop_id,
        title=req.title,
        description=req.description or "",
        level=req.level or "Intermediate",
        type=req.type or "single_correct",
        duration_mins=req.duration_mins or 15,
        total_questions=req.total_questions or len(req.questions),
        questions=req.questions,
        status=req.status or "Draft",
        is_live=req.is_live or 0,
        test_id=req.id
    )

@app.put("/api/tests/{test_id}/toggle-live")
def set_test_live(test_id: str, req: ToggleLiveRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to toggle 'Start Test (Go Live)' switch."""
    workshop_id = get_test_workshop_id(test_id)
    if not workshop_id:
        raise HTTPException(status_code=404, detail="Test not found")
    assert_workshop_access(require_admin(authorization), workshop_id)
    today_ist = get_ist_today_str()
    today_server = date.today().isoformat()
    if req.is_live == 1 and not (has_attendance_for_date(workshop_id, today_ist) or has_attendance_for_date(workshop_id, today_server)):
        raise HTTPException(status_code=400, detail="Please take and save today's attendance before making the test live.")
    res = toggle_test_live(test_id, req.is_live)
    if not res:
        raise HTTPException(status_code=404, detail="Test not found")
    return res

@app.put("/api/tests/{test_id}/toggle-publish")
def set_test_publish(test_id: str, req: TogglePublishRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to publish or draft a test."""
    workshop_id = get_test_workshop_id(test_id)
    if not workshop_id:
        raise HTTPException(status_code=404, detail="Test not found")
    assert_workshop_access(require_admin(authorization), workshop_id)
    res = toggle_test_publish(test_id, req.status)
    if not res:
        raise HTTPException(status_code=404, detail="Test not found")
    return res

@app.delete("/api/tests/{test_id}")
def remove_test(test_id: str, authorization: Optional[str] = Header(None)):
    """Admin endpoint to delete a test."""
    workshop_id = get_test_workshop_id(test_id)
    if not workshop_id:
        raise HTTPException(status_code=404, detail="Test not found")
    assert_workshop_access(require_admin(authorization), workshop_id)
    return delete_test(test_id)

@app.post("/api/tests/{test_id}/submit")
def submit_test(test_id: str, req: TestSubmitRequest):
    """
    Student endpoint to submit test answers.
    Evaluates answers automatically against correct key and returns score percentage and instant live rank.
    """
    workshop_id = get_test_workshop_id(test_id)
    if not workshop_id or not is_student_registered_for_workshop(req.student_email, workshop_id):
        raise HTTPException(status_code=403, detail="Student is not enrolled or approval is pending for this workshop.")
    today_ist = get_ist_today_str()
    today_server = date.today().isoformat()
    is_present = is_student_present_for_date(req.student_email, workshop_id, today_ist) or is_student_present_for_date(req.student_email, workshop_id, today_server)
    if not is_present:
        raise HTTPException(status_code=403, detail="Aaj ki attendance me Present students hi live test de sakte hain. Agar aap present hain to mentor se attendance check karwayen.")
    res = submit_test_answers(test_id, req.student_email, req.student_name or "Student", req.answers, req.time_taken_seconds)
    if not res:
        raise HTTPException(status_code=404, detail="Test not found or evaluation failed")
    return res

@app.get("/api/user/test-submissions")
def fetch_student_submissions(email: str):
    """Returns student test submission history."""
    return get_student_test_submissions(email)

@app.get("/api/tests/{test_id}/submissions/{student_email}")
def fetch_test_submission_detail(test_id: str, student_email: str, authorization: Optional[str] = Header(None)):
    """Admin-only answer review for one student in a workshop-scoped test."""
    workshop_id = get_test_workshop_id(test_id)
    if not workshop_id:
        raise HTTPException(status_code=404, detail="Test not found")
    assert_workshop_access(require_admin(authorization), workshop_id)
    detail = get_test_submission_detail(test_id, student_email)
    if not detail:
        raise HTTPException(status_code=404, detail="Submission not found")
    return detail

@app.delete("/api/tests/{test_id}/submissions/{student_email}")
def remove_test_submission(test_id: str, student_email: str, authorization: Optional[str] = Header(None)):
    """Admin-only endpoint to remove an unauthorized/cheating submission from leaderboard."""
    workshop_id = get_test_workshop_id(test_id)
    if not workshop_id:
        raise HTTPException(status_code=404, detail="Test not found")
    assert_workshop_access(require_admin(authorization), workshop_id)
    res = delete_test_submission(test_id, student_email)
    if not res.get("success"):
        raise HTTPException(status_code=404, detail="Submission not found or already removed")
    return res

class TeamMemberRequest(BaseModel):
    name: str
    role: str
    image_url: str
    linkedin_url: Optional[str] = ""

@app.get("/api/team")
def fetch_team_members():
    """Returns list of WDC core team members for 3D Taas Playing Cards Deck."""
    return get_all_team_members()

@app.post("/api/team")
def create_team_member(req: TeamMemberRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to add a new core team member."""
    require_super_admin(authorization)
    if not req.name or not req.role or not req.image_url:
        raise HTTPException(status_code=400, detail="Name, role, and image_url are required")
    return add_team_member(req.name, req.role, req.image_url, req.linkedin_url or "")

@app.delete("/api/team/{tm_id}")
def remove_team_member(tm_id: str, authorization: Optional[str] = Header(None)):
    """Admin endpoint to delete a team member."""
    require_super_admin(authorization)
    return delete_team_member(tm_id)

class EndWorkshopRequest(BaseModel):
    group_photo_url: str
    feedback_prompt: Optional[str] = ""

class WorkshopFeedbackRequest(BaseModel):
    student_email: str
    student_name: Optional[str] = "Student"
    rating: Optional[int] = 5
    feedback_text: str
    suggestions: Optional[str] = ""

class FeedbackPromptRequest(BaseModel):
    feedback_prompt: str = ""

@app.post("/api/workshops/{workshop_id}/end")
def end_workshop_endpoint(workshop_id: str, req: EndWorkshopRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to end/complete a workshop, save group photo, and publish feedback form."""
    assert_workshop_access(require_admin(authorization), workshop_id)
    return end_workshop(workshop_id, req.group_photo_url, req.feedback_prompt or "")

@app.put("/api/workshops/{workshop_id}/feedback-form")
def update_feedback_form(workshop_id: str, req: FeedbackPromptRequest, authorization: Optional[str] = Header(None)):
    assert_workshop_access(require_admin(authorization), workshop_id)
    return update_workshop_feedback_prompt(workshop_id, req.feedback_prompt)

@app.post("/api/workshops/{workshop_id}/feedback")
def submit_feedback_endpoint(workshop_id: str, req: WorkshopFeedbackRequest):
    """Student endpoint to submit feedback & suggestions for an ended workshop."""
    if not req.student_email or not req.feedback_text:
        raise HTTPException(status_code=400, detail="Student email and feedback text are required")
    return submit_workshop_feedback(
        workshop_id=workshop_id,
        student_email=req.student_email,
        student_name=req.student_name or "Student",
        rating=req.rating or 5,
        feedback_text=req.feedback_text,
        suggestions=req.suggestions or ""
    )

@app.get("/api/workshops/{workshop_id}/feedbacks")
def fetch_workshop_feedbacks(workshop_id: str, authorization: Optional[str] = Header(None)):
    """Returns all submitted student feedbacks for a workshop."""
    assert_workshop_access(require_admin(authorization), workshop_id)
    return get_workshop_feedbacks(workshop_id)

@app.get("/api/student/submitted-feedbacks")
def fetch_student_submitted_feedbacks(email: str):
    """Returns list of workshop IDs for which student has submitted feedback."""
    return get_student_feedbacks(email)

# --- ADMIN AUTHENTICATION ENDPOINTS ---

class AdminLoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/admin/login")
def admin_login_endpoint(req: AdminLoginRequest):
    """Secure Backend Admin Authentication with Role-Based Access Control."""
    result = verify_admin_credentials(req.username.strip(), req.password.strip())
    
    if result:
        token = create_admin_token(result)
        return {
            "authenticated": True,
            "token": token,
            "role": result["role"],
            "workshop_id": result["workshop_id"],
            "workshop_title": result["workshop_title"],
            "message": "Admin Login Successful!"
        }
    else:
        raise HTTPException(status_code=401, detail="Invalid Admin User ID or Password!")

# --- BLOGS ENDPOINTS ---

class AddBlogRequest(BaseModel):
    title: str
    content: str
    excerpt: Optional[str] = ""
    author: Optional[str] = "WDC RECB Editorial"
    category: Optional[str] = "Tech & Web"
    read_time: Optional[str] = "5 min read"

@app.get("/api/blogs")
def fetch_all_blogs():
    """Returns all published blog posts for WDC Portal and SEO indexing."""
    return get_all_blogs()

@app.get("/api/blogs/{blog_id}")
def fetch_blog_details(blog_id: str):
    """Returns specific blog post details and increments view count."""
    blog = get_blog_by_id(blog_id)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog post not found")
    return blog

@app.post("/api/blogs")
def create_blog_post(req: AddBlogRequest, authorization: Optional[str] = Header(None)):
    """Admin endpoint to create a new blog post globally."""
    require_super_admin(authorization)
    if not req.title.strip() or not req.content.strip():
        raise HTTPException(status_code=400, detail="Title and content are required")
    return add_blog(
        title=req.title,
        content=req.content,
        excerpt=req.excerpt or "",
        author=req.author or "WDC RECB Editorial",
        category=req.category or "Tech & Web",
        read_time=req.read_time or "5 min read"
    )

@app.delete("/api/blogs/{blog_id}")
def remove_blog_post(blog_id: str, authorization: Optional[str] = Header(None)):
    """Admin endpoint to delete a blog post globally."""
    require_super_admin(authorization)
    return delete_blog(blog_id)

# --- WORKSHOP ADMIN CREDENTIAL MANAGEMENT ---

class UpdateCredentialsRequest(BaseModel):
    admin_username: str = ""
    admin_password: str = ""

@app.put("/api/workshops/{workshop_id}/credentials")
def update_ws_credentials(workshop_id: str, req: UpdateCredentialsRequest, authorization: Optional[str] = Header(None)):
    """Super Admin endpoint to update/reset workshop admin credentials."""
    require_super_admin(authorization)
    return update_workshop_credentials(workshop_id, req.admin_username, req.admin_password)

@app.get("/api/workshops/{workshop_id}/credentials")
def fetch_ws_credentials(workshop_id: str, authorization: Optional[str] = Header(None)):
    """Super Admin endpoint for viewing non-sensitive workshop credential metadata."""
    require_super_admin(authorization)
    credentials = get_workshop_credentials(workshop_id)
    if not credentials:
        raise HTTPException(status_code=404, detail="Workshop not found")
    return credentials

# --- TEST LEADERBOARD ENDPOINTS ---

@app.get("/api/tests/{test_id}/leaderboard")
def fetch_test_leaderboard(test_id: str, email: str = None, authorization: Optional[str] = Header(None)):
    """Returns ranked leaderboard for a test. Optionally includes student's rank."""
    data = get_test_leaderboard(test_id, student_email=email)
    workshop_id = get_test_workshop_id(test_id)
    if not workshop_id:
        raise HTTPException(status_code=404, detail="Test not found")
    session = read_admin_session(authorization)
    if session and workshop_id:
        assert_workshop_access(session, workshop_id)
        return data

    if email and not is_student_registered_for_workshop(email, workshop_id):
        raise HTTPException(status_code=403, detail="Student is not enrolled for this workshop")

    def public_entry(entry):
        if not entry:
            return None
        return {
            "rank": entry["rank"],
            "student_name": entry["student_name"],
            "score": entry["score"],
            "max_score": entry["max_score"],
            "percentage": entry["percentage"],
            "submitted_at": entry["submitted_at"],
        }

    return {
        "test_id": data["test_id"],
        "total_submissions": data["total_submissions"],
        "leaderboard": [],
        "top_5": [public_entry(item) for item in data["top_5"]],
        "student_rank": public_entry(data["student_rank"]),
    }

@app.get("/api/workshops/{workshop_id}/leaderboard")
def fetch_workshop_leaderboard(workshop_id: str, email: str = None, authorization: Optional[str] = Header(None)):
    """Returns the normalized leaderboard across every published quiz in a workshop."""
    data = get_workshop_overall_leaderboard(workshop_id, student_email=email)
    session = read_admin_session(authorization)
    if session:
        assert_workshop_access(session, workshop_id)
        return data

    if email and not is_student_registered_for_workshop(email, workshop_id):
        raise HTTPException(status_code=403, detail="Student is not enrolled for this workshop")

    def public_entry(entry):
        if not entry:
            return None
        return {key: entry[key] for key in (
            "rank", "student_name", "average_percentage", "attempted_quizzes",
            "total_quizzes", "participation_rate", "not_attempted_quizzes",
        )}

    return {
        "workshop_id": data["workshop_id"],
        "total_quizzes": data["total_quizzes"],
        "total_enrolled": data["total_enrolled"],
        "ranked_students": data["ranked_students"],
        "unranked_students": data["unranked_students"],
        "normalization": data["normalization"],
        "leaderboard": [],
        "top_5": [public_entry(item) for item in data["top_5"]],
        "student_rank": public_entry(data["student_rank"]),
    }
# --- SINGLE SERVICE FULL-STACK DEPLOYMENT (Serve React Frontend Dist) ---
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

dist_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dist")
if os.path.exists(dist_dir):
    assets_dir = os.path.join(dist_dir, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD", "OPTIONS"])
    def serve_react_app(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        target_file = os.path.join(dist_dir, full_path)
        if os.path.exists(target_file) and os.path.isfile(target_file):
            return FileResponse(target_file)
        return FileResponse(os.path.join(dist_dir, "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
