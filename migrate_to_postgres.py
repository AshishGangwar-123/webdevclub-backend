import os
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load env variables from backend/.env
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

DB_URL = os.getenv("DATABASE_URL")
SQLITE_DB_PATH = os.path.join(os.path.dirname(__file__), "wdc_portal.db")

print(f"Connecting to Neon PostgreSQL...")

def get_pg_connection():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)

def get_sqlite_connection():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def create_pg_tables(pg_conn):
    with pg_conn.cursor() as cur:
        # 1. Workshops
        cur.execute("""
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

        # 2. Notifications
        cur.execute("""
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

        # 3. Students
        cur.execute("""
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

        # 4. Workshop Resources
        cur.execute("""
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

        # 5. Workshop Attendance
        cur.execute("""
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

        # 6. Tests
        cur.execute("""
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

        # 7. Test Submissions
        cur.execute("""
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
            submitted_at TEXT,
            time_taken_seconds INTEGER
        );
        """)

        # 8. Gallery Media
        cur.execute("""
        CREATE TABLE IF NOT EXISTS gallery_media (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            media_type TEXT DEFAULT 'video',
            category TEXT DEFAULT 'Highlight',
            date TEXT
        );
        """)

        # 9. Team Members
        cur.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            image_url TEXT NOT NULL,
            linkedin_url TEXT,
            created_at TEXT
        );
        """)

        # 10. Workshop Feedbacks
        cur.execute("""
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

        # 11. Subscribers
        cur.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            email TEXT PRIMARY KEY,
            date TEXT
        );
        """)

    pg_conn.commit()
    print("PostgreSQL tables created/verified successfully.")

def migrate_data():
    if not os.path.exists(SQLITE_DB_PATH):
        print("No SQLite database found, initializing PostgreSQL tables only.")
        pg_conn = get_pg_connection()
        create_pg_tables(pg_conn)
        pg_conn.close()
        return

    sqlite_conn = get_sqlite_connection()
    pg_conn = get_pg_connection()

    create_pg_tables(pg_conn)

    sq_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    tables = [
        "workshops", "notifications", "students", "workshop_resources",
        "workshop_attendance", "tests", "test_submissions", "gallery_media",
        "team_members", "workshop_feedbacks", "subscribers"
    ]

    for table in tables:
        # Check if table exists in SQLite
        sq_cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        if not sq_cur.fetchone():
            continue

        sq_cur.execute(f"SELECT * FROM {table}")
        rows = sq_cur.fetchall()
        if not rows:
            print(f"Table '{table}' is empty in SQLite. Skipping data transfer.")
            continue

        columns = rows[0].keys()
        cols_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))

        # Check primary key column for ON CONFLICT clause
        pk_col = "email" if table == "subscribers" else "id"

        update_cols = [f"{c} = EXCLUDED.{c}" for c in columns if c != pk_col]
        if update_cols:
            on_conflict = f"ON CONFLICT ({pk_col}) DO UPDATE SET {', '.join(update_cols)}"
        else:
            on_conflict = f"ON CONFLICT ({pk_col}) DO NOTHING"

        query = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) {on_conflict}"

        inserted_count = 0
        for r in rows:
            values = tuple(dict(r).values())
            pg_cur.execute(query, values)
            inserted_count += 1

        print(f"Migrated {inserted_count} rows into '{table}' table.")

    pg_conn.commit()
    sqlite_conn.close()
    pg_conn.close()
    print("\nSUCCESS: All data migrated from SQLite to Neon PostgreSQL successfully!")

if __name__ == "__main__":
    migrate_data()
