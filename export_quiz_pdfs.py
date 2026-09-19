"""
WDC Quiz Dashboard PDF Exporter
================================
READ-ONLY script — no data is modified.
Exports all quiz dashboards + overall dashboard to PDF files.
"""

import os
import sys
import json
from datetime import datetime

# Load .env before importing database
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

import psycopg2
from psycopg2.extras import RealDictCursor

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# ─── Config ───────────────────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "quiz_exports")
os.makedirs(OUTPUT_DIR, exist_ok=True)

DB_URL = os.getenv("DATABASE_URL")

# ─── Colors ───────────────────────────────────────────────────────────────────
WDC_BLUE   = colors.HexColor("#00b4d8")
WDC_DARK   = colors.HexColor("#0077b6")
WDC_LIGHT  = colors.HexColor("#e8f4f8")
RANK1_COL  = colors.HexColor("#FFD700")
RANK2_COL  = colors.HexColor("#C0C0C0")
RANK3_COL  = colors.HexColor("#CD7F32")
ROW_ALT    = colors.HexColor("#f0f8ff")
HEADER_BG  = colors.HexColor("#023e8a")

# ─── DB Connection ─────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor, connect_timeout=10)

# ─── Fetch Functions (READ ONLY) ───────────────────────────────────────────────
def fetch_all_workshops():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM workshops ORDER BY date DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def fetch_workshop_tests(workshop_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE workshop_id = %s ORDER BY created_at DESC", (workshop_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        try:
            r['questions'] = json.loads(r.get('questions_json') or '[]')
        except:
            r['questions'] = []
    return rows

def fetch_test_leaderboard(test_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT student_name, student_email, score, max_score, percentage,
               time_taken_seconds, submitted_at
        FROM test_submissions
        WHERE test_id = %s
        ORDER BY percentage DESC, score DESC, time_taken_seconds ASC NULLS LAST, submitted_at ASC
    """, (test_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    seen = {}
    for r in rows:
        email = (r.get('student_email') or '').strip().lower()
        if email and email not in seen:
            seen[email] = r

    ranked = sorted(seen.values(),
                    key=lambda x: (-(float(x.get('percentage') or 0)),
                                   -(int(x.get('score') or 0)),
                                   x.get('time_taken_seconds') is None,
                                   int(x.get('time_taken_seconds') or 0)))
    for i, r in enumerate(ranked):
        r['rank'] = i + 1
    return ranked

def fetch_overall_leaderboard(workshop_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, total_questions FROM tests WHERE workshop_id = %s AND status = 'Published'", (workshop_id,))
    tests = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT id, name, email FROM students WHERE workshop_id = %s AND allowed = 1", (workshop_id,))
    students = [dict(r) for r in cur.fetchall()]
    cur.execute("""
        SELECT s.student_email, s.student_name, s.test_id, s.score, s.max_score,
               s.percentage, s.time_taken_seconds, s.submitted_at
        FROM test_submissions s
        JOIN tests t ON t.id = s.test_id
        WHERE s.workshop_id = %s AND t.workshop_id = %s AND t.status = 'Published'
        ORDER BY s.student_email, s.percentage DESC, s.score DESC,
                 s.time_taken_seconds ASC NULLS LAST
    """, (workshop_id, workshop_id))
    submissions = [dict(r) for r in cur.fetchall()]
    conn.close()

    enrolled = {(s['email'] or '').strip().lower(): s for s in students if s.get('email')}
    best = {}
    for sub in submissions:
        email = (sub['student_email'] or '').strip().lower()
        key = (email, sub['test_id'])
        if email in enrolled and key not in best:
            best[key] = sub

    by_email = {}
    for (email, _tid), sub in best.items():
        by_email.setdefault(email, []).append(sub)

    leaderboard = []
    total_quizzes = len(tests)
    for email, attempts in by_email.items():
        avg_pct = sum(float(a.get('percentage') or 0) for a in attempts) / len(attempts)
        student = enrolled[email]
        leaderboard.append({
            'student_name': student['name'] or attempts[0]['student_name'],
            'student_email': student['email'],
            'average_percentage': round(avg_pct, 2),
            'attempted_quizzes': len(attempts),
            'total_quizzes': total_quizzes,
            'participation_rate': round((len(attempts)/total_quizzes)*100, 2) if total_quizzes else 0,
        })

    leaderboard.sort(key=lambda x: (-x['average_percentage'], -x['attempted_quizzes'], x['student_name'].lower()))
    for i, e in enumerate(leaderboard, 1):
        e['rank'] = i

    return {
        'total_quizzes': total_quizzes,
        'total_enrolled': len(students),
        'leaderboard': leaderboard,
        'top_5': leaderboard[:5],
    }

# ─── PDF Styles ───────────────────────────────────────────────────────────────
def get_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('WDCTitle', fontName='Helvetica-Bold', fontSize=20,
                               textColor=HEADER_BG, alignment=TA_CENTER, spaceAfter=4))
    styles.add(ParagraphStyle('WDCSubtitle', fontName='Helvetica', fontSize=11,
                               textColor=colors.HexColor("#555555"), alignment=TA_CENTER, spaceAfter=2))
    styles.add(ParagraphStyle('WDCSectionHead', fontName='Helvetica-Bold', fontSize=13,
                               textColor=WDC_DARK, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle('WDCMeta', fontName='Helvetica', fontSize=9,
                               textColor=colors.HexColor("#666666"), alignment=TA_LEFT))
    styles.add(ParagraphStyle('WDCSmall', fontName='Helvetica', fontSize=8,
                               textColor=colors.HexColor("#888888")))
    return styles

def rank_color(rank):
    if rank == 1: return RANK1_COL
    if rank == 2: return RANK2_COL
    if rank == 3: return RANK3_COL
    return colors.white if rank % 2 == 0 else ROW_ALT

def fmt_time(secs):
    if secs is None: return "-"
    secs = int(secs)
    m, s = divmod(secs, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"

def fmt_pct(val):
    try: return f"{float(val):.1f}%"
    except: return "-"

# ─── Build Quiz-Specific PDF ──────────────────────────────────────────────────
def build_quiz_pdf(workshop, test, leaderboard, output_path):
    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = get_styles()
    story = []

    story.append(Paragraph("Web Dev Club - Quiz Dashboard", styles['WDCTitle']))
    story.append(Paragraph(f"Workshop: {workshop.get('title', 'N/A')}", styles['WDCSubtitle']))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=2, color=WDC_DARK))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Quiz Information", styles['WDCSectionHead']))
    info_data = [
        ["Quiz Title", test.get('title', '-')],
        ["Quiz ID", test.get('id', '-')],
        ["Level", test.get('level', '-')],
        ["Type", test.get('type', '-')],
        ["Duration", f"{test.get('duration_mins', '-')} minutes"],
        ["Total Questions", str(test.get('total_questions', '-'))],
        ["Status", test.get('status', '-')],
        ["Live", "Yes" if test.get('is_live') else "No"],
        ["Created At", str(test.get('created_at', '-'))],
        ["Total Submissions", str(len(leaderboard))],
    ]
    info_table = Table(info_data, colWidths=[5*cm, 11*cm])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), WDC_LIGHT),
        ('TEXTCOLOR', (0,0), (0,-1), WDC_DARK),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME', (1,0), (1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [WDC_LIGHT, colors.white]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 14))

    if leaderboard:
        scores = [float(r.get('percentage') or 0) for r in leaderboard]
        avg_score = sum(scores) / len(scores)
        story.append(Paragraph("Performance Summary", styles['WDCSectionHead']))
        stats_data = [
            ["Total Participants", str(len(leaderboard))],
            ["Average Score", fmt_pct(avg_score)],
            ["Highest Score", fmt_pct(max(scores))],
            ["Lowest Score", fmt_pct(min(scores))],
        ]
        stats_table = Table(stats_data, colWidths=[5*cm, 11*cm])
        stats_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,-1), WDC_LIGHT),
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,0), (-1,-1), [WDC_LIGHT, colors.white]),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
            ('PADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(stats_table)
        story.append(Spacer(1, 14))

    story.append(Paragraph("Leaderboard (Best Attempt per Student)", styles['WDCSectionHead']))

    if not leaderboard:
        story.append(Paragraph("No submissions yet.", styles['WDCMeta']))
    else:
        header = ["Rank", "Student Name", "Email", "Score", "Percentage", "Time Taken", "Submitted At"]
        table_data = [header]
        for r in leaderboard:
            table_data.append([
                str(r.get('rank', '-')),
                str(r.get('student_name', '-')),
                str(r.get('student_email', '-')),
                f"{r.get('score','-')}/{r.get('max_score','-')}",
                fmt_pct(r.get('percentage')),
                fmt_time(r.get('time_taken_seconds')),
                str(r.get('submitted_at', '-'))[:16] if r.get('submitted_at') else '-',
            ])

        col_widths = [1.2*cm, 4*cm, 5*cm, 2*cm, 2.2*cm, 2*cm, 2.5*cm]
        lb_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        ts = [
            ('BACKGROUND', (0,0), (-1,0), HEADER_BG),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (1,1), (2,-1), 'LEFT'),
            ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#dddddd")),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, ROW_ALT]),
            ('PADDING', (0,0), (-1,-1), 4),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]
        for i in range(min(3, len(leaderboard))):
            ts.append(('BACKGROUND', (0, i+1), (-1, i+1), rank_color(i+1)))
        lb_table.setStyle(TableStyle(ts))
        story.append(lb_table)

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y, %I:%M %p')} | WDC RECB Quiz Export (Read-Only)",
                           styles['WDCSmall']))
    doc.build(story)
    print(f"  Saved: {os.path.basename(output_path)}")

# ─── Build Overall Dashboard PDF ──────────────────────────────────────────────
def build_overall_pdf(workshop, overall_data, all_tests, output_path):
    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = get_styles()
    story = []

    story.append(Paragraph("Web Dev Club - Overall Dashboard", styles['WDCTitle']))
    story.append(Paragraph(f"Workshop: {workshop.get('title', 'N/A')}", styles['WDCSubtitle']))
    story.append(Paragraph(f"Workshop ID: {workshop.get('id', 'N/A')}  |  Date: {workshop.get('date', '-')}",
                           styles['WDCSubtitle']))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=2, color=WDC_DARK))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Workshop Overview", styles['WDCSectionHead']))
    lb = overall_data.get('leaderboard', [])
    summary_data = [
        ["Total Quizzes (Published)", str(overall_data.get('total_quizzes', 0))],
        ["Total Quizzes (All)", str(len(all_tests))],
        ["Enrolled Students", str(overall_data.get('total_enrolled', 0))],
        ["Students Ranked", str(len(lb))],
        ["Students Not Attempted", str(max(overall_data.get('total_enrolled', 0) - len(lb), 0))],
    ]
    sum_table = Table(summary_data, colWidths=[6*cm, 10*cm])
    sum_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), WDC_LIGHT),
        ('TEXTCOLOR', (0,0), (0,-1), WDC_DARK),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [WDC_LIGHT, colors.white]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
        ('PADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(sum_table)
    story.append(Spacer(1, 12))

    story.append(Paragraph("All Quizzes in This Workshop", styles['WDCSectionHead']))
    if not all_tests:
        story.append(Paragraph("No quizzes found.", styles['WDCMeta']))
    else:
        quiz_header = ["#", "Quiz Title", "Level", "Questions", "Duration", "Status", "Submissions"]
        quiz_rows = [quiz_header]
        for i, t in enumerate(all_tests, 1):
            quiz_rows.append([
                str(i),
                str(t.get('title', '-')),
                str(t.get('level', '-')),
                str(t.get('total_questions', '-')),
                f"{t.get('duration_mins','-')} min",
                str(t.get('status', '-')),
                str(t.get('_submission_count', '-')),
            ])
        quiz_table = Table(quiz_rows, colWidths=[0.8*cm, 5*cm, 2.2*cm, 2*cm, 2*cm, 2*cm, 2.5*cm])
        quiz_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), HEADER_BG),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (1,1), (1,-1), 'LEFT'),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, ROW_ALT]),
            ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#dddddd")),
            ('PADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(quiz_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Overall Leaderboard (Avg % across Published Quizzes)", styles['WDCSectionHead']))
    story.append(Paragraph(
        "Ranking based on average percentage across attempted published quizzes. Not-attempted quizzes excluded.",
        styles['WDCMeta']))
    story.append(Spacer(1, 6))

    if not lb:
        story.append(Paragraph("No submissions yet.", styles['WDCMeta']))
    else:
        header = ["Rank", "Student Name", "Email", "Avg %", "Quizzes Done", "Participation"]
        table_data = [header]
        for r in lb:
            table_data.append([
                str(r.get('rank', '-')),
                str(r.get('student_name', '-')),
                str(r.get('student_email', '-')),
                fmt_pct(r.get('average_percentage')),
                f"{r.get('attempted_quizzes','-')}/{r.get('total_quizzes','-')}",
                fmt_pct(r.get('participation_rate')),
            ])
        col_widths = [1.2*cm, 4.5*cm, 5.3*cm, 2*cm, 2.5*cm, 2*cm]
        overall_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        ts = [
            ('BACKGROUND', (0,0), (-1,0), HEADER_BG),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (1,1), (2,-1), 'LEFT'),
            ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#dddddd")),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, ROW_ALT]),
            ('PADDING', (0,0), (-1,-1), 4),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]
        for i in range(min(3, len(lb))):
            ts.append(('BACKGROUND', (0, i+1), (-1, i+1), rank_color(i+1)))
        overall_table.setStyle(TableStyle(ts))
        story.append(overall_table)

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y, %I:%M %p')} | WDC RECB Quiz Export (Read-Only)",
                           styles['WDCSmall']))
    doc.build(story)
    print(f"  Saved: {os.path.basename(output_path)}")

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  WDC Quiz Dashboard PDF Exporter")
    print("  Mode: READ-ONLY (No data modified)")
    print("=" * 60)

    print("\nConnecting to database...")
    workshops = fetch_all_workshops()
    if not workshops:
        print("No workshops found.")
        return

    print(f"Found {len(workshops)} workshop(s).\n")
    total_pdfs = 0

    for ws in workshops:
        ws_id = ws.get('id', '')
        ws_title = ws.get('title', ws_id)
        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in ws_title)[:40].strip()

        print(f"\nWorkshop: {ws_title} (ID: {ws_id})")

        tests = fetch_workshop_tests(ws_id)
        if not tests:
            print("  No quizzes found for this workshop.")
            continue

        print(f"  Found {len(tests)} quiz(es).")

        conn = get_conn()
        cur = conn.cursor()
        for t in tests:
            cur.execute("SELECT COUNT(DISTINCT student_email) FROM test_submissions WHERE test_id = %s", (t['id'],))
            row = cur.fetchone()
            t['_submission_count'] = list(row.values())[0] if row else 0
        conn.close()

        # Count titles to detect duplicates
        from collections import Counter
        title_counts = Counter(t.get('title', t.get('id', '')) for t in tests)
        title_seen = Counter()

        for test in tests:
            test_id = test.get('id', '')
            test_title = test.get('title', test_id)
            safe_test = "".join(c if c.isalnum() or c in " _-" else "_" for c in test_title)[:40].strip()

            title_seen[test_title] += 1
            if title_counts[test_title] > 1:
                filename = f"{safe_title}__{safe_test}__({test_id}).pdf"
            else:
                filename = f"{safe_title}__{safe_test}.pdf"

            print(f"  Exporting quiz: {test_title} (ID: {test_id}) -> {filename}")
            leaderboard = fetch_test_leaderboard(test_id)
            out_path = os.path.join(OUTPUT_DIR, filename)
            build_quiz_pdf(ws, test, leaderboard, out_path)
            total_pdfs += 1

        print(f"  Exporting overall dashboard for: {ws_title}")
        overall_data = fetch_overall_leaderboard(ws_id)
        overall_path = os.path.join(OUTPUT_DIR, f"{safe_title}__OVERALL_DASHBOARD.pdf")
        build_overall_pdf(ws, overall_data, tests, overall_path)
        total_pdfs += 1

    print("\n" + "=" * 60)
    print(f"  Done! {total_pdfs} PDF(s) exported.")
    print(f"  Location: {os.path.abspath(OUTPUT_DIR)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
