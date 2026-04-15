import os
import sqlite3
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from io import BytesIO
from datetime import datetime, date, timedelta
from urllib.parse import urlparse, parse_qs
import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_file, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "SACHET_FINAL_PRO_2026")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "edtech.db")

PAYMENT_METHODS = {
    "credits": "Sachet Wallet Credits",
    "upi": "UPI Payment",
    "card": "Credit / Debit Card",
    "netbanking": "Net Banking",
    "stripe": "Stripe Gateway Ready",
}

STREAK_CHALLENGES = {
    3: {"multiplier": 1.5, "risk": "Low risk"},
    7: {"multiplier": 2.0, "risk": "Medium risk"},
    14: {"multiplier": 3.0, "risk": "High risk"},
    30: {"multiplier": 5.0, "risk": "Extreme risk"},
}

ALLOWED_WAGERS = {50, 100, 200, 500}


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_youtube_url(url):
    url = (url or "").strip()
    if not url:
        return ""

    parsed = urlparse(url)
    video_id = ""

    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/")[0]
    elif "youtube.com" in parsed.netloc:
        if parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/embed/")[-1].split("/")[0]
        elif parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/shorts/")[-1].split("/")[0]
        else:
            video_id = parse_qs(parsed.query).get("v", [""])[0]

    if video_id:
        return f"https://www.youtube.com/embed/{video_id}"

    return url


def certificate_token(user_id, course_id, completed_at):
    payload = f"{user_id}:{course_id}:{completed_at}"
    return hmac.new(app.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def get_certificate_by_token(token):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT e.user_id, e.course_id, e.completed, e.completed_at, u.username,
                   c.title, creator.username AS creator_name
            FROM enrollments e
            JOIN users u ON u.id = e.user_id
            JOIN courses c ON c.id = e.course_id
            JOIN users creator ON creator.id = c.creator_id
            WHERE e.completed = 1 AND e.completed_at IS NOT NULL
            """
        ).fetchall()

    for row in rows:
        expected = certificate_token(row["user_id"], row["course_id"], row["completed_at"])
        if hmac.compare_digest(expected, token):
            return row
    return None


def build_certificate_pdf(cert, verify_url):
    buffer = BytesIO()
    page_width, page_height = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=landscape(A4))

    pdf.setFillColor(colors.HexColor("#fffaf0"))
    pdf.rect(0, 0, page_width, page_height, fill=1, stroke=0)
    pdf.setStrokeColor(colors.HexColor("#1d4ed8"))
    pdf.setLineWidth(16)
    pdf.rect(28, 28, page_width - 56, page_height - 56, fill=0, stroke=1)
    pdf.setStrokeColor(colors.HexColor("#f59e0b"))
    pdf.setLineWidth(4)
    pdf.rect(48, 48, page_width - 96, page_height - 96, fill=0, stroke=1)

    pdf.setFillColor(colors.HexColor("#172554"))
    pdf.setFont("Times-Bold", 40)
    pdf.drawCentredString(page_width / 2, page_height - 120, "Certificate of Completion")

    pdf.setFillColor(colors.HexColor("#1d4ed8"))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(page_width / 2, page_height - 78, "SACHET LEARNING PORTAL")

    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.setFont("Times-Roman", 18)
    pdf.drawCentredString(page_width / 2, page_height - 175, "This certificate is proudly presented to")

    pdf.setFillColor(colors.HexColor("#1d4ed8"))
    pdf.setFont("Times-Bold", 38)
    pdf.drawCentredString(page_width / 2, page_height - 235, cert["username"])

    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.setFont("Times-Roman", 17)
    pdf.drawCentredString(page_width / 2, page_height - 282, "for successfully completing the course")

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Times-BoldItalic", 28)
    pdf.drawCentredString(page_width / 2, page_height - 330, cert["title"])

    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.setFont("Helvetica", 12)
    pdf.drawCentredString(page_width / 2, page_height - 372, "Attendance, video completion and assessment requirements fulfilled.")

    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.line(110, 125, 300, 125)
    pdf.drawCentredString(205, 103, f"Completed: {cert['completed_at']}")
    pdf.line(page_width - 300, 125, page_width - 110, 125)
    pdf.drawCentredString(page_width - 205, 103, f"Creator: {cert['creator_name']}")

    qr = qrcode.make(verify_url)
    qr_buffer = BytesIO()
    qr.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    pdf.drawImage(ImageReader(qr_buffer), page_width - 160, 165, width=86, height=86)
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.drawCentredString(page_width - 117, 150, "Scan to verify")

    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(page_width / 2, 66, verify_url)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


def ensure_column(conn, table, column, definition):
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def payment_reference(method, user_id, course_id):
    raw = f"{method}:{user_id}:{course_id}:{datetime.now().isoformat()}:{app.secret_key}"
    return f"SCHT-{hashlib.sha256(raw.encode()).hexdigest()[:12].upper()}"


def check_streak_bet_status(conn, user_id):
    bet = conn.execute(
        "SELECT * FROM streak_bets WHERE user_id = ? AND status = 'Active' ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not bet:
        return bet

    last_checkin = datetime.strptime(bet["last_checkin"], "%Y-%m-%d").date() if bet["last_checkin"] else None
    if not last_checkin:
        start_date = datetime.strptime(bet["start_date"], "%Y-%m-%d").date()
        if date.today() > start_date:
            conn.execute(
                "UPDATE streak_bets SET status = 'Lost', settled_at = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), bet["id"]),
            )
            conn.commit()
            return None
        return bet

    if (date.today() - last_checkin).days > 1:
        conn.execute(
            "UPDATE streak_bets SET status = 'Lost', settled_at = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M"), bet["id"]),
        )
        conn.commit()
        return None
    return bet


def update_streak_on_module_completion(conn, user_id):
    bet = conn.execute(
        "SELECT * FROM streak_bets WHERE user_id = ? AND status = 'Active' ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not bet:
        return

    today = date.today()
    last_checkin = datetime.strptime(bet["last_checkin"], "%Y-%m-%d").date() if bet["last_checkin"] else None
    if last_checkin == today:
        return
    if last_checkin and (today - last_checkin).days > 1:
        conn.execute(
            "UPDATE streak_bets SET status = 'Lost', settled_at = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M"), bet["id"]),
        )
        return

    completed_days = bet["completed_days"] + 1
    if completed_days >= bet["duration_days"]:
        reward = int(bet["wager"] * bet["multiplier"])
        conn.execute("UPDATE users SET points = points + ? WHERE id = ?", (reward, user_id))
        conn.execute(
            """
            UPDATE streak_bets
            SET completed_days = ?, last_checkin = ?, status = 'Won', reward = ?, settled_at = ?
            WHERE id = ?
            """,
            (completed_days, today.isoformat(), reward, datetime.now().strftime("%Y-%m-%d %H:%M"), bet["id"]),
        )
    else:
        conn.execute(
            "UPDATE streak_bets SET completed_days = ?, last_checkin = ? WHERE id = ?",
            (completed_days, today.isoformat(), bet["id"]),
        )


def fallback_chatbot_reply(message):
    text = message.lower()
    if "payment" in text or "refund" in text or "protection" in text:
        return "Sachet protects course fees by holding payment until course progress and completion conditions are met. Use the payment/protection section to see the method, reference ID, and protected status."
    if "streak" in text or "bet" in text:
        return "Streak Betting lets you wager learning points on a 3, 7, 14, or 30 day challenge. Complete at least one module each day. If you miss a day, the active bet is marked lost."
    if "certificate" in text:
        return "Certificates unlock after all videos are completed and attendance is marked. The certificate includes a QR verification link."
    if "roadmap" in text or "progress" in text:
        return "Your course roadmap is generated from course modules: videos first, then quizzes, then attendance and certificate completion."
    return "I can help with payments, protected course fees, streak betting, progress tracking, quizzes, certificates, and learning methodology. Tell me which part you are stuck on."


def ai_chatbot_reply(message):
    base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
    if not base_url or not api_key:
        return fallback_chatbot_reply(message)

    try:
        endpoint = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": "gpt-5-mini",
            "messages": [
                {
                    "role": "system",
                    "content": "You are Sachet HelpBot for an EdTech + FinTech learning platform. Help learners with course progress, payment protection, streak betting, quizzes, certificates, and problem-solving methodology. Keep answers short and practical.",
                },
                {"role": "user", "content": message},
            ],
            "max_completion_tokens": 220,
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=18) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return fallback_chatbot_reply(message)


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('learner', 'creator')),
                balance REAL DEFAULT 5000.0,
                revenue REAL DEFAULT 0.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                creator_id INTEGER NOT NULL,
                price REAL NOT NULL,
                duration_months INTEGER DEFAULT 3,
                notice TEXT DEFAULT 'Welcome to the course!',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(creator_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                video_url TEXT NOT NULL,
                minutes_required INTEGER DEFAULT 30,
                position INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(course_id) REFERENCES courses(id)
            );

            CREATE TABLE IF NOT EXISTS enrollments (
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                attendance_marked INTEGER DEFAULT 0,
                paid_amount REAL DEFAULT 0.0,
                payment_released INTEGER DEFAULT 0,
                completed INTEGER DEFAULT 0,
                completed_at TEXT,
                enrolled_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, course_id),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(course_id) REFERENCES courses(id)
            );

            CREATE TABLE IF NOT EXISTS video_progress (
                user_id INTEGER NOT NULL,
                video_id INTEGER NOT NULL,
                watched_minutes INTEGER DEFAULT 0,
                completed INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, video_id),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(video_id) REFERENCES videos(id)
            );

            CREATE TABLE IF NOT EXISTS quizes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(course_id) REFERENCES courses(id)
            );

            CREATE TABLE IF NOT EXISTS quiz_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                quiz_id INTEGER NOT NULL,
                user_answer TEXT NOT NULL,
                status TEXT DEFAULT 'Submitted',
                feedback TEXT DEFAULT '',
                submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                evaluated_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(quiz_id) REFERENCES quizes(id)
            );

            CREATE TABLE IF NOT EXISTS payment_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL,
                status TEXT DEFAULT 'Protected Hold',
                reference TEXT UNIQUE NOT NULL,
                protection_status TEXT DEFAULT 'Course Fee Protection Active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(course_id) REFERENCES courses(id)
            );

            CREATE TABLE IF NOT EXISTS streak_bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                duration_days INTEGER NOT NULL,
                wager INTEGER NOT NULL,
                multiplier REAL NOT NULL,
                status TEXT DEFAULT 'Active',
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                last_checkin TEXT,
                completed_days INTEGER DEFAULT 0,
                reward INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                settled_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS ai_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                reply TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        ensure_column(conn, "users", "points", "INTEGER DEFAULT 1000")
        ensure_column(conn, "enrollments", "payment_method", "TEXT DEFAULT 'credits'")
        ensure_column(conn, "enrollments", "payment_reference", "TEXT DEFAULT ''")
        ensure_column(conn, "enrollments", "payment_status", "TEXT DEFAULT 'Protected Hold'")
        ensure_column(conn, "enrollments", "protection_status", "TEXT DEFAULT 'Course Fee Protection Active'")
        conn.commit()


def current_user(conn):
    if "uid" not in session:
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (session["uid"],)).fetchone()


def login_required():
    if "uid" not in session:
        return False
    return True


def creator_required():
    return login_required() and session.get("role") == "creator"


def learner_required():
    return login_required() and session.get("role") == "learner"


def seed_demo_course():
    with get_db() as conn:
        creator = conn.execute("SELECT * FROM users WHERE username = ?", ("creator_demo",)).fetchone()
        if not creator:
            conn.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                ("creator_demo", generate_password_hash("creator123"), "creator"),
            )
            creator_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            creator_id = creator["id"]

        existing = conn.execute("SELECT * FROM courses WHERE title = ?", ("Database Management Masterclass",)).fetchone()
        if existing:
            conn.commit()
            return

        conn.execute(
            """
            INSERT INTO courses (title, description, creator_id, price, duration_months, notice)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "Database Management Masterclass",
                "Learn DBMS fundamentals, SQL concepts, normalization, transactions, and real-world database design.",
                creator_id,
                1499,
                4,
                "Complete the video lesson and attempt the quiz before the weekly review.",
            ),
        )
        course_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO videos (course_id, title, video_url, minutes_required, position) VALUES (?, ?, ?, ?, ?)",
            (course_id, "DBMS Complete Introduction", normalize_youtube_url("https://youtu.be/kBdlM6hNDAE?si=RPWDa_YT0rXaLk_U"), 30, 1),
        )
        conn.execute(
            "INSERT INTO quizes (course_id, question, answer) VALUES (?, ?, ?)",
            (course_id, "What is the main purpose of a DBMS?", "To store, manage, and retrieve data efficiently"),
        )
        conn.commit()


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("u", "").strip()
        password = request.form.get("p", "")
        role = request.form.get("r", "learner")

        if role not in {"learner", "creator"}:
            flash("Please select a valid account type.")
            return redirect(url_for("signup"))

        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), role),
                )
                conn.commit()
            flash("Account created successfully. Please login.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists. Please choose another username.")

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("u", "").strip()
        password = request.form.get("p", "")
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if user and check_password_hash(user["password"], password):
                session["uid"] = user["id"]
                session["role"] = user["role"]
                session["uname"] = user["username"]
                return redirect(url_for("dashboard"))
        flash("Invalid username or password.")

    return render_template("login.html")


@app.route("/")
def dashboard():
    if not login_required():
        return redirect(url_for("login"))

    with get_db() as conn:
        user = current_user(conn)

        if session.get("role") == "creator":
            courses = conn.execute(
                """
                SELECT c.*,
                       COUNT(DISTINCT v.id) AS video_count,
                       COUNT(DISTINCT e.user_id) AS learner_count,
                       SUM(CASE WHEN e.completed = 1 THEN 1 ELSE 0 END) AS completed_count
                FROM courses c
                LEFT JOIN videos v ON v.course_id = c.id
                LEFT JOIN enrollments e ON e.course_id = c.id
                WHERE c.creator_id = ?
                GROUP BY c.id
                ORDER BY c.created_at DESC
                """,
                (user["id"],),
            ).fetchall()

            videos = conn.execute(
                """
                SELECT v.*, c.title AS course_title
                FROM videos v JOIN courses c ON c.id = v.course_id
                WHERE c.creator_id = ?
                ORDER BY c.id DESC, v.position ASC
                """,
                (user["id"],),
            ).fetchall()

            attendance = conn.execute(
                """
                SELECT e.*, u.username, c.title AS course_title
                FROM enrollments e
                JOIN users u ON u.id = e.user_id
                JOIN courses c ON c.id = e.course_id
                WHERE c.creator_id = ?
                ORDER BY e.enrolled_at DESC
                """,
                (user["id"],),
            ).fetchall()

            submissions = conn.execute(
                """
                SELECT qs.*, u.username, q.question, q.answer AS correct_answer, c.title AS course_title
                FROM quiz_submissions qs
                JOIN users u ON qs.user_id = u.id
                JOIN quizes q ON qs.quiz_id = q.id
                JOIN courses c ON c.id = q.course_id
                WHERE c.creator_id = ?
                ORDER BY qs.submitted_at DESC
                """,
                (user["id"],),
            ).fetchall()

            quizzes = conn.execute(
                """
                SELECT q.*, c.title AS course_title
                FROM quizes q JOIN courses c ON c.id = q.course_id
                WHERE c.creator_id = ?
                ORDER BY q.created_at DESC
                """,
                (user["id"],),
            ).fetchall()

            protected_payments = conn.execute(
                """
                SELECT pt.*, learner.username AS learner_name, c.title AS course_title
                FROM payment_transactions pt
                JOIN courses c ON c.id = pt.course_id
                JOIN users learner ON learner.id = pt.user_id
                WHERE c.creator_id = ?
                ORDER BY pt.created_at DESC
                """,
                (user["id"],),
            ).fetchall()

            return render_template(
                "creator.html",
                user=user,
                courses=courses,
                videos=videos,
                attendance=attendance,
                submissions=submissions,
                quizzes=quizzes,
                protected_payments=protected_payments,
            )

        check_streak_bet_status(conn, user["id"])
        market = conn.execute(
            """
            SELECT c.*, u.username AS creator_name, COUNT(v.id) AS video_count
            FROM courses c
            JOIN users u ON c.creator_id = u.id
            LEFT JOIN videos v ON v.course_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
            """
        ).fetchall()

        my_courses = conn.execute(
            """
            SELECT c.*, e.attendance_marked, e.completed, e.completed_at, e.paid_amount,
                   u.username AS creator_name,
                   COUNT(DISTINCT v.id) AS total_videos,
                   COUNT(DISTINCT CASE WHEN vp.completed = 1 THEN v.id END) AS completed_videos
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            JOIN users u ON u.id = c.creator_id
            LEFT JOIN videos v ON v.course_id = c.id
            LEFT JOIN video_progress vp ON vp.video_id = v.id AND vp.user_id = e.user_id
            WHERE e.user_id = ?
            GROUP BY c.id
            ORDER BY e.enrolled_at DESC
            """,
            (user["id"],),
        ).fetchall()

        course_videos = conn.execute(
            """
            SELECT v.*, c.title AS course_title, COALESCE(vp.watched_minutes, 0) AS watched_minutes,
                   COALESCE(vp.completed, 0) AS completed
            FROM videos v
            JOIN courses c ON c.id = v.course_id
            JOIN enrollments e ON e.course_id = c.id AND e.user_id = ?
            LEFT JOIN video_progress vp ON vp.video_id = v.id AND vp.user_id = ?
            ORDER BY v.course_id DESC, v.position ASC
            """,
            (user["id"], user["id"]),
        ).fetchall()

        quizzes = conn.execute(
            """
            SELECT q.*, c.title AS course_title,
                   latest.id AS submission_id,
                   latest.user_answer,
                   latest.status,
                   latest.feedback
            FROM quizes q
            JOIN courses c ON c.id = q.course_id
            JOIN enrollments e ON e.course_id = c.id AND e.user_id = ?
            LEFT JOIN quiz_submissions latest ON latest.id = (
                SELECT qs2.id FROM quiz_submissions qs2
                WHERE qs2.quiz_id = q.id AND qs2.user_id = ?
                ORDER BY qs2.submitted_at DESC LIMIT 1
            )
            ORDER BY q.created_at DESC
            """,
            (user["id"], user["id"]),
        ).fetchall()

        payments = conn.execute(
            """
            SELECT pt.*, c.title AS course_title
            FROM payment_transactions pt
            JOIN courses c ON c.id = pt.course_id
            WHERE pt.user_id = ?
            ORDER BY pt.created_at DESC
            """,
            (user["id"],),
        ).fetchall()

        active_bet = conn.execute(
            "SELECT * FROM streak_bets WHERE user_id = ? AND status = 'Active' ORDER BY created_at DESC LIMIT 1",
            (user["id"],),
        ).fetchone()

        bet_history = conn.execute(
            "SELECT * FROM streak_bets WHERE user_id = ? ORDER BY created_at DESC LIMIT 12",
            (user["id"],),
        ).fetchall()

        analytics = conn.execute(
            """
            SELECT c.id, c.title,
                   COUNT(DISTINCT v.id) AS total_videos,
                   COUNT(DISTINCT CASE WHEN vp.completed = 1 THEN v.id END) AS completed_videos,
                   COUNT(DISTINCT q.id) AS total_quizzes,
                   COUNT(DISTINCT CASE WHEN qs.status = 'Correct' THEN q.id END) AS correct_quizzes,
                   COUNT(DISTINCT CASE WHEN qs.status IN ('Needs Improvement', 'Partially Correct') THEN q.id END) AS weak_topics
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            LEFT JOIN videos v ON v.course_id = c.id
            LEFT JOIN video_progress vp ON vp.video_id = v.id AND vp.user_id = e.user_id
            LEFT JOIN quizes q ON q.course_id = c.id
            LEFT JOIN quiz_submissions qs ON qs.quiz_id = q.id AND qs.user_id = e.user_id
            WHERE e.user_id = ?
            GROUP BY c.id
            ORDER BY e.enrolled_at DESC
            """,
            (user["id"],),
        ).fetchall()

        weak_areas = conn.execute(
            """
            SELECT c.title AS course_title, q.question, qs.status, qs.feedback
            FROM quiz_submissions qs
            JOIN quizes q ON q.id = qs.quiz_id
            JOIN courses c ON c.id = q.course_id
            WHERE qs.user_id = ? AND qs.status IN ('Needs Improvement', 'Partially Correct')
            ORDER BY qs.submitted_at DESC
            LIMIT 6
            """,
            (user["id"],),
        ).fetchall()

        return render_template(
            "learner.html",
            user=user,
            market=market,
            my_courses=my_courses,
            course_videos=course_videos,
            quizzes=quizzes,
            payments=payments,
            payment_methods=PAYMENT_METHODS,
            streak_challenges=STREAK_CHALLENGES,
            active_bet=active_bet,
            bet_history=bet_history,
            analytics=analytics,
            weak_areas=weak_areas,
        )


@app.route("/publish", methods=["POST"])
def publish():
    if not creator_required():
        abort(403)

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    price = float(request.form.get("price", 0))
    duration_months = int(request.form.get("duration_months", 3))
    first_video_title = request.form.get("video_title", "First Lesson").strip() or "First Lesson"
    first_video_url = normalize_youtube_url(request.form.get("url", ""))

    with get_db() as conn:
        conn.execute(
            "INSERT INTO courses (title, description, price, duration_months, creator_id) VALUES (?, ?, ?, ?, ?)",
            (title, description, price, duration_months, session["uid"]),
        )
        course_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        if first_video_url:
            conn.execute(
                "INSERT INTO videos (course_id, title, video_url, minutes_required, position) VALUES (?, ?, ?, ?, ?)",
                (course_id, first_video_title, first_video_url, 30, 1),
            )
        conn.commit()
    flash("Course launched successfully.")
    return redirect(url_for("dashboard"))


@app.route("/add_video", methods=["POST"])
def add_video():
    if not creator_required():
        abort(403)

    course_id = request.form.get("course_id")
    title = request.form.get("title", "").strip()
    video_url = normalize_youtube_url(request.form.get("url", ""))
    minutes_required = int(request.form.get("minutes_required", 30))

    with get_db() as conn:
        course = conn.execute("SELECT * FROM courses WHERE id = ? AND creator_id = ?", (course_id, session["uid"])).fetchone()
        if not course:
            abort(403)
        position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM videos WHERE course_id = ?", (course_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO videos (course_id, title, video_url, minutes_required, position) VALUES (?, ?, ?, ?, ?)",
            (course_id, title, video_url, minutes_required, position),
        )
        conn.commit()
    flash("Video added to course.")
    return redirect(url_for("dashboard"))


@app.route("/post_notice", methods=["POST"])
def post_notice():
    if not creator_required():
        abort(403)

    notice = request.form.get("notice", "").strip()
    course_id = request.form.get("course_id")
    with get_db() as conn:
        conn.execute("UPDATE courses SET notice = ? WHERE id = ? AND creator_id = ?", (notice, course_id, session["uid"]))
        conn.commit()
    flash("Notice updated for learners.")
    return redirect(url_for("dashboard"))


@app.route("/add_quiz", methods=["POST"])
def add_quiz():
    if not creator_required():
        abort(403)

    course_id = request.form.get("cid")
    question = request.form.get("q", "").strip()
    answer = request.form.get("a", "").strip()
    with get_db() as conn:
        course = conn.execute("SELECT * FROM courses WHERE id = ? AND creator_id = ?", (course_id, session["uid"])).fetchone()
        if not course:
            abort(403)
        conn.execute("INSERT INTO quizes (course_id, question, answer) VALUES (?, ?, ?)", (course_id, question, answer))
        conn.commit()
    flash("Quiz question created.")
    return redirect(url_for("dashboard"))


@app.route("/evaluate_quiz/<int:submission_id>", methods=["POST"])
def evaluate_quiz(submission_id):
    if not creator_required():
        abort(403)

    status = request.form.get("status", "Needs Improvement")
    feedback = request.form.get("feedback", "").strip()
    with get_db() as conn:
        owns_submission = conn.execute(
            """
            SELECT qs.id
            FROM quiz_submissions qs
            JOIN quizes q ON q.id = qs.quiz_id
            JOIN courses c ON c.id = q.course_id
            WHERE qs.id = ? AND c.creator_id = ?
            """,
            (submission_id, session["uid"]),
        ).fetchone()
        if not owns_submission:
            abort(403)
        conn.execute(
            "UPDATE quiz_submissions SET status = ?, feedback = ?, evaluated_at = ? WHERE id = ?",
            (status, feedback, datetime.now().strftime("%Y-%m-%d %H:%M"), submission_id),
        )
        conn.commit()
    flash("Quiz evaluated and feedback sent to learner.")
    return redirect(url_for("dashboard"))


@app.route("/buy/<int:course_id>", methods=["GET", "POST"])
def buy(course_id):
    if not learner_required():
        return redirect(url_for("login"))

    with get_db() as conn:
        course = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        user = current_user(conn)
        already = conn.execute("SELECT * FROM enrollments WHERE user_id = ? AND course_id = ?", (user["id"], course_id)).fetchone()
        method = request.form.get("payment_method", "credits") if request.method == "POST" else "credits"
        if method not in PAYMENT_METHODS:
            method = "credits"

        if already:
            flash("You already unlocked this course.")
        elif not course:
            flash("Course not found.")
        elif method == "credits" and user["balance"] < course["price"]:
            flash("Insufficient wallet credits to unlock this course.")
        else:
            reference = payment_reference(method, user["id"], course_id)
            if method == "credits":
                conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (course["price"], user["id"]))
            conn.execute(
                """
                INSERT INTO enrollments
                (user_id, course_id, paid_amount, payment_method, payment_reference, payment_status, protection_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    course_id,
                    course["price"],
                    method,
                    reference,
                    "Protected Hold",
                    "Course Fee Protection Active",
                ),
            )
            conn.execute(
                """
                INSERT INTO payment_transactions
                (user_id, course_id, amount, method, status, reference, protection_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    course_id,
                    course["price"],
                    PAYMENT_METHODS[method],
                    "Protected Hold",
                    reference,
                    "Refund and learning guarantee protection active",
                ),
            )
            conn.commit()
            flash(f"Course unlocked using {PAYMENT_METHODS[method]}. Reference {reference}. Payment is protected until course completion.")

    return redirect(url_for("dashboard"))


@app.route("/place_bet", methods=["POST"])
def place_bet():
    if not learner_required():
        return redirect(url_for("login"))

    duration = int(request.form.get("duration_days", 0))
    wager = int(request.form.get("wager", 0))
    challenge = STREAK_CHALLENGES.get(duration)
    if not challenge or wager not in ALLOWED_WAGERS:
        flash("Select a valid streak duration and wager.")
        return redirect(url_for("dashboard"))

    with get_db() as conn:
        user = current_user(conn)
        check_streak_bet_status(conn, user["id"])
        active = conn.execute("SELECT id FROM streak_bets WHERE user_id = ? AND status = 'Active'", (user["id"],)).fetchone()
        if active:
            flash("Only one active streak bet is allowed at a time.")
            return redirect(url_for("dashboard"))
        if user["points"] < wager:
            flash("You cannot bet more points than you have.")
            return redirect(url_for("dashboard"))

        start = date.today()
        end = start + timedelta(days=duration - 1)
        conn.execute("UPDATE users SET points = points - ? WHERE id = ?", (wager, user["id"]))
        conn.execute(
            """
            INSERT INTO streak_bets
            (user_id, duration_days, wager, multiplier, start_date, end_date, last_checkin)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user["id"], duration, wager, challenge["multiplier"], start.isoformat(), end.isoformat(), None),
        )
        conn.commit()
        flash(f"Streak bet started: {duration} days, {wager} points at {challenge['multiplier']}x. Complete one module daily.")

    return redirect(url_for("dashboard"))


@app.route("/complete_video/<int:video_id>")
def complete_video(video_id):
    if not learner_required():
        return redirect(url_for("login"))

    with get_db() as conn:
        video = conn.execute(
            """
            SELECT v.* FROM videos v
            JOIN enrollments e ON e.course_id = v.course_id
            WHERE v.id = ? AND e.user_id = ?
            """,
            (video_id, session["uid"]),
        ).fetchone()
        if not video:
            abort(403)
        existing_progress = conn.execute(
            "SELECT completed FROM video_progress WHERE user_id = ? AND video_id = ?",
            (session["uid"], video_id),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO video_progress (user_id, video_id, watched_minutes, completed, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(user_id, video_id) DO UPDATE SET
                watched_minutes = excluded.watched_minutes,
                completed = 1,
                updated_at = excluded.updated_at
            """,
            (session["uid"], video_id, video["minutes_required"], datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
        if not existing_progress or existing_progress["completed"] != 1:
            update_streak_on_module_completion(conn, session["uid"])
        conn.commit()
    flash("Video marked as completed.")
    return redirect(url_for("dashboard"))


@app.route("/mark_attendance/<int:course_id>")
def mark_attendance(course_id):
    if not learner_required():
        return redirect(url_for("login"))

    with get_db() as conn:
        watched = conn.execute(
            """
            SELECT COALESCE(SUM(vp.watched_minutes), 0) AS total_minutes
            FROM video_progress vp
            JOIN videos v ON v.id = vp.video_id
            WHERE vp.user_id = ? AND v.course_id = ?
            """,
            (session["uid"], course_id),
        ).fetchone()["total_minutes"]

        if watched >= 30:
            conn.execute(
                "UPDATE enrollments SET attendance_marked = 1 WHERE user_id = ? AND course_id = ?",
                (session["uid"], course_id),
            )
            conn.commit()
            flash("Attendance marked successfully.")
        else:
            flash("Watch at least 30 minutes of course video before marking attendance.")

    return redirect(url_for("dashboard"))


@app.route("/submit_quiz", methods=["POST"])
def submit_quiz():
    if not learner_required():
        return redirect(url_for("login"))

    quiz_id = request.form.get("qid")
    answer = request.form.get("ans", "").strip()

    with get_db() as conn:
        allowed = conn.execute(
            """
            SELECT q.id FROM quizes q
            JOIN enrollments e ON e.course_id = q.course_id
            WHERE q.id = ? AND e.user_id = ?
            """,
            (quiz_id, session["uid"]),
        ).fetchone()
        if not allowed:
            abort(403)
        conn.execute(
            "INSERT INTO quiz_submissions (user_id, quiz_id, user_answer) VALUES (?, ?, ?)",
            (session["uid"], quiz_id, answer),
        )
        conn.commit()
    flash("Quiz submitted. Your creator will evaluate it.")
    return redirect(url_for("dashboard"))


@app.route("/chatbot", methods=["POST"])
def chatbot():
    if not learner_required():
        return jsonify({"reply": "Please login as a learner to use Sachet HelpBot."}), 401

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"reply": "Please type your question first."})

    reply = ai_chatbot_reply(message)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO ai_chat_messages (user_id, message, reply) VALUES (?, ?, ?)",
            (session["uid"], message, reply),
        )
        conn.commit()
    return jsonify({"reply": reply})


@app.route("/complete_course/<int:course_id>")
def complete_course(course_id):
    if not learner_required():
        return redirect(url_for("login"))

    with get_db() as conn:
        enrollment = conn.execute(
            "SELECT * FROM enrollments WHERE user_id = ? AND course_id = ?",
            (session["uid"], course_id),
        ).fetchone()
        if not enrollment:
            abort(403)

        totals = conn.execute(
            """
            SELECT COUNT(v.id) AS total_videos,
                   COUNT(CASE WHEN vp.completed = 1 THEN 1 END) AS completed_videos
            FROM videos v
            LEFT JOIN video_progress vp ON vp.video_id = v.id AND vp.user_id = ?
            WHERE v.course_id = ?
            """,
            (session["uid"], course_id),
        ).fetchone()

        if totals["total_videos"] == 0 or totals["completed_videos"] < totals["total_videos"]:
            flash("Complete all course videos before generating the certificate.")
            return redirect(url_for("dashboard"))

        if enrollment["attendance_marked"] != 1:
            flash("Mark attendance before completing the course.")
            return redirect(url_for("dashboard"))

        completed_at = date.today().strftime("%d %B %Y")
        conn.execute(
            "UPDATE enrollments SET completed = 1, completed_at = ? WHERE user_id = ? AND course_id = ?",
            (completed_at, session["uid"], course_id),
        )

        if enrollment["payment_released"] != 1:
            course = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
            conn.execute("UPDATE users SET revenue = revenue + ? WHERE id = ?", (enrollment["paid_amount"], course["creator_id"]))
            conn.execute(
                "UPDATE enrollments SET payment_released = 1 WHERE user_id = ? AND course_id = ?",
                (session["uid"], course_id),
            )
            conn.execute(
                "UPDATE payment_transactions SET status = 'Released to Creator', protection_status = 'Learning completed and revenue released' WHERE user_id = ? AND course_id = ?",
                (session["uid"], course_id),
            )
        conn.commit()

    flash("Course completed. Certificate is ready.")
    return redirect(url_for("certificate", course_id=course_id))


@app.route("/certificate/<int:course_id>")
def certificate(course_id):
    if not learner_required():
        return redirect(url_for("login"))

    with get_db() as conn:
        cert = conn.execute(
            """
            SELECT e.user_id, e.course_id, e.completed, e.completed_at, u.username,
                   c.title, creator.username AS creator_name
            FROM enrollments e
            JOIN users u ON u.id = e.user_id
            JOIN courses c ON c.id = e.course_id
            JOIN users creator ON creator.id = c.creator_id
            WHERE e.user_id = ? AND e.course_id = ?
            """,
            (session["uid"], course_id),
        ).fetchone()

        if not cert or cert["completed"] != 1:
            flash("Certificate unlocks after course completion.")
            return redirect(url_for("dashboard"))

    token = certificate_token(cert["user_id"], cert["course_id"], cert["completed_at"])
    verify_url = url_for("verify_certificate", token=token, _external=True)
    return render_template("certificate.html", cert=cert, token=token, verify_url=verify_url)


@app.route("/certificate_pdf/<int:course_id>")
def certificate_pdf(course_id):
    if not learner_required():
        return redirect(url_for("login"))

    with get_db() as conn:
        cert = conn.execute(
            """
            SELECT e.user_id, e.course_id, e.completed, e.completed_at, u.username,
                   c.title, creator.username AS creator_name
            FROM enrollments e
            JOIN users u ON u.id = e.user_id
            JOIN courses c ON c.id = e.course_id
            JOIN users creator ON creator.id = c.creator_id
            WHERE e.user_id = ? AND e.course_id = ?
            """,
            (session["uid"], course_id),
        ).fetchone()

        if not cert or cert["completed"] != 1:
            flash("PDF certificate unlocks after course completion.")
            return redirect(url_for("dashboard"))

    token = certificate_token(cert["user_id"], cert["course_id"], cert["completed_at"])
    verify_url = url_for("verify_certificate", token=token, _external=True)
    pdf_buffer = build_certificate_pdf(cert, verify_url)
    filename = f"sachet_certificate_{cert['username']}_{cert['course_id']}.pdf".replace(" ", "_")
    return send_file(pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=filename)


@app.route("/verify/<token>")
def verify_certificate(token):
    cert = get_certificate_by_token(token)
    return render_template("verify_certificate.html", cert=cert, token=token)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    init_db()
    seed_demo_course()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
