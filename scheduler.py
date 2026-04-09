"""
Планировщик напоминаний.
Проверяет БД каждую минуту и отправляет напоминания пользователям.
Учитывает персональные настройки каждого пользователя:
  - время утренних/вечерних сообщений
  - режим уведомлений о дедлайне: 'smart' (20%), 'fixed' (7/3/1 дней), 'both'
"""

import asyncio
import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardMarkup, InlineKeyboardButton

import database as db
import motivation as mot

logger = logging.getLogger(__name__)


async def check_and_send_reminders(bot):
    """Проверить и отправить все просроченные напоминания из таблицы reminders."""
    reminders = db.get_pending_reminders()

    for r in reminders:
        try:
            if not db.is_subscription_valid(r["user_id"]):
                db.mark_reminder_sent(r["id"])
                continue

            await bot.send_message(chat_id=r["user_id"], text=r["message"])
            db.mark_reminder_sent(r["id"])
            logger.info(f"Reminder sent: user={r['user_id']}, id={r['id']}")
        except Exception as e:
            logger.error(f"Failed to send reminder {r['id']}: {e}")
            db.mark_reminder_sent(r["id"])


def _should_notify_deadline(mode: str, total_days: int, days_left: int) -> str | None:
    """
    Вернуть тип уведомления или None если уведомлять не надо.
    Возвращает: '20pct' | '7d' | '3d' | '1d' | None
    """
    # Режим «умный» — 20% оставшегося времени
    smart_trigger = max(1, round(total_days * 0.20))

    if mode in ("smart", "both") and days_left == smart_trigger:
        return "20pct"

    if mode in ("fixed", "both"):
        if days_left == 7:
            return "7d"
        if days_left == 3:
            return "3d"
        if days_left == 1:
            return "1d"

    return None


async def check_deadline_reminders(bot):
    """
    Проверить дедлайны целей и этапов, отправить уведомления
    согласно персональным настройкам каждого пользователя.
    """
    conn = db.get_connection()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()

    now = datetime.utcnow().date()

    for user in users:
        user_id = user["user_id"]
        try:
            if not db.is_subscription_valid(user_id):
                continue

            settings = db.get_user_settings(user_id)
            mode = settings.get("deadline_mode", "both")

            # --- Проверка этапов ---
            milestones = db.get_pending_milestones_for_user(user_id)
            for ms in milestones:
                deadline = datetime.fromisoformat(ms["deadline"]).date()
                days_left = (deadline - now).days

                if days_left < 0:
                    # Просрочен — разовое уведомление (если ещё не отправляли сегодня)
                    await _send_if_not_sent_today(
                        bot, user_id,
                        key=f"overdue_ms_{ms['id']}",
                        text=(
                            f"{mot.get_deadline_message(-1)}\n\n"
                            f"📌 Этап: *{ms['title']}*\n"
                            f"🎯 Цель: {ms['goal_title']}\n"
                            f"Срок был: {deadline.strftime('%d.%m.%Y')}\n\n"
                            "Нажми ✅ Отметить прогресс в меню"
                        )
                    )
                    continue

                if days_left == 0:
                    await _send_if_not_sent_today(
                        bot, user_id,
                        key=f"today_ms_{ms['id']}",
                        text=(
                            f"{mot.get_deadline_message(0)}\n\n"
                            f"📌 Этап: *{ms['title']}*\n"
                            f"🎯 Цель: {ms['goal_title']}\n\n"
                            "Нажми ✅ Отметить прогресс в меню"
                        )
                    )
                    continue

                # Считаем общую длину от создания цели
                goal = db.get_goal(ms["goal_id"])
                if goal:
                    created = datetime.fromisoformat(goal["created_at"]).date()
                    ms_deadline = datetime.fromisoformat(ms["deadline"]).date()
                    total_days = max(1, (ms_deadline - created).days)
                else:
                    total_days = max(1, days_left)

                notify_type = _should_notify_deadline(mode, total_days, days_left)
                if notify_type:
                    await _send_deadline_notification(
                        bot, user_id, ms["title"], ms["goal_title"],
                        deadline, days_left, total_days, notify_type
                    )

            # --- Проверка финальных дедлайнов целей ---
            goals = db.get_active_goals(user_id)
            for goal in goals:
                goal_deadline = datetime.fromisoformat(goal["deadline"]).date()
                days_left = (goal_deadline - now).days

                if days_left < 0 or days_left == 0:
                    continue  # этапы уже охватывают эти случаи

                created = datetime.fromisoformat(goal["created_at"]).date()
                total_days = max(1, (goal_deadline - created).days)

                notify_type = _should_notify_deadline(mode, total_days, days_left)
                if notify_type:
                    text = _build_goal_deadline_text(
                        goal["title"], goal_deadline, days_left, total_days, notify_type
                    )
                    if text:
                        await _send_if_not_sent_today(
                            bot, user_id,
                            key=f"goal_deadline_{goal['id']}_{notify_type}",
                            text=text
                        )

        except Exception as e:
            logger.error(f"Deadline check error for user {user_id}: {e}")


async def _send_deadline_notification(
    bot, user_id, ms_title, goal_title,
    deadline, days_left, total_days, notify_type
):
    pct_left = round(days_left / total_days * 100)
    if notify_type == "20pct":
        header = mot.get_deadline_20pct_message()
        detail = f"⏳ Осталось ~{days_left} дн. ({pct_left}% от срока)"
    elif notify_type == "7d":
        header = "⏰ Через неделю дедлайн! Пора поднажать!"
        detail = f"⏳ Осталось 7 дней"
    elif notify_type == "3d":
        header = "🔥 До дедлайна 3 дня! Финальный рывок!"
        detail = f"⏳ Осталось 3 дня"
    elif notify_type == "1d":
        header = "🚨 Завтра дедлайн! Ты справишься!"
        detail = f"⏳ Остался 1 день"
    else:
        return

    text = (
        f"{header}\n\n"
        f"📌 Этап: *{ms_title}*\n"
        f"🎯 Цель: {goal_title}\n"
        f"{detail} ({deadline.strftime('%d.%m.%Y')})\n\n"
        "Нажми ✅ Отметить прогресс в меню"
    )
    await _send_if_not_sent_today(
        bot, user_id,
        key=f"ms_notify_{ms_title[:20]}_{notify_type}",
        text=text
    )


def _build_goal_deadline_text(goal_title, deadline, days_left, total_days, notify_type):
    pct_left = round(days_left / total_days * 100)
    if notify_type == "20pct":
        header = mot.get_deadline_20pct_message()
        detail = f"⏳ До финала: ~{days_left} дн. ({pct_left}% от срока)"
    elif notify_type == "7d":
        header = "⏰ До завершения цели — неделя!"
        detail = "⏳ Осталось 7 дней"
    elif notify_type == "3d":
        header = "🔥 До дедлайна цели — 3 дня!"
        detail = "⏳ Осталось 3 дня"
    elif notify_type == "1d":
        header = "🚨 Завтра дедлайн цели! Собери все силы!"
        detail = "⏳ Остался 1 день"
    else:
        return None

    return (
        f"{header}\n\n"
        f"🎯 Цель: *{goal_title}*\n"
        f"{detail} ({deadline.strftime('%d.%m.%Y')})\n\n"
        "Нажми 🎯 Мои цели в меню"
    )


# Временное хранилище для дедупликации «уже отправлено сегодня»
_sent_today: dict[str, str] = {}


async def _send_if_not_sent_today(bot, user_id: int, key: str, text: str):
    """Отправить сообщение не более одного раза в сутки по ключу."""
    today = datetime.utcnow().date().isoformat()
    full_key = f"{user_id}:{key}:{today}"
    if full_key in _sent_today:
        return
    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        _sent_today[full_key] = today
        logger.info(f"Deadline notify sent: user={user_id}, key={key}")
    except Exception as e:
        logger.error(f"Failed to send deadline notify to {user_id}: {e}")


async def send_daily_digests(bot):
    """
    Отправить утренний/вечерний дайджест пользователям
    в их персональное время (из user_settings).
    """
    conn = db.get_connection()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()

    now_utc = datetime.utcnow()

    for user in users:
        user_id = user["user_id"]
        try:
            if not db.is_subscription_valid(user_id):
                continue

            settings = db.get_user_settings(user_id)
            tz_offset = user["timezone_offset"] or 3
            local_hour = (now_utc.hour + tz_offset) % 24
            local_minute = now_utc.minute

            # Утренний дайджест
            if (
                settings.get("morning_enabled", 1)
                and local_hour == settings.get("morning_hour", 9)
                and local_minute < 5
            ):
                goals = db.get_active_goals(user_id)
                if goals:
                    pending = db.get_pending_milestones_for_user(user_id)
                    today = datetime.utcnow().date()
                    today_count = sum(
                        1 for m in pending
                        if datetime.fromisoformat(m["deadline"]).date() == today
                    )
                    overdue_count = sum(
                        1 for m in pending
                        if datetime.fromisoformat(m["deadline"]).date() < today
                    )
                    text = mot.get_morning_motivation() + "\n\n"
                    if overdue_count > 0:
                        text += f"⚠️ Просроченных этапов: {overdue_count}\n"
                    if today_count > 0:
                        text += f"🔥 Сегодня дедлайн у {today_count} этапов\n"
                    text += f"📋 Активных целей: {len(goals)}\n"
                    text += f"\n_{mot.get_random_quote()}_"
                    morning_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("⚡ Фокус на сегодня", callback_data="quick_focus")],
                        [InlineKeyboardButton("🔥 Ещё мотивацию", callback_data="quick_motivation")],
                    ])
                    await bot.send_message(
                        chat_id=user_id, text=text,
                        parse_mode="Markdown",
                        reply_markup=morning_kb
                    )
                    logger.info(f"Morning digest sent to user {user_id}")

            # Вечерний дайджест
            elif (
                settings.get("evening_enabled", 1)
                and local_hour == settings.get("evening_hour", 20)
                and local_minute < 5
            ):
                goals = db.get_active_goals(user_id)
                if goals:
                    stats = db.get_user_stats(user_id)
                    text = mot.get_evening_check() + "\n\n"
                    if stats["streak"] > 1:
                        text += f"🔥 Серия: {stats['streak']} дней подряд!\n"
                    evening_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Отметить прогресс", callback_data="quick_focus")],
                        [InlineKeyboardButton("🔥 Мотивация", callback_data="quick_motivation")],
                    ])
                    await bot.send_message(
                        chat_id=user_id, text=text,
                        parse_mode="Markdown",
                        reply_markup=evening_kb
                    )
                    logger.info(f"Evening digest sent to user {user_id}")

        except Exception as e:
            logger.error(f"Digest error for user {user_id}: {e}")


async def check_expiring_subscriptions(bot):
    """Предупредить пользователей об истекающей подписке (за 3 дня и за 1 день)."""
    conn = db.get_connection()
    users = conn.execute(
        "SELECT * FROM users WHERE subscription_active = 1 AND subscription_expires_at IS NOT NULL"
    ).fetchall()
    conn.close()

    now = datetime.utcnow()

    for user in users:
        try:
            expires = datetime.fromisoformat(user["subscription_expires_at"])
            days_left = (expires.date() - now.date()).days

            pro_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👑 Продлить PRO-подписку", callback_data="pro_sub_buy")],
            ])
            if days_left == 3:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=(
                        "⏰ Подписка заканчивается через 3 дня!\n\n"
                        "Продли сейчас, чтобы не потерять доступ к коучу и напоминаниям."
                    ),
                    reply_markup=pro_kb
                )
            elif days_left == 1:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=(
                        "🚨 Подписка заканчивается ЗАВТРА!\n\n"
                        "Не теряй свой прогресс — продли подписку!"
                    ),
                    reply_markup=pro_kb
                )
            elif days_left == 0:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=(
                        "😔 Подписка закончилась сегодня.\n\n"
                        "Все твои цели сохранены и ждут тебя!"
                    ),
                    reply_markup=pro_kb
                )
        except Exception as e:
            logger.error(f"Subscription check error for user {user['user_id']}: {e}")


async def check_expiring_trials(bot):
    """Предупредить пользователей об окончании trial периода."""
    conn = db.get_connection()
    users = conn.execute(
        "SELECT * FROM users WHERE trial_started_at IS NOT NULL AND subscription_active = 0"
    ).fetchall()
    conn.close()

    now = datetime.utcnow()

    for user in users:
        try:
            trial_start = datetime.fromisoformat(user["trial_started_at"])
            trial_end = trial_start + timedelta(days=db.TRIAL_DAYS)
            days_left = (trial_end.date() - now.date()).days

            trial_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 PRO-доступ — 950 Stars", callback_data="pro_buy")],
                [InlineKeyboardButton("👑 PRO-подписка — 1450 Stars/мес", callback_data="pro_sub_buy")],
            ])
            if days_left == 7:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=(
                        "📢 Уже 3 недели вместе! 🎉\n\n"
                        "Ты отлично двигаешься к своим целям! "
                        "Через 7 дней закончится бесплатный период.\n\n"
                        "Хочешь продолжить без ограничений?"
                    ),
                    reply_markup=trial_kb
                )
            elif days_left == 3:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=(
                        "⏰ До конца бесплатного периода осталось 3 дня!"
                    ),
                    reply_markup=trial_kb
                )
            elif days_left == 1:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=(
                        "🚨 Завтра заканчивается бесплатный период!\n\n"
                        "Не упусти момент — PRO откроет полный доступ!"
                    ),
                    reply_markup=trial_kb
                )
        except Exception as e:
            logger.error(f"Trial check error for user {user['user_id']}: {e}")


async def scheduler_loop(bot):
    """Основной цикл планировщика. Запускается каждую минуту."""
    logger.info("Scheduler started")

    while True:
        try:
            # Напоминания из таблицы reminders
            await check_and_send_reminders(bot)

            now = datetime.utcnow()

            # Дедлайн-уведомления — раз в час (в :00)
            if now.minute == 0:
                await check_deadline_reminders(bot)

            # Утренние/вечерние дайджесты — проверяем каждые 5 минут
            if now.minute < 5:
                await send_daily_digests(bot)

            # Проверка подписок — раз в час (в :00)
            if now.minute == 0:
                await check_expiring_subscriptions(bot)
                await check_expiring_trials(bot)

        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")

        await asyncio.sleep(60)
