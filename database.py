import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import secrets

def get_ist_now():
    """Returns current datetime strictly in Indian Standard Time (IST - UTC+5:30)."""
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def get_ist_now_str(fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Returns formatted IST timestamp string."""
    return get_ist_now().strftime(fmt)
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables from backend/.env
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

DEFAULT_DB_URL = "postgresql://neondb_owner:npg_ogwPDO36SpiI@ep-polished-pine-azh6fcsb.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
DB_URL = os.getenv("DATABASE_URL") or DEFAULT_DB_URL

# Thread-safe Connection Pool (Min 2, Max 35 connections)
_db_pool = None
_pool_lock = threading.Lock()

# Thread-safe High-Performance In-Memory Leaderboard Cache
_LEADERBOARD_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 30.0  # 30 seconds TTL with instant cache invalidation on any test/submission change

def invalidate_leaderboard_cache(test_id: str = None, workshop_id: str = None):
    """Instantly invalidates in-memory leaderboard cache so new submissions reflect in real time."""
    with _CACHE_LOCK:
        if test_id:
            _LEADERBOARD_CACHE.pop(f"test_{test_id}", None)
        if workshop_id:
            _LEADERBOARD_CACHE.pop(f"workshop_{workshop_id}", None)
        if not test_id and not workshop_id:
            _LEADERBOARD_CACHE.clear()

def _get_pool():
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                db_url = os.getenv("DATABASE_URL") or DB_URL or DEFAULT_DB_URL
                _db_pool = pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=35,
                    dsn=db_url,
                    cursor_factory=RealDictCursor,
                    connect_timeout=10
                )
    return _db_pool

class PooledConnection:
    """Thread-safe proxy for pooled PostgreSQL connections that returns connection to pool on close()."""
    def __init__(self, p, conn):
        self._pool = p
        self._conn = conn
        self._closed = False

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        if not self._closed and self._pool and self._conn:
            self._closed = True
            try:
                if not self._conn.closed:
                    self._conn.rollback()
                self._pool.putconn(self._conn)
            except Exception:
                try:
                    self._pool.putconn(self._conn, close=True)
                except Exception:
                    pass
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

def get_connection():
    """Returns a pooled PostgreSQL database connection with automatic reconnection and fallback."""
    try:
        p = _get_pool()
        raw_conn = p.getconn()
        if raw_conn.closed:
            p.putconn(raw_conn, close=True)
            raw_conn = p.getconn()
        return PooledConnection(p, raw_conn)
    except Exception:
        # Fallback to direct connection if pool is temporarily exhausted
        db_url = os.getenv("DATABASE_URL") or DB_URL or DEFAULT_DB_URL
        return psycopg2.connect(db_url, cursor_factory=RealDictCursor, connect_timeout=10)

def hash_password(password: str) -> str:
    """Hashes workshop admin passwords without adding a new dependency."""
    if not password:
        return ""
    iterations = 120000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    if not password or not stored:
        return False
    if not stored.startswith("pbkdf2_sha256$"):
        return hmac.compare_digest(password, stored)
    try:
        _, iterations, salt, digest = stored.split("$", 3)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
        return hmac.compare_digest(candidate.hex(), digest)
    except Exception:
        return False

def redact_workshop_credentials(workshop: dict) -> dict:
    safe = dict(workshop)
    safe.pop("admin_password", None)
    safe.pop("admin_username", None)
    return safe

def init_db():
    """Initializes PostgreSQL database tables and seeds default WDC data if empty."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Workshops Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS workshops (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        mentor TEXT,
        date TEXT,
        time TEXT,
        seats INTEGER,
        enrolled INTEGER DEFAULT 0,
        topics TEXT,
        status TEXT DEFAULT 'Active',
        color TEXT DEFAULT '#00f2fe',
        is_ended INTEGER DEFAULT 0,
        group_photo_url TEXT DEFAULT '',
        feedback_prompt TEXT DEFAULT ''
    );
    """)

    cursor.execute("ALTER TABLE workshops ADD COLUMN IF NOT EXISTS is_ended INTEGER DEFAULT 0;")
    cursor.execute("ALTER TABLE workshops ADD COLUMN IF NOT EXISTS group_photo_url TEXT DEFAULT '';")
    cursor.execute("ALTER TABLE workshops ADD COLUMN IF NOT EXISTS feedback_prompt TEXT DEFAULT '';")
    cursor.execute("ALTER TABLE workshops ADD COLUMN IF NOT EXISTS feedback_questions_json TEXT DEFAULT '[]';")
    cursor.execute("ALTER TABLE workshops ADD COLUMN IF NOT EXISTS admin_username TEXT DEFAULT '';")
    cursor.execute("ALTER TABLE workshops ADD COLUMN IF NOT EXISTS admin_password TEXT DEFAULT '';")

    # 2. Notifications Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        category TEXT,
        related_workshop_id TEXT,
        date TEXT,
        time TEXT,
        views INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1
    );
    """)

    cursor.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS category TEXT;")
    cursor.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS related_workshop_id TEXT;")
    cursor.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS date TEXT;")
    cursor.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS time TEXT;")
    cursor.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0;")
    cursor.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS active INTEGER DEFAULT 1;")

    # 3. Enrolled Students Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT,
        workshop_id TEXT,
        workshop_title TEXT,
        date TEXT,
        status TEXT DEFAULT 'Confirmed',
        allowed INTEGER DEFAULT 0,
        password TEXT DEFAULT ''
    );
    """)

    cursor.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS allowed INTEGER DEFAULT 0;")
    cursor.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS password TEXT DEFAULT '';")

    # 4. Workshop Resources Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS workshop_resources (
        id TEXT PRIMARY KEY,
        workshop_id TEXT NOT NULL,
        title TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        link_url TEXT,
        description TEXT,
        date_added TEXT
    );
    """)

    # 5. Workshop Daily Attendance Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS workshop_attendance (
        id TEXT PRIMARY KEY,
        workshop_id TEXT NOT NULL,
        student_id TEXT NOT NULL,
        student_name TEXT,
        date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Present',
        updated_at TEXT
    );
    """)

    # 6. Workshop Tests Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tests (
        id TEXT PRIMARY KEY,
        workshop_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        level TEXT DEFAULT 'Intermediate',
        type TEXT DEFAULT 'single_correct',
        duration_mins INTEGER DEFAULT 15,
        total_questions INTEGER DEFAULT 5,
        questions_json TEXT NOT NULL,
        status TEXT DEFAULT 'Draft',
        is_live INTEGER DEFAULT 0,
        created_at TEXT
    );
    """)

    # 7. Student Test Submissions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS test_submissions (
        id TEXT PRIMARY KEY,
        test_id TEXT NOT NULL,
        workshop_id TEXT NOT NULL,
        student_email TEXT NOT NULL,
        student_name TEXT,
        answers_json TEXT,
        score INTEGER,
        max_score INTEGER,
        percentage REAL,
        submitted_at TEXT
    );
    """)
    cursor.execute("ALTER TABLE test_submissions ADD COLUMN IF NOT EXISTS time_taken_seconds INTEGER;")

    # 8. Gallery Media Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gallery_media (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        media_type TEXT DEFAULT 'video',
        category TEXT DEFAULT 'Highlight',
        date TEXT
    );
    """)

    # 9. Core Team Members Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS team_members (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        image_url TEXT NOT NULL,
        linkedin_url TEXT,
        created_at TEXT
    );
    """)

    # 10. Workshop Feedbacks Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS workshop_feedbacks (
        id TEXT PRIMARY KEY,
        workshop_id TEXT,
        student_email TEXT,
        student_name TEXT,
        rating INTEGER DEFAULT 5,
        feedback_text TEXT,
        suggestions TEXT,
        submitted_at TEXT
    );
    """)
    cursor.execute("ALTER TABLE workshop_feedbacks ADD COLUMN IF NOT EXISTS answers_json TEXT DEFAULT '{}';")

    # 11. Subscribers Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subscribers (
        email TEXT PRIMARY KEY,
        date TEXT
    );
    """)

    # 12. Blogs Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS blogs (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        slug TEXT NOT NULL,
        content TEXT NOT NULL,
        excerpt TEXT,
        author TEXT DEFAULT 'WDC RECB Editorial',
        category TEXT DEFAULT 'Tech & Web',
        read_time TEXT DEFAULT '5 min read',
        created_at TEXT,
        views INTEGER DEFAULT 0
    );
    """)

    # Seed default team members if empty
    cursor.execute("SELECT COUNT(*) as count FROM team_members")
    if cursor.fetchone()['count'] == 0:
        default_team = [
            ("tm-1", "Aditya Sharma", "Lead Developer & President", "/wdc_logo.png", "https://www.linkedin.com/company/web-dev-club-recb/posts/?feedView=all"),
            ("tm-2", "Priya Singh", "AI & ML Specialist", "/wdc_logo.png", "https://www.linkedin.com/company/web-dev-club-recb/posts/?feedView=all"),
            ("tm-3", "Rohan Verma", "Full-Stack Web Lead", "/wdc_logo.png", "https://www.linkedin.com/company/web-dev-club-recb/posts/?feedView=all"),
            ("tm-4", "Ananya Gupta", "Data Science & Cloud Lead", "/wdc_logo.png", "https://www.linkedin.com/company/web-dev-club-recb/posts/?feedView=all"),
            ("tm-5", "Vikram Patel", "Competitive Coding Lead", "/wdc_logo.png", "https://www.linkedin.com/company/web-dev-club-recb/posts/?feedView=all")
        ]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        for item in default_team:
            cursor.execute("""
            INSERT INTO team_members (id, name, role, image_url, linkedin_url, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """, (item[0], item[1], item[2], item[3], item[4], now_str))

    # Seed Default Gallery Media if empty
    cursor.execute("SELECT COUNT(*) as count FROM gallery_media")
    if cursor.fetchone()['count'] == 0:
        date_today = datetime.now().strftime("%Y-%m-%d")
        default_gallery = [
            ('media-101', 'WDC 3D AI Concierge Autonomous Agent', '/avatar_video.mp4', 'video', 'AI Tech Showcase', date_today),
            ('media-102', 'Full-Stack Web & AI Development Masterclass', 'https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=1200&q=80', 'image', 'Workshop Feature', date_today),
            ('media-103', 'Smart India Hackathon Victory & RECB Team', 'https://images.unsplash.com/photo-1531482615713-2afd69097998?auto=format&fit=crop&w=1200&q=80', 'image', 'Achievement', date_today),
        ]
        for item in default_gallery:
            cursor.execute("""
            INSERT INTO gallery_media (id, title, url, media_type, category, date)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """, item)

    # 13. High-Performance Composite Indexes for 100+ Concurrent Students
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_submissions_test_id ON test_submissions(test_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_submissions_ws ON test_submissions(workshop_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_submissions_email ON test_submissions(LOWER(student_email));")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_submissions_composite ON test_submissions(test_id, LOWER(student_email));")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_students_ws_allowed ON students(workshop_id, allowed);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_students_email_ws ON students(LOWER(email), workshop_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_attendance_ws_date ON workshop_attendance(workshop_id, date);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tests_ws_status ON tests(workshop_id, status);")
    except Exception as e:
        print(f"Index creation note: {e}")

    conn.commit()
    conn.close()

# Initial database setup
try:
    init_db()
except Exception as e:
    print(f"Database init note: {e}")


# --- DATABASE CRUD FUNCTIONS ---

def get_all_workshops():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM workshops ORDER BY date ASC")
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for r in rows:
        item = dict(r)
        item['topics'] = json.loads(item['topics']) if item.get('topics') else []
        if item.get('feedback_questions_json'):
            try:
                item['feedback_questions'] = json.loads(item['feedback_questions_json'])
            except Exception:
                item['feedback_questions'] = []
        else:
            item['feedback_questions'] = []
        result.append(redact_workshop_credentials(item))
    return result

def get_workshop_by_id(workshop_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM workshops WHERE id = %s", (workshop_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        item = dict(row)
        item['topics'] = json.loads(item['topics']) if item.get('topics') else []
        if item.get('feedback_questions_json'):
            try:
                item['feedback_questions'] = json.loads(item['feedback_questions_json'])
            except Exception:
                item['feedback_questions'] = []
        else:
            item['feedback_questions'] = []
        return redact_workshop_credentials(item)
    return None

def add_workshop(title, mentor, date, time, seats, topics, color='#00f2fe', admin_username='', admin_password=''):
    """Adds a new workshop to PostgreSQL DB with optional scoped admin credentials."""
    import time as time_module
    ws_id = f"ws-{int(time_module.time() * 1000) % 100000}"
    topics_json = json.dumps(topics if isinstance(topics, list) else [t.strip() for t in topics.split(',')])
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO workshops (id, title, mentor, date, time, seats, enrolled, topics, status, color, admin_username, admin_password)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (ws_id, title, mentor or 'WDC Lead Mentor', date, time or '18:00 IST', 
          int(seats) if seats else 50, 0, topics_json, 'Active', color,
          admin_username.strip() if admin_username else '',
          hash_password(admin_password.strip()) if admin_password else ''))
    conn.commit()
    conn.close()
    return get_workshop_by_id(ws_id)

def update_workshop_seats(workshop_id: str, seats: int):
    """Updates total seat capacity for a workshop in PostgreSQL DB."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE workshops SET seats = %s WHERE id = %s", (int(seats), workshop_id))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if updated:
        return get_workshop_by_id(workshop_id)
    return None

def update_workshop(workshop_id: str, title: str = None, mentor: str = None, date: str = None, time: str = None, seats: int = None, topics = None, color: str = None):
    """Updates workshop details including seat capacity in PostgreSQL DB."""
    current = get_workshop_by_id(workshop_id)
    if not current:
        return None
    
    new_title = title if title is not None else current['title']
    new_mentor = mentor if mentor is not None else current['mentor']
    new_date = date if date is not None else current['date']
    new_time = time if time is not None else current['time']
    new_seats = int(seats) if seats is not None else current['seats']
    new_color = color if color is not None else current.get('color', '#00f2fe')
    
    if topics is not None:
        new_topics = json.dumps(topics if isinstance(topics, list) else [t.strip() for t in str(topics).split(',')])
    else:
        new_topics = json.dumps(current.get('topics', []))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE workshops 
    SET title = %s, mentor = %s, date = %s, time = %s, seats = %s, topics = %s, color = %s
    WHERE id = %s
    """, (new_title, new_mentor, new_date, new_time, new_seats, new_topics, new_color, workshop_id))
    conn.commit()
    conn.close()
    return get_workshop_by_id(workshop_id)

def delete_workshop(workshop_id):
    """Deletes a workshop from PostgreSQL DB."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM workshops WHERE id = %s", (workshop_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

def get_all_notifications():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notifications ORDER BY date DESC, id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_notification(title, category='Announcement', related_workshop_id=None, date=None, time=None):
    """Adds a new notification to PostgreSQL DB."""
    import time as time_module
    notif_id = f"notif-{int(time_module.time() * 1000) % 1000000}"
    date_str = date if date else datetime.now().strftime("%Y-%m-%d")
    time_str = time if time else datetime.now().strftime("%H:%M IST")
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO notifications (id, title, category, related_workshop_id, date, time, views, active)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (notif_id, title, category, related_workshop_id, date_str, time_str, 0, 1))
    # 12. Blogs Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS blogs (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        slug TEXT NOT NULL,
        content TEXT NOT NULL,
        excerpt TEXT,
        author TEXT DEFAULT 'WDC RECB Editorial',
        category TEXT DEFAULT 'Tech & Web',
        read_time TEXT DEFAULT '5 min read',
        created_at TEXT,
        views INTEGER DEFAULT 0
    );
    """)

    # Seed Default SEO Blogs if empty
    cursor.execute("SELECT COUNT(*) as count FROM blogs")
    if cursor.fetchone()['count'] == 0:
        date_today = datetime.now().strftime("%Y-%m-%d")
        default_blogs = [
            (
                "blog-1",
                "Web Development Club (WDC) RECB: Empowering Tech Innovators at Rajkiya Engineering College Banda",
                "welcome-to-wdc-recb-rajkiya-engineering-college-banda",
                "Web Development Club (WDC) at Rajkiya Engineering College Banda (REC Banda / RECB) is the premier student tech community dedicated to mastering modern software engineering, artificial intelligence, cloud architecture, and competitive coding.\n\nOur mission is to bridge the gap between classroom theory and real-world industrial software practices. Through hands-on workshops, 100% client-side WDC Code Lab compilers, 3D interactive concept arcades, and AI Concierge guidance, WDC RECB empowers engineering students to build production-grade web applications, excel in national hackathons like Smart India Hackathon (SIH), and secure high-paying software engineering placements.\n\nKey Highlights of WDC RECB:\n- Free access to multi-language compilers (C, C++, Python, JavaScript, HTML/CSS).\n- Weekly hands-on workshops on React, FastAPI, Machine Learning & DevOps.\n- Peer-to-peer mentorship and collaborative open-source project development.",
                "Explore how the Web Development Club (WDC) at Rajkiya Engineering College Banda (RECB) fosters coding excellence, AI innovation, full-stack web engineering, and student hackathons.",
                "WDC RECB Editorial",
                "Campus & Club",
                "4 min read",
                date_today,
                142
            ),
            (
                "blog-2",
                "Mastering C, C++, Python & Web Development with WDC Code Lab",
                "mastering-c-cpp-python-web-development-wdc-code-lab",
                "In today's fast-paced tech environment, having an instant, zero-setup coding environment is vital for computer science and engineering students. WDC Code Lab is an advanced 100% client-side compiler and interpreter suite built directly into the Web Development Club portal.\n\nPowered by WebAssembly (Pyodide for Python), AST JS compilers (JSCPP for C/C++), and sandboxed execution frames, WDC Code Lab offers instant execution without requiring expensive server hardware or database connections.\n\nSupported Technologies:\n1. C & C++ Programming: Standard I/O, pointers, array operations, and algorithmic logic.\n2. Python 3.12: Full WebAssembly Python execution for data structures and math.\n3. JavaScript & Web: V8 engine sandboxing for modern ES6+ JS.\n4. HTML & CSS Studio: Real-time live web preview with support for Inline CSS, Internal style tags, and external CSS stylesheets.",
                "Discover how WDC Code Lab enables students at Rajkiya Engineering College Banda to practice C, C++, Python, JS, and HTML/CSS instantly in their browser with zero server latency.",
                "Lead Technical Instructor",
                "Web & Compilers",
                "5 min read",
                date_today,
                98
            ),
            (
                "blog-3",
                "How Student Clubs at RECB Build Career-Ready Engineers for Tech Leaders",
                "how-recb-student-clubs-build-career-ready-engineers",
                "Practical software development experience is the most important asset for engineering undergraduates. At Rajkiya Engineering College Banda (REC Banda), the Web Development Club (WDC) plays a central role in preparing students for technical interviews, DSA rounds, and system design evaluations.\n\nBy taking part in WDC RECB hackathons, building real-world full-stack web applications, and practicing on the WDC portal, students gain confidence in Git, APIs, database design (PostgreSQL/SQLite), and cloud deployment.",
                "A deep dive into how active participation in Rajkiya Engineering College Banda's tech societies prepares students for top tier software developer interviews.",
                "WDC Alumni & Placement Cell",
                "Career & Placements",
                "6 min read",
                date_today,
                76
            )
        ]
        for b in default_blogs:
            cursor.execute("""
            INSERT INTO blogs (id, title, slug, content, excerpt, author, category, read_time, created_at, views)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """, b)

    conn.commit()
    conn.close()
    print("Database initialization complete.")

def delete_notification(notif_id):
    """Deletes a notification from PostgreSQL DB."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notifications WHERE id = %s", (notif_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

def get_all_students(workshop_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    if workshop_id and workshop_id != 'all':
        cursor.execute("SELECT * FROM students WHERE workshop_id = %s ORDER BY date DESC", (workshop_id,))
    else:
        cursor.execute("SELECT * FROM students ORDER BY date DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def register_student_db(name, email, phone, workshop_id="ws-101"):
    workshop = get_workshop_by_id(workshop_id) or get_workshop_by_id("ws-101")
    if not workshop:
        return None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, email, status, allowed FROM students WHERE LOWER(email) = LOWER(%s) AND workshop_id = %s LIMIT 1",
        (email.strip(), workshop['id']),
    )
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return {
            "id": existing['id'],
            "name": existing['name'],
            "email": existing['email'],
            "workshop_title": workshop['title'],
            "status": "Allowed" if existing['allowed'] == 1 else "Pending",
            "allowed": existing['allowed'],
            "already_registered": True,
        }

    import time as time_module
    ticket_id = f"WDC-2026-{int(time_module.time() * 1000) % 900000 + 100000}"
    date_str = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
    INSERT INTO students (id, name, email, phone, workshop_id, workshop_title, date, status)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (ticket_id, name, email, phone, workshop['id'], workshop['title'], date_str, 'Pending'))
    
    # Increment enrolled count in workshops
    cursor.execute("UPDATE workshops SET enrolled = enrolled + 1 WHERE id = %s", (workshop['id'],))
    conn.commit()
    conn.close()
    
    return {
        "id": ticket_id,
        "name": name,
        "email": email,
        "workshop_title": workshop['title'],
        "date": date_str,
        "status": "Pending",
        "allowed": 0,
        "already_registered": False,
    }

def get_all_subscribers():
    """Returns all email subscribers from DB."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM subscribers ORDER BY date DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_club_stats():
    """Returns aggregate stats for the club agent to use."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as count FROM workshops WHERE status='Active'")
    total_workshops = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM students")
    total_students = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM notifications WHERE active=1")
    active_notifications = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM subscribers")
    total_subscribers = cursor.fetchone()['count']
    
    cursor.execute("SELECT SUM(enrolled) as total_enrolled, SUM(seats) as total_seats FROM workshops")
    row = cursor.fetchone()
    total_enrolled = row['total_enrolled'] or 0
    total_seats = row['total_seats'] or 0
    
    conn.close()
    return {
        "total_workshops": total_workshops,
        "total_students": total_students,
        "active_notifications": active_notifications,
        "total_subscribers": total_subscribers,
        "total_enrolled": total_enrolled,
        "total_seats": total_seats,
        "occupancy_pct": round((total_enrolled / total_seats * 100) if total_seats > 0 else 0, 1)
    }

def add_subscriber(email: str):
    """Adds a subscriber email to PostgreSQL DB for upcoming workshop & notification alerts."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO subscribers (email, date) VALUES (%s, %s)
    ON CONFLICT (email) DO UPDATE SET date = EXCLUDED.date
    """, (email, date_str))
    conn.commit()
    conn.close()
    return {"success": True, "email": email, "message": "Email subscribed successfully!"}

def get_all_gallery_media():
    """Returns all gallery media items from DB."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM gallery_media ORDER BY date DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_gallery_media(title: str, url: str, media_type: str = 'video', category: str = 'Highlight'):
    """Adds a new gallery media item to DB."""
    media_id = f"media-{int(datetime.now().timestamp())}"
    date_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO gallery_media (id, title, url, media_type, category, date)
    VALUES (%s, %s, %s, %s, %s, %s)
    """, (media_id, title, url, media_type, category, date_str))
    conn.commit()
    conn.close()
    return {
        "id": media_id,
        "title": title,
        "url": url,
        "media_type": media_type,
        "category": category,
        "date": date_str
    }

def delete_gallery_media(media_id: str):
    """Deletes a gallery media item by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM gallery_media WHERE id = %s", (media_id,))
    conn.commit()
    conn.close()
    return {"success": True, "id": media_id}

# --- STUDENT ACCESS & WORKSHOP RESOURCES CRUD ---

def update_student_access(student_id: str, allowed: int, password: str = ""):
    """Updates student permission status (allowed = 1/0) and assigns login password."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE students
    SET allowed = %s, password = CASE WHEN %s <> '' THEN %s ELSE password END, status = CASE WHEN %s = 1 THEN 'Allowed' ELSE 'Pending' END
    WHERE id = %s
    """, (allowed, password, password, allowed, student_id))
    updated = cursor.rowcount > 0
    conn.commit()
    
    # Return updated student
    cursor.execute("SELECT * FROM students WHERE id = %s", (student_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def student_login(email: str, password: str):
    """Authenticates student using email and admin-assigned password."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM students
    WHERE LOWER(email) = LOWER(%s) AND password = %s
    """, (email.strip(), password.strip()))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return None
    
    students_list = [dict(r) for r in rows]
    user_name = students_list[0]['name']
    
    # Fetch all allowed workshops for this student's email
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT s.*, w.title as full_workshop_title, w.mentor, w.date as workshop_date, w.time as workshop_time, w.topics, w.color,
           w.status as workshop_status, w.is_ended, w.group_photo_url, w.feedback_prompt, w.feedback_questions_json
    FROM students s
    LEFT JOIN workshops w ON s.workshop_id = w.id
    WHERE LOWER(s.email) = LOWER(%s)
    """, (email.strip(),))
    registrations = [dict(r) for r in cursor.fetchall()]
    
    # For each allowed registration, fetch workshop resources
    for reg in registrations:
        if reg.get('topics'):
            try:
                reg['topics'] = json.loads(reg['topics'])
            except Exception:
                pass
        if reg.get('feedback_questions_json'):
            try:
                reg['feedback_questions'] = json.loads(reg['feedback_questions_json'])
            except Exception:
                reg['feedback_questions'] = []
        else:
            reg['feedback_questions'] = []
            
        # Normalize is_ended
        is_ended_raw = reg.get('is_ended')
        ws_status_raw = str(reg.get('workshop_status') or reg.get('status') or '').strip().lower()
        if is_ended_raw in (1, True, '1', 'true') or ws_status_raw == 'completed':
            reg['is_ended'] = 1
        else:
            reg['is_ended'] = 0

        if reg.get('allowed') == 1:
            cursor.execute("SELECT * FROM workshop_resources WHERE workshop_id = %s ORDER BY date_added DESC", (reg['workshop_id'],))
            reg['resources'] = [dict(r) for r in cursor.fetchall()]
        else:
            reg['resources'] = []
            
    conn.close()
    return {
        "authenticated": True,
        "name": user_name,
        "email": email.strip(),
        "registrations": registrations
    }

def get_student_dashboard_data(email: str):
    """Fetches all registrations and allowed workshop resources for a student."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT s.*, w.title as full_workshop_title, w.mentor, w.date as workshop_date, w.time as workshop_time, w.topics, w.color,
           w.status as workshop_status, w.is_ended, w.group_photo_url, w.feedback_prompt, w.feedback_questions_json
    FROM students s
    LEFT JOIN workshops w ON s.workshop_id = w.id
    WHERE LOWER(s.email) = LOWER(%s)
    """, (email.strip(),))
    registrations = [dict(r) for r in cursor.fetchall()]
    
    for reg in registrations:
        if reg.get('topics'):
            try:
                reg['topics'] = json.loads(reg['topics'])
            except Exception:
                pass
        if reg.get('feedback_questions_json'):
            try:
                reg['feedback_questions'] = json.loads(reg['feedback_questions_json'])
            except Exception:
                reg['feedback_questions'] = []
        else:
            reg['feedback_questions'] = []

        # Normalize is_ended
        is_ended_raw = reg.get('is_ended')
        ws_status_raw = str(reg.get('workshop_status') or reg.get('status') or '').strip().lower()
        if is_ended_raw in (1, True, '1', 'true') or ws_status_raw == 'completed':
            reg['is_ended'] = 1
        else:
            reg['is_ended'] = 0

        if reg.get('allowed') == 1:
            cursor.execute("SELECT * FROM workshop_resources WHERE workshop_id = %s ORDER BY date_added DESC", (reg['workshop_id'],))
            reg['resources'] = [dict(r) for r in cursor.fetchall()]
        else:
            reg['resources'] = []
            
    conn.close()
    return {
        "email": email.strip(),
        "registrations": registrations
    }

def get_workshop_resources(workshop_id: str):
    """Returns all resources for a specific workshop."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM workshop_resources WHERE workshop_id = %s ORDER BY date_added DESC", (workshop_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_workshop_resource(workshop_id: str, title: str, resource_type: str = "Notes", link_url: str = "", description: str = ""):
    """Adds a new resource (Notes, Test, Assignment, Code, Video) to a workshop."""
    import time as time_module
    res_id = f"res-{int(time_module.time() * 1000) % 1000000}"
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO workshop_resources (id, workshop_id, title, resource_type, link_url, description, date_added)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (res_id, workshop_id, title, resource_type, link_url, description, date_str))
    conn.commit()
    conn.close()
    
    return {
        "id": res_id,
        "workshop_id": workshop_id,
        "title": title,
        "resource_type": resource_type,
        "link_url": link_url,
        "description": description,
        "date_added": date_str
    }

def delete_workshop_resource(resource_id: str):
    """Deletes a workshop resource by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM workshop_resources WHERE id = %s", (resource_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return {"success": deleted, "id": resource_id}

# --- DAILY ATTENDANCE SYSTEM FUNCTIONS ---

def get_workshop_attendance(workshop_id: str, date: str):
    """Returns attendance records for all enrolled students in a workshop for a specific date."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Fetch all students enrolled in this workshop
    cursor.execute("SELECT id, name, email, phone FROM students WHERE workshop_id = %s", (workshop_id,))
    students_list = [dict(s) for s in cursor.fetchall()]
    
    # Fetch existing attendance for this date
    cursor.execute("SELECT * FROM workshop_attendance WHERE workshop_id = %s AND date = %s", (workshop_id, date))
    att_rows = {r['student_id']: dict(r) for r in cursor.fetchall()}
    
    conn.close()
    
    result = []
    for st in students_list:
        att = att_rows.get(st['id'])
        result.append({
            "student_id": st['id'],
            "name": st['name'],
            "email": st['email'],
            "phone": st['phone'],
            "date": date,
            "status": att['status'] if att else "Present"
        })
    return result

def save_workshop_attendance(workshop_id: str, date: str, attendance_records: list):
    """Saves or updates daily attendance for students in a workshop."""
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_connection()
    cursor = conn.cursor()
    
    for rec in attendance_records:
        st_id = rec.get('student_id')
        st_name = rec.get('name', '')
        st_status = rec.get('status', 'Present')
        att_id = f"att-{workshop_id}-{st_id}-{date}"
        
        cursor.execute("""
        INSERT INTO workshop_attendance (id, workshop_id, student_id, student_name, date, status, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(id) DO UPDATE SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
        """, (att_id, workshop_id, st_id, st_name, date, st_status, date_now))
        
    conn.commit()
    conn.close()
    return {"success": True, "workshop_id": workshop_id, "date": date, "count": len(attendance_records)}

def has_attendance_for_date(workshop_id: str, date: str):
    """Returns whether the admin has saved any attendance for the requested date."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS count FROM workshop_attendance WHERE workshop_id = %s AND date = %s",
        (workshop_id, date),
    )
    row = cursor.fetchone()
    conn.close()
    return bool(row and row['count'] > 0)

def is_student_present_for_date(email: str, workshop_id: str, date: str):
    """Returns whether a registered student was marked Present on a given date."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1
        FROM students s
        JOIN workshop_attendance a ON a.student_id = s.id AND a.workshop_id = s.workshop_id
        WHERE LOWER(s.email) = LOWER(%s)
          AND s.workshop_id = %s
          AND a.date = %s
          AND LOWER(a.status) = 'present'
        LIMIT 1
    """, (email.strip(), workshop_id, date))
    present = cursor.fetchone() is not None
    conn.close()
    return present

def get_workshop_attendance_dates(workshop_id: str):
    """Returns distinct past dates for which attendance has been recorded for a workshop."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT DISTINCT date 
    FROM workshop_attendance 
    WHERE workshop_id = %s 
    ORDER BY date DESC
    """, (workshop_id,))
    rows = cursor.fetchall()
    conn.close()
    return [r['date'] for r in rows]

def get_student_workshop_id(student_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT workshop_id FROM students WHERE id = %s", (student_id,))
    row = cursor.fetchone()
    conn.close()
    return row['workshop_id'] if row else None

def get_resource_workshop_id(resource_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT workshop_id FROM workshop_resources WHERE id = %s", (resource_id,))
    row = cursor.fetchone()
    conn.close()
    return row['workshop_id'] if row else None

def get_test_workshop_id(test_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT workshop_id FROM tests WHERE id = %s", (test_id,))
    row = cursor.fetchone()
    conn.close()
    return row['workshop_id'] if row else None

def is_student_registered_for_workshop(email: str, workshop_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM students WHERE LOWER(email) = LOWER(%s) AND workshop_id = %s AND allowed = 1 LIMIT 1",
        (email.strip(), workshop_id),
    )
    registered = cursor.fetchone() is not None
    conn.close()
    return registered


# --- AI TEST MAKER & ONLINE TEST FUNCTIONS ---

def get_workshop_tests(workshop_id: str, for_student: bool = False):
    """Returns tests for a workshop. If for_student is True, filters only Published tests."""
    conn = get_connection()
    cursor = conn.cursor()
    if for_student:
        cursor.execute("SELECT * FROM tests WHERE workshop_id = %s AND status = 'Published' ORDER BY created_at DESC", (workshop_id,))
    else:
        cursor.execute("SELECT * FROM tests WHERE workshop_id = %s ORDER BY created_at DESC", (workshop_id,))
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for r in rows:
        item = dict(r)
        if item.get('questions_json'):
            try:
                item['questions'] = json.loads(item['questions_json'])
            except Exception:
                item['questions'] = []
        else:
            item['questions'] = []
        result.append(item)
    return result

def save_test(workshop_id: str, title: str, description: str, level: str, type: str, duration_mins: int, total_questions: int, questions: list, status: str = 'Draft', is_live: int = 0, test_id: str = None):
    """Creates or updates a workshop test."""
    import time as time_module
    tid = test_id if test_id else f"test-{int(time_module.time() * 1000) % 1000000}"
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    q_json = json.dumps(questions)
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO tests (id, workshop_id, title, description, level, type, duration_mins, total_questions, questions_json, status, is_live, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT(id) DO UPDATE SET
        title = EXCLUDED.title,
        description = EXCLUDED.description,
        level = EXCLUDED.level,
        type = EXCLUDED.type,
        duration_mins = EXCLUDED.duration_mins,
        total_questions = EXCLUDED.total_questions,
        questions_json = EXCLUDED.questions_json,
        status = EXCLUDED.status,
        is_live = EXCLUDED.is_live
    """, (tid, workshop_id, title, description or '', level or 'Intermediate', type or 'single_correct',
          int(duration_mins or 15), int(total_questions or len(questions)), q_json, status, int(is_live), date_now))
    conn.commit()
    
    cursor.execute("SELECT * FROM tests WHERE id = %s", (tid,))
    row = cursor.fetchone()
    conn.close()
    
    invalidate_leaderboard_cache(test_id=tid, workshop_id=workshop_id)
    res = dict(row)
    res['questions'] = json.loads(res['questions_json'])
    return res

def toggle_test_live(test_id: str, is_live: int):
    """Toggles live lock status of a test (1 = Start test live for students, 0 = Lock)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tests SET is_live = %s WHERE id = %s", (int(is_live), test_id))
    conn.commit()
    cursor.execute("SELECT * FROM tests WHERE id = %s", (test_id,))
    row = cursor.fetchone()
    conn.close()
    ws_id = row['workshop_id'] if row else None
    invalidate_leaderboard_cache(test_id=test_id, workshop_id=ws_id)
    if row:
        res = dict(row)
        res['questions'] = json.loads(res['questions_json'])
        return res
    return None

def toggle_test_publish(test_id: str, status: str):
    """Toggles publish status of a test ('Published' vs 'Draft')."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tests SET status = %s WHERE id = %s", (status, test_id))
    conn.commit()
    cursor.execute("SELECT * FROM tests WHERE id = %s", (test_id,))
    row = cursor.fetchone()
    conn.close()
    ws_id = row['workshop_id'] if row else None
    invalidate_leaderboard_cache(test_id=test_id, workshop_id=ws_id)
    if row:
        res = dict(row)
        res['questions'] = json.loads(res['questions_json'])
        return res
    return None

def delete_test(test_id: str):
    """Deletes a test by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT workshop_id FROM tests WHERE id = %s", (test_id,))
    test_row = cursor.fetchone()
    ws_id = test_row['workshop_id'] if test_row else None
    cursor.execute("DELETE FROM tests WHERE id = %s", (test_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    invalidate_leaderboard_cache(test_id=test_id, workshop_id=ws_id)
    return {"success": deleted, "id": test_id}

def submit_test_answers(test_id: str, student_email: str, student_name: str, user_answers: dict, time_taken_seconds: int = None):
    """
    Evaluates student answers against correct test answers automatically.
    Computes score & percentage, saves submission to DB, invalidates cache and returns instant live rank.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tests WHERE id = %s", (test_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
        
    test = dict(row)
    if test.get('status') != 'Published' or int(test.get('is_live') or 0) != 1:
        conn.close()
        return None

    cursor.execute("""
    SELECT * FROM test_submissions
    WHERE test_id = %s AND LOWER(student_email) = LOWER(%s)
    ORDER BY submitted_at ASC
    LIMIT 1
    """, (test_id, student_email.strip()))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        previous = dict(existing)
        lb = get_test_leaderboard(test_id, student_email=student_email)
        detail = get_test_submission_detail(test_id, student_email) or {}
        return {
            "already_submitted": True,
            "submission_id": previous["id"],
            "test_id": previous["test_id"],
            "workshop_id": previous["workshop_id"],
            "score": previous["score"],
            "max_score": previous["max_score"],
            "percentage": previous["percentage"],
            "submitted_at": previous["submitted_at"],
            "evaluation": detail.get("questions", []),
            "student_rank": lb.get("student_rank"),
            "top_5": lb.get("top_5", []),
            "total_submissions": lb.get("total_submissions", 1)
        }


    questions = json.loads(test['questions_json'])
    
    score = 0
    max_score = len(questions)
    detailed_evaluation = []
    
    for q in questions:
        q_id = str(q.get('id'))
        correct_indices = set(q.get('correct_answers', []))
        user_choice = user_answers.get(q_id, [])
        
        if isinstance(user_choice, int):
            user_choice = [user_choice]
        user_choice_set = set(user_choice)
        
        is_correct = (user_choice_set == correct_indices)
        if is_correct:
            score += 1
            
        detailed_evaluation.append({
            "question_id": q_id,
            "question": q.get('question'),
            "user_answers": list(user_choice_set),
            "correct_answers": list(correct_indices),
            "is_correct": is_correct,
            "explanation": q.get('explanation', '')
        })
        
    pct = round((score / max_score * 100) if max_score > 0 else 0, 1)
    # Multiple students can submit in the same second, especially on timeout.
    sub_id = f"sub-{test_id}-{secrets.token_urlsafe(12)}"
    date_now = get_ist_now_str()
    
    cursor.execute("""
    INSERT INTO test_submissions (id, test_id, workshop_id, student_email, student_name, answers_json, score, max_score, percentage, submitted_at, time_taken_seconds)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (sub_id, test_id, test['workshop_id'], student_email, student_name, json.dumps(user_answers), score, max_score, pct, date_now, max(0, int(time_taken_seconds or 0))))
    
    conn.commit()
    conn.close()
    
    # Invalidate cache for real-time live sync
    invalidate_leaderboard_cache(test_id=test_id, workshop_id=test['workshop_id'])
    
    # Fetch instant updated leaderboard with student's rank
    lb = get_test_leaderboard(test_id, student_email=student_email)
    
    return {
        "submission_id": sub_id,
        "test_id": test_id,
        "workshop_id": test['workshop_id'],
        "score": score,
        "max_score": max_score,
        "percentage": pct,
        "submitted_at": date_now,
        "time_taken_seconds": max(0, int(time_taken_seconds or 0)),
        "evaluation": detailed_evaluation,
        "student_rank": lb.get("student_rank"),
        "top_5": lb.get("top_5", []),
        "total_submissions": lb.get("total_submissions", 1)
    }

def get_student_test_submissions(student_email: str):
    """Returns past test submissions for a student."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT s.*, t.title as test_title, w.title as workshop_title
    FROM test_submissions s
    LEFT JOIN tests t ON s.test_id = t.id
    LEFT JOIN workshops w ON s.workshop_id = w.id
    WHERE LOWER(s.student_email) = LOWER(%s)
    ORDER BY s.submitted_at DESC
    """, (student_email.strip(),))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_test_submission_detail(test_id: str, student_email: str):
    """Returns a student's selected answers alongside the test's correct answers."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tests WHERE id = %s", (test_id,))
    test_row = cursor.fetchone()
    cursor.execute("""
    SELECT * FROM test_submissions
    WHERE test_id = %s AND LOWER(student_email) = LOWER(%s)
    ORDER BY submitted_at DESC
    LIMIT 1
    """, (test_id, student_email.strip()))
    submission_row = cursor.fetchone()
    conn.close()
    if not test_row or not submission_row:
        return None

    test = dict(test_row)
    submission = dict(submission_row)
    answers = json.loads(submission.get('answers_json') or '{}')
    review = []
    for index, question in enumerate(json.loads(test['questions_json'])):
        question_id = str(question.get('id', index + 1))
        selected = answers.get(question_id, [])
        if isinstance(selected, int):
            selected = [selected]
        correct = question.get('correct_answers', [])
        review.append({
            'question_id': question_id,
            'question': question.get('question', ''),
            'options': question.get('options', []),
            'selected_answers': selected,
            'correct_answers': correct,
            'is_correct': set(selected) == set(correct),
            'explanation': question.get('explanation', ''),
        })
    return {
        'test_id': test_id,
        'test_title': test.get('title', ''),
        'student_name': submission.get('student_name', ''),
        'student_email': submission.get('student_email', ''),
        'score': submission.get('score', 0),
        'max_score': submission.get('max_score', 0),
        'percentage': submission.get('percentage', 0),
        'submitted_at': submission.get('submitted_at', ''),
        'time_taken_seconds': submission.get('time_taken_seconds'),
        'questions': review,
    }

def delete_test_submission(test_id: str, student_email: str):
    """Admin can remove an unauthorized/cheated student submission. Leaderboards recalculate cleanly."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT workshop_id FROM tests WHERE id = %s", (test_id,))
    test_row = cursor.fetchone()
    workshop_id = test_row['workshop_id'] if test_row else None
    
    cursor.execute("""
    DELETE FROM test_submissions
    WHERE test_id = %s AND LOWER(student_email) = LOWER(%s)
    """, (test_id, student_email.strip()))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    # Invalidate cache so remaining students' ranks are instantly re-calculated
    invalidate_leaderboard_cache(test_id=test_id, workshop_id=workshop_id)
    return {"success": deleted_count > 0, "deleted_count": deleted_count, "test_id": test_id, "student_email": student_email}

def get_all_team_members():
    """Returns list of all team members."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM team_members ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_team_member(name: str, role: str, image_url: str, linkedin_url: str = ""):
    """Adds a new core team member."""
    conn = get_connection()
    cursor = conn.cursor()
    tm_id = f"tm-{int(datetime.now().timestamp())}"
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("""
    INSERT INTO team_members (id, name, role, image_url, linkedin_url, created_at)
    VALUES (%s, %s, %s, %s, %s, %s)
    """, (tm_id, name.strip(), role.strip(), image_url.strip(), linkedin_url.strip() if linkedin_url else "", date_now))
    conn.commit()
    conn.close()
    return {"id": tm_id, "name": name, "role": role, "image_url": image_url, "linkedin_url": linkedin_url, "created_at": date_now}

def delete_team_member(tm_id: str):
    """Deletes a team member by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM team_members WHERE id = %s", (tm_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "deleted_id": tm_id}

def end_workshop(workshop_id: str, group_photo_url: str, feedback_prompt: str = "", feedback_questions: list = None):
    """
    Marks a workshop as ended/completed.
    Saves group photo URL, custom feedback prompt, and dynamic feedback questions.
    Automatically posts the group photo to the Media Gallery Activities section!
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT title FROM workshops WHERE id = %s", (workshop_id,))
    row = cursor.fetchone()
    ws_title = row['title'] if row else 'Workshop'

    date_now = datetime.now().strftime("%Y-%m-%d")
    q_json = json.dumps(feedback_questions if feedback_questions is not None else [])

    cursor.execute("""
    UPDATE workshops
    SET is_ended = 1, status = 'Completed', group_photo_url = %s, feedback_prompt = %s, feedback_questions_json = %s
    WHERE id = %s
    """, (group_photo_url.strip(), feedback_prompt.strip(), q_json, workshop_id))

    # Auto-publish group photo to gallery media for Landing Page Activities showcase
    if group_photo_url.strip():
        media_id = f"media-{int(datetime.now().timestamp())}"
        cursor.execute("""
        INSERT INTO gallery_media (id, title, url, media_type, category, date)
        VALUES (%s, %s, %s, 'image', 'Activities / Workshop', %s)
        """, (media_id, f"{ws_title} - Final Group Photo", group_photo_url.strip(), date_now))

    conn.commit()
    conn.close()
    return {
        "status": "success",
        "workshop_id": workshop_id,
        "is_ended": 1,
        "group_photo_url": group_photo_url,
        "feedback_prompt": feedback_prompt,
        "feedback_questions": feedback_questions or []
    }

def update_workshop_feedback_prompt(workshop_id: str, feedback_prompt: str, feedback_questions: list = None):
    """Updates workshop feedback prompt and optional dynamic feedback questions."""
    conn = get_connection()
    cursor = conn.cursor()
    if feedback_questions is not None:
        q_json = json.dumps(feedback_questions)
        cursor.execute("""
        UPDATE workshops
        SET feedback_prompt = %s, feedback_questions_json = %s
        WHERE id = %s
        """, (feedback_prompt.strip(), q_json, workshop_id))
    else:
        cursor.execute("UPDATE workshops SET feedback_prompt = %s WHERE id = %s", (feedback_prompt.strip(), workshop_id))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return {
        "success": updated,
        "workshop_id": workshop_id,
        "feedback_prompt": feedback_prompt,
        "feedback_questions": feedback_questions if feedback_questions is not None else []
    }

def submit_workshop_feedback(workshop_id: str, student_email: str, student_name: str, rating: int, feedback_text: str, suggestions: str = "", answers: dict = None):
    """Submits student feedback for an ended workshop with custom dynamic answers."""
    conn = get_connection()
    cursor = conn.cursor()
    fb_id = f"fb-{int(datetime.now().timestamp())}"
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    answers_json = json.dumps(answers if isinstance(answers, dict) else {})
    
    cursor.execute("""
    INSERT INTO workshop_feedbacks (id, workshop_id, student_email, student_name, rating, feedback_text, suggestions, submitted_at, answers_json)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (fb_id, workshop_id, student_email.strip(), student_name.strip(), rating, feedback_text.strip(), suggestions.strip(), date_now, answers_json))
    
    conn.commit()
    conn.close()
    return {
        "id": fb_id,
        "workshop_id": workshop_id,
        "student_email": student_email,
        "student_name": student_name,
        "rating": rating,
        "feedback_text": feedback_text,
        "suggestions": suggestions,
        "answers": answers or {},
        "submitted_at": date_now
    }

def get_workshop_feedbacks(workshop_id: str = None):
    """Fetches submitted feedbacks for a specific workshop or all workshops."""
    conn = get_connection()
    cursor = conn.cursor()
    if workshop_id:
        cursor.execute("""
        SELECT f.*, w.title as workshop_title, w.feedback_questions_json
        FROM workshop_feedbacks f
        LEFT JOIN workshops w ON f.workshop_id = w.id
        WHERE f.workshop_id = %s
        ORDER BY f.submitted_at DESC
        """, (workshop_id,))
    else:
        cursor.execute("""
        SELECT f.*, w.title as workshop_title, w.feedback_questions_json
        FROM workshop_feedbacks f
        LEFT JOIN workshops w ON f.workshop_id = w.id
        ORDER BY f.submitted_at DESC
        """)
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for r in rows:
        item = dict(r)
        if item.get('answers_json'):
            try:
                item['answers'] = json.loads(item['answers_json'])
            except Exception:
                item['answers'] = {}
        else:
            item['answers'] = {}
        if item.get('feedback_questions_json'):
            try:
                item['feedback_questions'] = json.loads(item['feedback_questions_json'])
            except Exception:
                item['feedback_questions'] = []
        else:
            item['feedback_questions'] = []
        result.append(item)
    return result

def get_student_feedbacks(student_email: str):
    """Returns list of workshop IDs for which student has submitted feedback."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT workshop_id FROM workshop_feedbacks WHERE LOWER(student_email) = LOWER(%s)", (student_email.strip(),))
    rows = cursor.fetchall()
    conn.close()
    return [r['workshop_id'] for r in rows]

# --- BLOGS CRUD ---
def get_all_blogs():
    """Fetches all published blogs from database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM blogs ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_blog_by_id(blog_id: str):
    """Fetches a specific blog by ID and increments view count."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE blogs SET views = views + 1 WHERE id = %s", (blog_id,))
    cursor.execute("SELECT * FROM blogs WHERE id = %s", (blog_id,))
    row = cursor.fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None

def add_blog(title: str, content: str, excerpt: str = "", author: str = "WDC RECB Editorial", category: str = "Tech & Web", read_time: str = "5 min read"):
    """Adds a new blog post globally."""
    import re
    conn = get_connection()
    cursor = conn.cursor()
    blog_id = f"blog-{int(datetime.now().timestamp())}"
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    created_at = datetime.now().strftime("%Y-%m-%d")
    
    if not excerpt:
        excerpt = content[:150] + "..." if len(content) > 150 else content
        
    cursor.execute("""
    INSERT INTO blogs (id, title, slug, content, excerpt, author, category, read_time, created_at, views)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
    """, (blog_id, title.strip(), slug, content.strip(), excerpt.strip(), author.strip(), category.strip(), read_time.strip(), created_at))
    
    conn.commit()
    conn.close()
    return {
        "id": blog_id,
        "title": title,
        "slug": slug,
        "content": content,
        "excerpt": excerpt,
        "author": author,
        "category": category,
        "read_time": read_time,
        "created_at": created_at,
        "views": 0
    }

def delete_blog(blog_id: str):
    """Deletes a blog post globally."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blogs WHERE id = %s", (blog_id,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Blog {blog_id} deleted successfully"}

# --- WORKSHOP ADMIN RBAC FUNCTIONS ---

def verify_admin_credentials(username: str, password: str):
    """
    Verifies admin credentials with role-based access control.
    First checks Super Admin (env vars), then checks scoped Workshop Admins (DB).
    Returns dict with role info or None if invalid.
    """
    import os
    env_user = os.getenv("ADMIN_USERNAME", "admin")
    env_pass = os.getenv("ADMIN_PASSWORD", "wdcadmin2026")
    
    # Check Super Admin credentials first
    if username.strip() == env_user and password.strip() == env_pass:
        return {
            "role": "super_admin",
            "workshop_id": "",
            "workshop_title": ""
        }
    
    # Check Workshop Admin credentials from DB
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, title, admin_password FROM workshops
    WHERE admin_username = %s AND admin_username <> ''
    ORDER BY id
    """, (username.strip(),))
    rows = cursor.fetchall()

    for row in rows:
        if verify_password(password.strip(), row['admin_password']):
            if row['admin_password'] and not row['admin_password'].startswith("pbkdf2_sha256$"):
                cursor.execute(
                    "UPDATE workshops SET admin_password = %s WHERE id = %s",
                    (hash_password(password.strip()), row['id'])
                )
                conn.commit()
            conn.close()
            return {
                "role": "workshop_admin",
                "workshop_id": row['id'],
                "workshop_title": row['title']
            }
    
    conn.close()
    return None

def update_workshop_credentials(workshop_id: str, admin_username: str, admin_password: str):
    """Super Admin can update/reset workshop admin credentials at any time."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE workshops SET admin_username = %s, admin_password = %s WHERE id = %s
    """, (
        admin_username.strip(),
        hash_password(admin_password.strip()) if admin_password else '',
        workshop_id
    ))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return {"success": updated, "workshop_id": workshop_id}

def get_workshop_credentials(workshop_id: str):
    """Returns non-sensitive workshop admin credential metadata for the super admin."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, title, admin_username, admin_password
    FROM workshops
    WHERE id = %s
    """, (workshop_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "workshop_id": row["id"],
        "workshop_title": row["title"],
        "admin_username": row["admin_username"] or "",
        "password_set": bool(row["admin_password"]),
    }

def get_test_leaderboard(test_id: str, student_email: str = None):
    """
    Returns leaderboard data for a specific test with sub-millisecond in-memory caching.
    - Full ranked list of all submissions (sorted by score desc, then time asc)
    - Top 5 highlighted
    - If student_email provided, includes their rank position
    """
    cache_key = f"test_{test_id}"
    now = time.time()
    
    with _CACHE_LOCK:
        cached = _LEADERBOARD_CACHE.get(cache_key)
        if cached and (now - cached.get("timestamp", 0) < _CACHE_TTL):
            base_data = cached.get("data")
        else:
            base_data = None

    if base_data is None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT student_name, student_email, score, max_score, percentage, submitted_at, time_taken_seconds
        FROM test_submissions
        WHERE test_id = %s
        ORDER BY percentage DESC, score DESC, time_taken_seconds ASC NULLS LAST, submitted_at ASC
        """, (test_id,))
        rows = cursor.fetchall()
        conn.close()
        
        leaderboard = []
        best_by_email = {}
        for r in rows:
            key = (r['student_email'] or '').strip().lower()
            if key and key not in best_by_email:
                best_by_email[key] = r

        ranked_rows = sorted(
            best_by_email.values(),
            key=lambda r: (-(float(r['percentage'] or 0)), -(int(r['score'] or 0)), r['time_taken_seconds'] is None, int(r['time_taken_seconds'] or 0), r['submitted_at'] or '')
        )
        
        for idx, r in enumerate(ranked_rows):
            entry = {
                "rank": idx + 1,
                "student_name": r['student_name'],
                "student_email": r['student_email'],
                "score": r['score'],
                "max_score": r['max_score'],
                "percentage": r['percentage'],
                "time_taken_seconds": r['time_taken_seconds'],
                "submitted_at": r['submitted_at']
            }
            leaderboard.append(entry)
            
        base_data = {
            "test_id": test_id,
            "total_submissions": len(leaderboard),
            "leaderboard": leaderboard,
            "top_5": leaderboard[:5]
        }
        with _CACHE_LOCK:
            _LEADERBOARD_CACHE[cache_key] = {"data": base_data, "timestamp": now}

    student_rank = None
    if student_email:
        norm_email = student_email.strip().lower()
        for entry in base_data["leaderboard"]:
            if (entry.get("student_email") or "").strip().lower() == norm_email:
                student_rank = entry
                break
    
    return {
        "test_id": base_data["test_id"],
        "total_submissions": base_data["total_submissions"],
        "leaderboard": base_data["leaderboard"],
        "top_5": base_data["top_5"],
        "student_rank": student_rank
    }

def get_workshop_overall_leaderboard(workshop_id: str, student_email: str = None):
    """Ranks students across all published quizzes with sub-millisecond in-memory caching."""
    cache_key = f"workshop_{workshop_id}"
    now = time.time()
    
    with _CACHE_LOCK:
        cached = _LEADERBOARD_CACHE.get(cache_key)
        if cached and (now - cached.get("timestamp", 0) < _CACHE_TTL):
            base_data = cached.get("data")
        else:
            base_data = None

    if base_data is None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, total_questions
        FROM tests
        WHERE workshop_id = %s AND status = 'Published'
        """, (workshop_id,))
        tests = cursor.fetchall()
        cursor.execute("""
        SELECT id, name, email
        FROM students
        WHERE workshop_id = %s AND allowed = 1
        ORDER BY name ASC
        """, (workshop_id,))
        students = cursor.fetchall()
        cursor.execute("""
        SELECT s.student_email, s.student_name, s.test_id, s.score, s.max_score,
               s.percentage, s.time_taken_seconds, s.submitted_at
        FROM test_submissions s
        JOIN tests t ON t.id = s.test_id
        WHERE t.workshop_id = %s AND t.status = 'Published'
        ORDER BY s.student_email, s.test_id, s.percentage DESC, s.score DESC,
                 s.time_taken_seconds ASC NULLS LAST, s.submitted_at ASC
        """, (workshop_id,))
        submissions = cursor.fetchall()
        conn.close()

        total_quizzes = len(tests)
        enrolled_by_email = {
            (student['email'] or '').strip().lower(): student for student in students
            if (student['email'] or '').strip()
        }
        best_attempts = {}
        for submission in submissions:
            email = (submission['student_email'] or '').strip().lower()
            if not email:
                continue
            key = (email, submission['test_id'])
            if key not in best_attempts:
                best_attempts[key] = submission

        attempts_by_email = {}
        for (email, _test_id), submission in best_attempts.items():
            attempts_by_email.setdefault(email, []).append(submission)

        leaderboard = []
        for email, attempts in attempts_by_email.items():
            total_percentage = sum(float(item['percentage'] or 0) for item in attempts)
            total_score = sum(int(item['score'] or 0) for item in attempts)
            attempted_quizzes = len(attempts)
            average_percentage = total_percentage / attempted_quizzes if attempted_quizzes else 0
            student = enrolled_by_email.get(email)
            student_name = (student['name'] if (student and student.get('name')) else None) or attempts[0].get('student_name') or email
            canonical_email = (student['email'] if (student and student.get('email')) else None) or email
            leaderboard.append({
                "student_name": student_name,
                "student_email": canonical_email,
                "average_percentage": round(average_percentage, 2),
                "total_percentage": round(total_percentage, 2),
                "total_score": total_score,
                "attempted_quizzes": attempted_quizzes,
                "total_quizzes": total_quizzes,
                "participation_rate": round((attempted_quizzes / total_quizzes) * 100, 2) if total_quizzes else 0,
                "not_attempted_quizzes": max(total_quizzes - attempted_quizzes, 0),
            })

        leaderboard.sort(key=lambda item: (
            -item['total_percentage'],
            -item['average_percentage'],
            -item['attempted_quizzes'],
            item['student_name'].lower(),
        ))
        for index, entry in enumerate(leaderboard, start=1):
            entry['rank'] = index

        base_data = {
            "workshop_id": workshop_id,
            "total_quizzes": total_quizzes,
            "total_enrolled": max(len(students), len(leaderboard)),
            "ranked_students": len(leaderboard),
            "unranked_students": max(len(students) - len(leaderboard), 0),
            "normalization": "Total percentage and average across attempted published quizzes.",
            "leaderboard": leaderboard,
            "top_5": leaderboard[:5]
        }
        with _CACHE_LOCK:
            _LEADERBOARD_CACHE[cache_key] = {"data": base_data, "timestamp": now}

    student_rank = None
    if student_email:
        normalized_email = student_email.strip().lower()
        for entry in base_data["leaderboard"]:
            if (entry.get("student_email") or "").strip().lower() == normalized_email:
                student_rank = entry
                break

    return {
        "workshop_id": base_data["workshop_id"],
        "total_quizzes": base_data["total_quizzes"],
        "total_enrolled": base_data["total_enrolled"],
        "ranked_students": base_data["ranked_students"],
        "unranked_students": base_data["unranked_students"],
        "normalization": base_data["normalization"],
        "leaderboard": base_data["leaderboard"],
        "top_5": base_data["top_5"],
        "student_rank": student_rank,
    }
