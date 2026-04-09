"""
Модуль для работы с базой данных SQLite.
Хранит цели, этапы, подписки, PRO-доступ и историю действий пользователя.
"""

import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "coach_bot.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            timezone_offset INTEGER DEFAULT 3,
            trial_started_at TEXT,
            subscription_active INTEGER DEFAULT 0,
            subscription_expires_at TEXT
        );

        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            deadline TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            deadline TEXT NOT NULL,
            completed_at TEXT,
            status TEXT DEFAULT 'pending',
            order_num INTEGER DEFAULT 0,
            FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            goal_id INTEGER,
            milestone_id INTEGER,
            remind_at TEXT NOT NULL,
            message TEXT,
            sent INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE,
            FOREIGN KEY (milestone_id) REFERENCES milestones(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_payment_charge_id TEXT NOT NULL,
            provider_payment_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT DEFAULT 'XTR',
            payload TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS user_profile (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            birth_date TEXT,
            birth_city TEXT,
            birth_time TEXT,
            face_photo_id TEXT,
            right_palm_photo_id TEXT,
            left_palm_photo_id TEXT,
            analysis_result TEXT,
            analysis_done_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            morning_hour INTEGER DEFAULT 9,
            evening_hour INTEGER DEFAULT 20,
            morning_enabled INTEGER DEFAULT 1,
            evening_enabled INTEGER DEFAULT 1,
            deadline_mode TEXT DEFAULT 'both',
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS pro_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            telegram_payment_charge_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS coach_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            used_date TEXT NOT NULL,
            messages_count INTEGER DEFAULT 0,
            total_minutes INTEGER DEFAULT 0,
            UNIQUE(user_id, used_date),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS coach_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS coach_questionnaire (
            user_id INTEGER PRIMARY KEY,
            business_area TEXT,
            experience_years TEXT,
            main_challenge TEXT,
            team_size TEXT,
            annual_revenue TEXT,
            completed_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
    """)
    conn.commit()
    conn.close()


# --- Пользователи ---

def ensure_user(user_id: int, username: str = None, first_name: str = None):
    conn = get_connection()
    existing = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not existing:
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO users (user_id, username, first_name, trial_started_at) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, now)
        )
        conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return user


# --- Подписка ---

TRIAL_DAYS = 30


def get_subscription_status(user_id: int) -> dict:
    user = get_user(user_id)
    if not user:
        return {"status": "expired", "days_left": 0, "expires_at": None}

    now = datetime.utcnow()

    if user["subscription_active"] and user["subscription_expires_at"]:
        expires = datetime.fromisoformat(user["subscription_expires_at"])
        if expires > now:
            days_left = (expires.date() - now.date()).days
            return {
                "status": "active",
                "days_left": days_left,
                "expires_at": expires.strftime("%d.%m.%Y"),
            }

    if user["trial_started_at"]:
        trial_start = datetime.fromisoformat(user["trial_started_at"])
        trial_end = trial_start + timedelta(days=TRIAL_DAYS)
        if trial_end > now:
            days_left = (trial_end.date() - now.date()).days
            return {
                "status": "trial",
                "days_left": days_left,
                "expires_at": trial_end.strftime("%d.%m.%Y"),
            }

    return {"status": "expired", "days_left": 0, "expires_at": None}


def is_subscription_valid(user_id: int) -> bool:
    status = get_subscription_status(user_id)
    return status["status"] in ("trial", "active")


def activate_subscription(user_id: int, days: int = 30):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    now = datetime.utcnow()

    if user and user["subscription_active"] and user["subscription_expires_at"]:
        current_expires = datetime.fromisoformat(user["subscription_expires_at"])
        if current_expires > now:
            new_expires = current_expires + timedelta(days=days)
        else:
            new_expires = now + timedelta(days=days)
    else:
        new_expires = now + timedelta(days=days)

    conn.execute(
        "UPDATE users SET subscription_active = 1, subscription_expires_at = ? WHERE user_id = ?",
        (new_expires.isoformat(), user_id)
    )
    conn.commit()
    conn.close()


def save_payment(user_id: int, telegram_charge_id: str, provider_charge_id: str,
                 amount: int, currency: str, payload: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO payments (user_id, telegram_payment_charge_id, provider_payment_charge_id, amount, currency, payload) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, telegram_charge_id, provider_charge_id, amount, currency, payload)
    )
    conn.execute(
        "INSERT INTO activity_log (user_id, action, details) VALUES (?, ?, ?)",
        (user_id, "payment", f"Оплата: {amount} {currency}")
    )
    conn.commit()
    conn.close()


# --- PRO-доступ ---

# === 3 тарифа (1 Star ≈ $0.02) ===
# LITE:    $7/мес  = 350 Stars  — безлимит коуч + персональные звёзды
# PRO:     $15/мес = 750 Stars  — + профиль + профайлинг + стратегия роста
# PREMIUM: $29/мес = 1450 Stars — + еженедельные разборы + приоритетная поддержка
LITE_PRICE_STARS = int(os.getenv("LITE_PRICE_STARS", "350"))
PRO_PRICE_STARS = int(os.getenv("PRO_PRICE_STARS", "750"))
PRO_SUB_PRICE_STARS = int(os.getenv("PRO_SUB_PRICE_STARS", "1450"))


def get_user_tier(user_id: int) -> str:
    """Текущий тариф пользователя: 'free', 'lite', 'pro', 'premium'."""
    conn = get_connection()
    row = conn.execute(
        "SELECT product FROM pro_purchases WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return "free"
    p = row["product"]
    if p == "premium_sub" and is_subscription_valid(user_id):
        return "premium"
    if p in ("pro_sub", "pro_bundle") and is_subscription_valid(user_id):
        return "pro"
    if p == "lite_sub" and is_subscription_valid(user_id):
        return "lite"
    # Подписка истекла
    return "free"


def has_pro_access(user_id: int) -> bool:
    """Есть ли PRO или выше (профиль, профайлинг, стратегия)."""
    return get_user_tier(user_id) in ("pro", "premium")


def has_lite_access(user_id: int) -> bool:
    """Есть ли LITE или выше (безлимит коуч, звёзды)."""
    return get_user_tier(user_id) in ("lite", "pro", "premium")


def has_premium_access(user_id: int) -> bool:
    """Есть ли PREMIUM (разборы + приоритет)."""
    return get_user_tier(user_id) == "premium"


def has_pro_subscription(user_id: int) -> bool:
    """Любая активная подписка (LITE/PRO/PREMIUM)."""
    return get_user_tier(user_id) != "free"


def save_pro_purchase(user_id: int, product: str, telegram_charge_id: str, amount: int):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO pro_purchases (user_id, product, telegram_payment_charge_id, amount) VALUES (?, ?, ?, ?)",
        (user_id, product, telegram_charge_id, amount)
    )
    conn.execute(
        "INSERT INTO activity_log (user_id, action, details) VALUES (?, ?, ?)",
        (user_id, "pro_purchase", f"PRO куплен: {product}")
    )
    conn.commit()
    conn.close()


# --- Коуч-лимит ---

FREE_COACH_MESSAGES_PER_DAY = 20


def get_coach_messages_today(user_id: int) -> int:
    """Сколько сообщений коучу отправлено сегодня."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn = get_connection()
    row = conn.execute(
        "SELECT messages_count FROM coach_usage WHERE user_id = ? AND used_date = ?",
        (user_id, today)
    ).fetchone()
    conn.close()
    return row["messages_count"] if row else 0


def increment_coach_messages(user_id: int):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn = get_connection()
    conn.execute("""
        INSERT INTO coach_usage (user_id, used_date, messages_count)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, used_date) DO UPDATE SET messages_count = messages_count + 1
    """, (user_id, today))
    conn.commit()
    conn.close()


def can_use_coach_free(user_id: int) -> bool:
    return get_coach_messages_today(user_id) < FREE_COACH_MESSAGES_PER_DAY


def get_coach_history(user_id: int, limit: int = 10) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content FROM coach_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_coach_message(user_id: int, role: str, content: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO coach_history (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    # Храним только последние 50 сообщений на пользователя
    conn.execute("""
        DELETE FROM coach_history WHERE user_id = ? AND id NOT IN (
            SELECT id FROM coach_history WHERE user_id = ? ORDER BY created_at DESC LIMIT 50
        )
    """, (user_id, user_id))
    conn.commit()
    conn.close()


# --- Цели ---

def create_goal(user_id: int, title: str, description: str, deadline: str) -> int:
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO goals (user_id, title, description, deadline) VALUES (?, ?, ?, ?)",
        (user_id, title, description, deadline)
    )
    goal_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO activity_log (user_id, action, details) VALUES (?, ?, ?)",
        (user_id, "goal_created", f"Цель: {title}")
    )
    conn.commit()
    conn.close()
    return goal_id


def get_active_goals(user_id: int):
    conn = get_connection()
    goals = conn.execute(
        "SELECT * FROM goals WHERE user_id = ? AND status = 'active' ORDER BY deadline",
        (user_id,)
    ).fetchall()
    conn.close()
    return goals


def get_goal(goal_id: int):
    conn = get_connection()
    goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    conn.close()
    return goal


def complete_goal(goal_id: int):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE goals SET status = 'completed', completed_at = ? WHERE id = ?",
        (now, goal_id)
    )
    goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if goal:
        conn.execute(
            "INSERT INTO activity_log (user_id, action, details) VALUES (?, ?, ?)",
            (goal["user_id"], "goal_completed", f"Цель выполнена: {goal['title']}")
        )
    conn.commit()
    conn.close()


def cancel_goal(goal_id: int):
    conn = get_connection()
    conn.execute("UPDATE goals SET status = 'cancelled' WHERE id = ?", (goal_id,))
    conn.commit()
    conn.close()


def get_all_goals(user_id: int):
    conn = get_connection()
    goals = conn.execute(
        "SELECT * FROM goals WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return goals


# --- Этапы ---

def create_milestone(goal_id: int, title: str, deadline: str, order_num: int) -> int:
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO milestones (goal_id, title, deadline, order_num) VALUES (?, ?, ?, ?)",
        (goal_id, title, deadline, order_num)
    )
    milestone_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return milestone_id


def get_milestones(goal_id: int):
    conn = get_connection()
    milestones = conn.execute(
        "SELECT * FROM milestones WHERE goal_id = ? ORDER BY order_num",
        (goal_id,)
    ).fetchall()
    conn.close()
    return milestones


def complete_milestone(milestone_id: int):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE milestones SET status = 'completed', completed_at = ? WHERE id = ?",
        (now, milestone_id)
    )
    ms = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    if ms:
        goal = conn.execute("SELECT * FROM goals WHERE id = ?", (ms["goal_id"],)).fetchone()
        if goal:
            conn.execute(
                "INSERT INTO activity_log (user_id, action, details) VALUES (?, ?, ?)",
                (goal["user_id"], "milestone_completed", f"Этап выполнен: {ms['title']}")
            )
    conn.commit()
    conn.close()


def rename_milestone(milestone_id: int, new_title: str):
    conn = get_connection()
    conn.execute(
        "UPDATE milestones SET title = ? WHERE id = ?",
        (new_title, milestone_id)
    )
    conn.commit()
    conn.close()


def delete_milestone(milestone_id: int):
    conn = get_connection()
    ms = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    if ms:
        conn.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
        # Пересчитываем order_num для оставшихся этапов
        remaining = conn.execute(
            "SELECT id FROM milestones WHERE goal_id = ? ORDER BY order_num",
            (ms["goal_id"],)
        ).fetchall()
        for i, m in enumerate(remaining):
            conn.execute("UPDATE milestones SET order_num = ? WHERE id = ?", (i + 1, m["id"]))
    conn.commit()
    conn.close()


def add_milestone_to_goal(goal_id: int, title: str) -> int:
    conn = get_connection()
    # Определяем следующий order_num
    max_order = conn.execute(
        "SELECT MAX(order_num) as mx FROM milestones WHERE goal_id = ?",
        (goal_id,)
    ).fetchone()["mx"] or 0
    # Берём дедлайн цели как дедлайн нового этапа
    goal = conn.execute("SELECT deadline FROM goals WHERE id = ?", (goal_id,)).fetchone()
    deadline = goal["deadline"] if goal else ""
    cursor = conn.execute(
        "INSERT INTO milestones (goal_id, title, deadline, order_num) VALUES (?, ?, ?, ?)",
        (goal_id, title, deadline, max_order + 1)
    )
    milestone_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return milestone_id


def update_goal_deadline(goal_id: int, new_deadline: str):
    conn = get_connection()
    conn.execute(
        "UPDATE goals SET deadline = ? WHERE id = ?",
        (new_deadline, goal_id)
    )
    conn.commit()
    conn.close()


def get_milestone(milestone_id: int):
    conn = get_connection()
    ms = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    conn.close()
    return ms


def get_pending_milestones_for_user(user_id: int):
    conn = get_connection()
    milestones = conn.execute("""
        SELECT m.*, g.title as goal_title, g.user_id
        FROM milestones m
        JOIN goals g ON m.goal_id = g.id
        WHERE g.user_id = ? AND g.status = 'active' AND m.status = 'pending'
        ORDER BY m.deadline
    """, (user_id,)).fetchall()
    conn.close()
    return milestones


# --- Напоминания ---

def create_reminder(user_id: int, goal_id: int, milestone_id: int, remind_at: str, message: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO reminders (user_id, goal_id, milestone_id, remind_at, message) VALUES (?, ?, ?, ?, ?)",
        (user_id, goal_id, milestone_id, remind_at, message)
    )
    conn.commit()
    conn.close()


def get_pending_reminders():
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    reminders = conn.execute(
        "SELECT * FROM reminders WHERE sent = 0 AND remind_at <= ?",
        (now,)
    ).fetchall()
    conn.close()
    return reminders


def mark_reminder_sent(reminder_id: int):
    conn = get_connection()
    conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()


# --- Профиль пользователя ---

def get_user_profile(user_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM user_profile WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_user_profile(user_id: int, **kwargs):
    existing = get_user_profile(user_id)
    if not existing:
        conn = get_connection()
        conn.execute("INSERT OR IGNORE INTO user_profile (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
    if not kwargs:
        return
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    conn = get_connection()
    conn.execute(f"UPDATE user_profile SET {fields} WHERE user_id = ?", values)
    conn.commit()
    conn.close()


def profile_is_complete(user_id: int) -> bool:
    p = get_user_profile(user_id)
    if not p:
        return False
    return all([
        p.get("full_name"),
        p.get("birth_date"),
        p.get("birth_city"),
        p.get("face_photo_id"),
        p.get("right_palm_photo_id"),
        p.get("left_palm_photo_id"),
    ])


def profile_analysis_done(user_id: int) -> bool:
    p = get_user_profile(user_id)
    return bool(p and p.get("analysis_result"))


# --- Настройки пользователя ---

DEFAULT_SETTINGS = {
    "morning_hour": 9,
    "evening_hour": 20,
    "morning_enabled": 1,
    "evening_enabled": 1,
    "deadline_mode": "both",
}


def get_user_settings(user_id: int) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"user_id": user_id, **DEFAULT_SETTINGS}


def save_user_settings(user_id: int, **kwargs):
    current = get_user_settings(user_id)
    current.update(kwargs)
    conn = get_connection()
    conn.execute("""
        INSERT INTO user_settings (user_id, morning_hour, evening_hour,
            morning_enabled, evening_enabled, deadline_mode)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            morning_hour = excluded.morning_hour,
            evening_hour = excluded.evening_hour,
            morning_enabled = excluded.morning_enabled,
            evening_enabled = excluded.evening_enabled,
            deadline_mode = excluded.deadline_mode
    """, (
        user_id,
        current["morning_hour"],
        current["evening_hour"],
        current["morning_enabled"],
        current["evening_enabled"],
        current["deadline_mode"],
    ))
    conn.commit()
    conn.close()


# --- Статистика ---

def get_user_stats(user_id: int):
    conn = get_connection()

    total_goals = conn.execute(
        "SELECT COUNT(*) as cnt FROM goals WHERE user_id = ?", (user_id,)
    ).fetchone()["cnt"]

    completed_goals = conn.execute(
        "SELECT COUNT(*) as cnt FROM goals WHERE user_id = ? AND status = 'completed'", (user_id,)
    ).fetchone()["cnt"]

    active_goals = conn.execute(
        "SELECT COUNT(*) as cnt FROM goals WHERE user_id = ? AND status = 'active'", (user_id,)
    ).fetchone()["cnt"]

    total_milestones = conn.execute("""
        SELECT COUNT(*) as cnt FROM milestones m
        JOIN goals g ON m.goal_id = g.id
        WHERE g.user_id = ?
    """, (user_id,)).fetchone()["cnt"]

    completed_milestones = conn.execute("""
        SELECT COUNT(*) as cnt FROM milestones m
        JOIN goals g ON m.goal_id = g.id
        WHERE g.user_id = ? AND m.status = 'completed'
    """, (user_id,)).fetchone()["cnt"]

    recent_days = conn.execute("""
        SELECT DISTINCT date(created_at) as day
        FROM activity_log
        WHERE user_id = ?
        ORDER BY day DESC
        LIMIT 30
    """, (user_id,)).fetchall()

    streak = 0
    today = datetime.now().date()
    for i, row in enumerate(recent_days):
        day = datetime.strptime(row["day"], "%Y-%m-%d").date()
        expected = today - timedelta(days=i)
        if day == expected:
            streak += 1
        else:
            break

    conn.close()
    return {
        "total_goals": total_goals,
        "completed_goals": completed_goals,
        "active_goals": active_goals,
        "total_milestones": total_milestones,
        "completed_milestones": completed_milestones,
        "streak": streak,
    }


# --- Анкета коуча ---

def has_coach_questionnaire(user_id: int) -> bool:
    """Прошёл ли пользователь анкету перед коучем."""
    conn = get_connection()
    row = conn.execute(
        "SELECT user_id FROM coach_questionnaire WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return row is not None


def save_coach_questionnaire(user_id: int, business_area: str, experience_years: str,
                              main_challenge: str, team_size: str, annual_revenue: str):
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO coach_questionnaire
        (user_id, business_area, experience_years, main_challenge, team_size, annual_revenue)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, business_area, experience_years, main_challenge, team_size, annual_revenue))
    conn.commit()
    conn.close()


def get_coach_questionnaire(user_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM coach_questionnaire WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None
