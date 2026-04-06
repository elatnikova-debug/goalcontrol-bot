"""
Коуч-Трекер Telegram Bot
Главный файл с обработчиками команд и Conversation Handlers.
Включает монетизацию через Telegram Stars.
"""

import os
import logging
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, PreCheckoutQueryHandler, filters, ContextTypes
)

import database as db
import motivation as mot
from analyze_handler import build_analyze_conversation

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Настройки подписки ---
# Цена подписки в Telegram Stars (1 Star ≈ $0.02, так что 250 Stars ≈ $5)
# Можно изменить через переменную окружения
SUBSCRIPTION_PRICE_STARS = int(os.getenv("SUBSCRIPTION_PRICE_STARS", "250"))
SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))

# Состояния для ConversationHandler по созданию цели
(
    GOAL_TITLE,
    GOAL_DESCRIPTION,
    GOAL_DEADLINE,
    GOAL_MILESTONES_COUNT,
    GOAL_MILESTONE_TITLE,
    GOAL_CONFIRM,
) = range(6)

# Временное хранилище данных в процессе создания цели
creating_goals = {}


# ========================
# Декоратор проверки подписки
# ========================
def require_subscription(func):
    """Декоратор: проверяет подписку перед выполнением команды.
    В течение trial-периода (30 дней) — всё работает без ограничений и без упоминания оплаты.
    После окончания trial — предлагает подписку.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        db.ensure_user(user_id, update.effective_user.username, update.effective_user.first_name)

        if not db.is_subscription_valid(user_id):
            await update.message.reply_text(
                "😔 Твой бесплатный период закончился.\n\n"
                "Все твои цели сохранены и ждут тебя!\n"
                "Продолжи путь к своим целям — оформи подписку:\n\n"
                f"⭐ {SUBSCRIPTION_PRICE_STARS} Stars в месяц (≈$5)\n\n"
                "Что входит:\n"
                "✅ Неограниченные цели и этапы\n"
                "✅ Персональные напоминания по твоему расписанию\n"
                "✅ Анализ личности и персонализированные советы\n"
                "✅ Мотивационный коуч 24/7\n\n"
                "Жми /subscribe чтобы продолжить!"
            )
            return
        return await func(update, context)
    return wrapper


# ========================
# /start — приветствие
# ========================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username, user.first_name)
    name = user.first_name or "друг"

    sub = db.get_subscription_status(user.id)

    if sub["status"] == "trial":
        sub_line = f"\n🎁 Бесплатный период: ещё {sub['days_left']} дней (до {sub['expires_at']})\n"
    elif sub["status"] == "active":
        sub_line = f"\n⭐ Подписка активна до {sub['expires_at']}\n"
    else:
        sub_line = "\n😔 Подписка неактивна. Жми /subscribe\n"

    text = (
        f"{mot.get_greeting()}\n\n"
        f"Привет, {name}! Я — твой персональный коуч-трекер 🎯\n"
        f"{sub_line}\n"
        "Вот что я умею:\n"
        "📌 /newgoal — создать новую цель\n"
        "📋 /goals — посмотреть мои цели\n"
        "✅ /done — отметить этап выполненным\n"
        "📊 /stats — моя статистика\n"
        "💪 /motivate — мотивация прямо сейчас\n"
        "📅 /today — план на сегодня\n"
        "⭐ /subscribe — подписка\n"
        "💳 /mystatus — статус подписки\n"
        "⚙️ /settings — настройки напоминаний\n"
        "🔮 /analyze — анализ личности\n"
        "❓ /help — все команды\n\n"
        "Давай начнём! Какую цель хочешь поставить? Жми /newgoal"
    )
    await update.message.reply_text(text)


# ========================
# /help
# ========================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎯 *Мои команды:*\n\n"
        "*Цели:*\n"
        "📌 /newgoal — создать новую цель\n"
        "📋 /goals — все активные цели\n"
        "🔍 /goal\\_ID — подробности по цели\n"
        "✅ /done — отметить этап выполненным\n"
        "🏆 /complete — завершить цель\n"
        "❌ /cancel\\_goal — отменить цель\n"
        "📅 /today — план на сегодня\n\n"
        "*Мотивация:*\n"
        "💪 /motivate — порция мотивации\n"
        "📊 /stats — статистика и прогресс\n\n"
        "*Подписка:*\n"
        "⭐ /subscribe — оформить подписку\n"
        "💳 /mystatus — статус подписки\n"
        "⚙️ /settings — настройки напоминаний\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ========================
# /subscribe — оформить подписку
# ========================
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id, update.effective_user.username, update.effective_user.first_name)

    sub = db.get_subscription_status(user_id)

    if sub["status"] == "trial":
        intro = (
            f"🎁 У тебя ещё {sub['days_left']} дней бесплатного периода!\n"
            "Но ты можешь оформить подписку заранее — она начнётся после окончания trial.\n\n"
        )
    elif sub["status"] == "active":
        intro = (
            f"⭐ Твоя подписка активна до {sub['expires_at']}.\n"
            "Можешь продлить — дни добавятся к текущему сроку!\n\n"
        )
    else:
        intro = "Бесплатный период закончился. Оформи подписку, чтобы продолжить!\n\n"

    text = (
        f"{intro}"
        f"💰 *Подписка Коуч-Трекер*\n\n"
        f"Цена: {SUBSCRIPTION_PRICE_STARS} ⭐ Stars / месяц\n"
        f"(≈ $5)\n\n"
        "Что входит:\n"
        "✅ Неограниченные цели и этапы\n"
        "✅ Ежедневные напоминания о дедлайнах\n"
        "✅ Мотивационный коуч 24/7\n"
        "✅ Трекинг прогресса и стрики\n"
        "✅ Статистика достижений\n"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"⭐ Оплатить {SUBSCRIPTION_PRICE_STARS} Stars",
            callback_data="pay_subscription"
        )]
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ========================
# /mystatus — статус подписки
# ========================
async def mystatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id, update.effective_user.username, update.effective_user.first_name)

    sub = db.get_subscription_status(user_id)

    if sub["status"] == "trial":
        emoji = "🎁"
        status_text = "Бесплатный период"
        detail = f"Осталось {sub['days_left']} дней (до {sub['expires_at']})"
    elif sub["status"] == "active":
        emoji = "⭐"
        status_text = "Активная подписка"
        detail = f"Действует до {sub['expires_at']} ({sub['days_left']} дней)"
    else:
        emoji = "😔"
        status_text = "Подписка неактивна"
        detail = "Бесплатный период закончился"

    text = (
        f"{emoji} *Статус: {status_text}*\n\n"
        f"{detail}\n\n"
    )

    if sub["status"] == "expired":
        text += f"Жми /subscribe чтобы оформить подписку за {SUBSCRIPTION_PRICE_STARS} ⭐"
    elif sub["status"] == "trial":
        text += "Совет: оформи подписку заранее через /subscribe — дни не пропадут!"

    await update.message.reply_text(text, parse_mode="Markdown")


# ========================
# Обработка оплаты через Telegram Stars
# ========================
async def pay_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь нажал кнопку оплаты — отправляем invoice."""
    query = update.callback_query
    await query.answer()

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title="Коуч-Трекер: Подписка на месяц",
        description=(
            f"Подписка на {SUBSCRIPTION_DAYS} дней. "
            "Неограниченные цели, напоминания, мотивация и трекинг прогресса."
        ),
        payload="subscription_monthly",
        provider_token="",  # Пустой для Telegram Stars
        currency="XTR",     # Telegram Stars
        prices=[LabeledPrice(label="Подписка на месяц", amount=SUBSCRIPTION_PRICE_STARS)],
    )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение платежа перед списанием."""
    query = update.pre_checkout_query

    if query.invoice_payload == "subscription_monthly":
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Неизвестный тип платежа.")


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка успешной оплаты."""
    payment = update.message.successful_payment
    user_id = update.effective_user.id

    # Сохраняем платёж в БД
    db.save_payment(
        user_id=user_id,
        telegram_charge_id=payment.telegram_payment_charge_id,
        provider_charge_id=payment.provider_payment_charge_id or "",
        amount=payment.total_amount,
        currency=payment.currency,
        payload=payment.invoice_payload,
    )

    # Активируем подписку
    db.activate_subscription(user_id, days=SUBSCRIPTION_DAYS)

    sub = db.get_subscription_status(user_id)

    await update.message.reply_text(
        "🎉 *Оплата прошла успешно!*\n\n"
        f"⭐ Подписка активна до {sub['expires_at']}\n\n"
        "Спасибо за доверие! Теперь ты можешь пользоваться всеми функциями без ограничений.\n"
        "Давай покорять новые вершины! 🚀\n\n"
        "Жми /newgoal чтобы создать цель!",
        parse_mode="Markdown"
    )

    logger.info(f"Payment successful: user={user_id}, amount={payment.total_amount} XTR")


# ========================
# /motivate — мотивация
# ========================
@require_subscription
async def motivate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(mot.get_motivation())


# ========================
# /stats — статистика
# ========================
@require_subscription
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = db.get_user_stats(user_id)

    bar_goals = mot.format_progress_bar(stats["completed_goals"], stats["total_goals"])
    bar_milestones = mot.format_progress_bar(stats["completed_milestones"], stats["total_milestones"])

    streak_msg = mot.get_streak_message(stats["streak"])
    streak_line = f"\n{streak_msg}" if streak_msg else ""

    text = (
        "📊 *Твоя статистика:*\n\n"
        f"🎯 Цели: {stats['completed_goals']}/{stats['total_goals']} выполнено\n"
        f"{bar_goals}\n\n"
        f"📍 Этапы: {stats['completed_milestones']}/{stats['total_milestones']} выполнено\n"
        f"{bar_milestones}\n\n"
        f"🔥 Активные цели: {stats['active_goals']}\n"
        f"📅 Серия дней: {stats['streak']} дней подряд"
        f"{streak_line}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ========================
# /goals — список целей
# ========================
@require_subscription
async def goals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    goals = db.get_active_goals(user_id)

    if not goals:
        await update.message.reply_text(mot.get_no_goals())
        return

    text = "📋 *Твои активные цели:*\n\n"
    for g in goals:
        deadline = datetime.fromisoformat(g["deadline"])
        days_left = (deadline.date() - datetime.now().date()).days

        milestones = db.get_milestones(g["id"])
        done_ms = sum(1 for m in milestones if m["status"] == "completed")
        total_ms = len(milestones)
        bar = mot.format_progress_bar(done_ms, total_ms, 8)

        if days_left < 0:
            time_str = f"⚠️ просрочена на {abs(days_left)} дн."
        elif days_left == 0:
            time_str = "🔥 дедлайн СЕГОДНЯ"
        elif days_left <= 3:
            time_str = f"⏰ осталось {days_left} дн."
        else:
            time_str = f"📅 {deadline.strftime('%d.%m.%Y')} (ещё {days_left} дн.)"

        text += (
            f"*{g['id']}.* {g['title']}\n"
            f"   {bar} ({done_ms}/{total_ms} этапов)\n"
            f"   {time_str}\n\n"
        )

    text += "Подробнее о цели: /goal\\_ID (напр. /goal\\_1)"
    await update.message.reply_text(text, parse_mode="Markdown")


# ========================
# /goal_ID — подробности о цели
# ========================
@require_subscription
async def goal_detail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        goal_id = int(text.split("_")[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Используй формат: /goal_1, /goal_2 и т.д.")
        return

    goal = db.get_goal(goal_id)
    if not goal or goal["user_id"] != update.effective_user.id:
        await update.message.reply_text("Цель не найдена 🤷‍♀️")
        return

    milestones = db.get_milestones(goal_id)
    deadline = datetime.fromisoformat(goal["deadline"])
    days_left = (deadline.date() - datetime.now().date()).days

    text = f"🎯 *{goal['title']}*\n"
    if goal["description"]:
        text += f"_{goal['description']}_\n"
    text += f"\n📅 Дедлайн: {deadline.strftime('%d.%m.%Y')}"

    if days_left < 0:
        text += f" (⚠️ просрочена на {abs(days_left)} дн.)\n"
    elif days_left == 0:
        text += " (🔥 СЕГОДНЯ)\n"
    else:
        text += f" (ещё {days_left} дн.)\n"

    text += f"Статус: {goal['status']}\n\n"

    if milestones:
        text += "*Этапы:*\n"
        for m in milestones:
            ms_deadline = datetime.fromisoformat(m["deadline"])
            status_icon = "✅" if m["status"] == "completed" else "⬜"
            ms_days = (ms_deadline.date() - datetime.now().date()).days

            if m["status"] == "completed":
                time_note = "готово!"
            elif ms_days < 0:
                time_note = f"просрочен на {abs(ms_days)} дн."
            elif ms_days == 0:
                time_note = "сегодня!"
            else:
                time_note = f"через {ms_days} дн."

            text += f"  {status_icon} {m['title']} — {ms_deadline.strftime('%d.%m')} ({time_note})\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# ========================
# /today — что нужно сделать сегодня
# ========================
@require_subscription
async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pending = db.get_pending_milestones_for_user(user_id)

    today = datetime.now().date()
    today_tasks = []
    upcoming_tasks = []
    overdue_tasks = []

    for m in pending:
        ms_deadline = datetime.fromisoformat(m["deadline"]).date()
        days_left = (ms_deadline - today).days

        task_info = {
            "title": m["title"],
            "goal_title": m["goal_title"],
            "days_left": days_left,
            "deadline": ms_deadline.strftime("%d.%m"),
        }

        if days_left < 0:
            overdue_tasks.append(task_info)
        elif days_left == 0:
            today_tasks.append(task_info)
        elif days_left <= 3:
            upcoming_tasks.append(task_info)

    if not today_tasks and not upcoming_tasks and not overdue_tasks:
        await update.message.reply_text(
            "🌿 На сегодня всё чисто! Можешь расслабиться или забежать вперёд 😉"
        )
        return

    text = "📅 *Что на повестке:*\n\n"

    if overdue_tasks:
        text += "⚠️ *Просрочено:*\n"
        for t in overdue_tasks:
            text += f"  • {t['title']} (цель: {t['goal_title']}) — просрочено на {abs(t['days_left'])} дн.\n"
        text += "\n"

    if today_tasks:
        text += "🔥 *Сегодня:*\n"
        for t in today_tasks:
            text += f"  • {t['title']} (цель: {t['goal_title']})\n"
        text += "\n"

    if upcoming_tasks:
        text += "⏰ *Ближайшие 3 дня:*\n"
        for t in upcoming_tasks:
            text += f"  • {t['title']} (цель: {t['goal_title']}) — через {t['days_left']} дн.\n"
        text += "\n"

    text += "Выполнила? Жми /done чтобы отметить!"
    await update.message.reply_text(text, parse_mode="Markdown")


# ========================================
# Создание цели (ConversationHandler)
# ========================================

async def newgoal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id, update.effective_user.username, update.effective_user.first_name)

    # Проверка подписки
    if not db.is_subscription_valid(user_id):
        await update.message.reply_text(
            "😔 Твой бесплатный период закончился.\n"
            f"Оформи подписку за {SUBSCRIPTION_PRICE_STARS} ⭐ Stars чтобы продолжить!\n"
            "Жми /subscribe"
        )
        return ConversationHandler.END

    creating_goals[user_id] = {}

    await update.message.reply_text(
        "🎯 Отлично, создаём новую цель!\n\n"
        "Шаг 1/4: Как назовём цель? Напиши коротко и ясно.\n\n"
        "Например: «Выучить Python», «Пробежать марафон», «Запустить проект»\n\n"
        "_/cancel чтобы отменить_",
        parse_mode="Markdown"
    )
    return GOAL_TITLE


async def goal_title_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    title = update.message.text.strip()

    if len(title) > 200:
        await update.message.reply_text("Слишком длинно! Давай покороче, до 200 символов 😊")
        return GOAL_TITLE

    creating_goals[user_id]["title"] = title

    await update.message.reply_text(
        f"Отличная цель: *{title}* 👏\n\n"
        "Шаг 2/4: Опиши цель подробнее — что конкретно хочешь достичь? "
        "Какой результат будет означать, что цель выполнена?\n\n"
        "_Или напиши «-» чтобы пропустить_",
        parse_mode="Markdown"
    )
    return GOAL_DESCRIPTION


async def goal_description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    desc = update.message.text.strip()

    if desc == "-":
        desc = None
    creating_goals[user_id]["description"] = desc

    await update.message.reply_text(
        "Шаг 3/4: Когда дедлайн? 📅\n\n"
        "Напиши дату в формате ДД.ММ.ГГГГ\n"
        "Например: 15.05.2026\n\n"
        "_/cancel чтобы отменить_",
        parse_mode="Markdown"
    )
    return GOAL_DEADLINE


async def goal_deadline_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    try:
        deadline = datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        await update.message.reply_text(
            "Не могу разобрать дату 😅 Напиши в формате ДД.ММ.ГГГГ, например: 15.05.2026"
        )
        return GOAL_DEADLINE

    if deadline.date() <= datetime.now().date():
        await update.message.reply_text(
            "Дедлайн должен быть в будущем! ⏰ Попробуй другую дату."
        )
        return GOAL_DEADLINE

    creating_goals[user_id]["deadline"] = deadline
    days_total = (deadline.date() - datetime.now().date()).days

    await update.message.reply_text(
        f"📅 Дедлайн: {deadline.strftime('%d.%m.%Y')} (через {days_total} дней)\n\n"
        "Шаг 4/4: На сколько этапов разбить цель?\n\n"
        "Я помогу расставить промежуточные дедлайны равномерно. "
        "Ты сможешь переименовать каждый этап.\n\n"
        "Напиши число от 2 до 10 (рекомендую 3-5 этапов)\n\n"
        "_/cancel чтобы отменить_",
        parse_mode="Markdown"
    )
    return GOAL_MILESTONES_COUNT


async def goal_milestones_count_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    try:
        count = int(text)
    except ValueError:
        await update.message.reply_text("Напиши число от 2 до 10 😊")
        return GOAL_MILESTONES_COUNT

    if count < 2 or count > 10:
        await update.message.reply_text("От 2 до 10 этапов, пожалуйста! 🙏")
        return GOAL_MILESTONES_COUNT

    creating_goals[user_id]["milestones_count"] = count
    creating_goals[user_id]["milestones"] = []
    creating_goals[user_id]["current_milestone"] = 0

    await update.message.reply_text(
        f"Супер! {count} этапов 💪\n\n"
        f"Как назовём этап 1/{count}?\n"
        "Например: «Пройти первый модуль курса»"
    )
    return GOAL_MILESTONE_TITLE


async def goal_milestone_title_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = creating_goals[user_id]
    title = update.message.text.strip()

    data["milestones"].append(title)
    data["current_milestone"] += 1

    total = data["milestones_count"]
    current = data["current_milestone"]

    if current < total:
        await update.message.reply_text(
            f"✅ Этап {current} записан!\n\n"
            f"Как назовём этап {current + 1}/{total}?"
        )
        return GOAL_MILESTONE_TITLE

    # Все этапы введены — показываем сводку
    deadline = data["deadline"]
    days_total = (deadline.date() - datetime.now().date()).days

    interval = days_total / total
    milestone_deadlines = []
    for i in range(total):
        ms_date = datetime.now() + timedelta(days=interval * (i + 1))
        milestone_deadlines.append(ms_date.date())
    data["milestone_deadlines"] = milestone_deadlines

    text = f"📋 *Проверяем:*\n\n"
    text += f"🎯 Цель: *{data['title']}*\n"
    if data.get("description"):
        text += f"📝 Описание: {data['description']}\n"
    text += f"📅 Дедлайн: {deadline.strftime('%d.%m.%Y')}\n\n"
    text += "*Этапы:*\n"
    for i, (ms_title, ms_date) in enumerate(zip(data["milestones"], milestone_deadlines)):
        text += f"  {i+1}. {ms_title} — до {ms_date.strftime('%d.%m.%Y')}\n"

    text += "\nВсё верно? Жми «Да» или «Нет»"

    keyboard = ReplyKeyboardMarkup(
        [["✅ Да, создать!", "❌ Нет, отменить"]],
        one_time_keyboard=True, resize_keyboard=True
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    return GOAL_CONFIRM


async def goal_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if "да" in text.lower() or "создать" in text.lower():
        data = creating_goals[user_id]

        goal_id = db.create_goal(
            user_id=user_id,
            title=data["title"],
            description=data.get("description"),
            deadline=data["deadline"].isoformat()
        )

        for i, (ms_title, ms_date) in enumerate(
            zip(data["milestones"], data["milestone_deadlines"])
        ):
            ms_id = db.create_milestone(
                goal_id=goal_id,
                title=ms_title,
                deadline=datetime.combine(ms_date, datetime.min.time()).isoformat(),
                order_num=i
            )
            remind_date_before = datetime.combine(
                ms_date - timedelta(days=1), datetime.min.time().replace(hour=9)
            )
            remind_date_day = datetime.combine(
                ms_date, datetime.min.time().replace(hour=9)
            )

            if remind_date_before > datetime.now():
                db.create_reminder(
                    user_id=user_id,
                    goal_id=goal_id,
                    milestone_id=ms_id,
                    remind_at=remind_date_before.isoformat(),
                    message=f"⏰ Завтра дедлайн этапа «{ms_title}» (цель: {data['title']}). Ты готова?"
                )
            db.create_reminder(
                user_id=user_id,
                goal_id=goal_id,
                milestone_id=ms_id,
                remind_at=remind_date_day.isoformat(),
                message=f"🔥 Сегодня дедлайн этапа «{ms_title}» (цель: {data['title']})! Давай-давай!"
            )

        del creating_goals[user_id]

        await update.message.reply_text(
            f"🎉 Цель создана! Вперёд к победе!\n\n"
            f"Я буду напоминать о каждом этапе. "
            f"Когда выполнишь — жми /done\n\n"
            f"Ты справишься, я в тебя верю! 💪",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        if user_id in creating_goals:
            del creating_goals[user_id]
        await update.message.reply_text(
            "Ок, отменяю! Когда будешь готова — жми /newgoal 😊",
            reply_markup=ReplyKeyboardRemove()
        )

    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in creating_goals:
        del creating_goals[user_id]
    await update.message.reply_text(
        "Отменено! Ничего страшного, вернёшься когда будешь готова 😊",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# ========================
# /done — отметить этап
# ========================
@require_subscription
async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pending = db.get_pending_milestones_for_user(user_id)

    if not pending:
        await update.message.reply_text(
            "У тебя нет незавершённых этапов 🎉\n"
            "Все сделано, или ещё нет целей? /newgoal чтобы создать!"
        )
        return

    keyboard = []
    for m in pending:
        btn_text = f"{m['title']} (цель: {m['goal_title']})"
        if len(btn_text) > 60:
            btn_text = btn_text[:57] + "..."
        keyboard.append([
            InlineKeyboardButton(
                btn_text,
                callback_data=f"complete_ms_{m['id']}"
            )
        ])

    await update.message.reply_text(
        "Какой этап выполнен? Нажми на него! 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def complete_milestone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        ms_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        return

    db.complete_milestone(ms_id)

    conn = db.get_connection()
    ms = conn.execute("SELECT * FROM milestones WHERE id = ?", (ms_id,)).fetchone()
    if ms:
        all_milestones = conn.execute(
            "SELECT * FROM milestones WHERE goal_id = ?", (ms["goal_id"],)
        ).fetchall()
        all_done = all(m["status"] == "completed" or m["id"] == ms_id for m in all_milestones)
    else:
        all_done = False
    conn.close()

    praise = mot.get_milestone_praise()
    text = f"✅ Этап отмечен!\n\n{praise}"

    if all_done and ms:
        text += (
            f"\n\n🎯 Все этапы цели выполнены! "
            f"Если цель полностью готова, жми /complete чтобы закрыть её!"
        )

    user_id = query.from_user.id
    stats = db.get_user_stats(user_id)
    streak_msg = mot.get_streak_message(stats["streak"])
    if streak_msg:
        text += f"\n\n{streak_msg}"

    await query.edit_message_text(text)


# ========================
# /complete — завершить цель
# ========================
@require_subscription
async def complete_goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    goals = db.get_active_goals(user_id)

    if not goals:
        await update.message.reply_text("У тебя нет активных целей для завершения 🤷‍♀️")
        return

    keyboard = []
    for g in goals:
        keyboard.append([
            InlineKeyboardButton(
                g["title"],
                callback_data=f"complete_goal_{g['id']}"
            )
        ])

    await update.message.reply_text(
        "Какую цель завершить? 🏆",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def complete_goal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        goal_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        return

    goal = db.get_goal(goal_id)
    if not goal:
        await query.edit_message_text("Цель не найдена 🤷‍♀️")
        return

    db.complete_goal(goal_id)

    praise = mot.get_goal_praise()
    text = f"{praise}\n\n🎯 Цель «{goal['title']}» выполнена!"

    stats = db.get_user_stats(query.from_user.id)
    text += f"\n\n📊 Всего выполнено целей: {stats['completed_goals']}"

    await query.edit_message_text(text)


# ========================
# /cancel_goal — отменить цель
# ========================
async def cancel_goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    goals = db.get_active_goals(user_id)

    if not goals:
        await update.message.reply_text("У тебя нет активных целей для отмены.")
        return

    keyboard = []
    for g in goals:
        keyboard.append([
            InlineKeyboardButton(
                f"❌ {g['title']}",
                callback_data=f"cancel_goal_{g['id']}"
            )
        ])

    await update.message.reply_text(
        "Какую цель отменить?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cancel_goal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        goal_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        return

    goal = db.get_goal(goal_id)
    if goal:
        db.cancel_goal(goal_id)
        await query.edit_message_text(
            f"Цель «{goal['title']}» отменена.\n"
            "Ничего страшного! Иногда нужно отпустить одно, чтобы сосредоточиться на другом 💛"
        )


# ========================
# /settings — персональные настройки напоминаний
# ========================

def _settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """Генерировать клавиатуру настроек на основе текущих значений."""
    morning_enabled = settings.get("morning_enabled", 1)
    evening_enabled = settings.get("evening_enabled", 1)
    morning_hour = settings.get("morning_hour", 9)
    evening_hour = settings.get("evening_hour", 20)
    mode = settings.get("deadline_mode", "both")

    # Иконки переключателей
    m_icon = "🟢" if morning_enabled else "⚫"
    e_icon = "🟢" if evening_enabled else "⚫"

    # Иконки режима дедлайнов
    mode_smart = "✅" if mode == "smart" else "○"
    mode_fixed = "✅" if mode == "fixed" else "○"
    mode_both = "✅" if mode == "both" else "○"

    return InlineKeyboardMarkup([
        # Утренний дайджест
        [InlineKeyboardButton(
            f"{m_icon} Утренний дайджест: {'вкл' if morning_enabled else 'выкл'}",
            callback_data="settings_toggle_morning"
        )],
        # Время утреннего дайджеста
        [
            InlineKeyboardButton("◀", callback_data="settings_morning_minus"),
            InlineKeyboardButton(f"☀️ {morning_hour:02d}:00", callback_data="settings_noop"),
            InlineKeyboardButton("▶", callback_data="settings_morning_plus"),
        ],
        # Вечерний дайджест
        [InlineKeyboardButton(
            f"{e_icon} Вечерний дайджест: {'вкл' if evening_enabled else 'выкл'}",
            callback_data="settings_toggle_evening"
        )],
        # Время вечернего дайджеста
        [
            InlineKeyboardButton("◀", callback_data="settings_evening_minus"),
            InlineKeyboardButton(f"🌙 {evening_hour:02d}:00", callback_data="settings_noop"),
            InlineKeyboardButton("▶", callback_data="settings_evening_plus"),
        ],
        # Режим напоминаний о дедлайнах
        [InlineKeyboardButton("── Напоминания о дедлайнах ──", callback_data="settings_noop")],
        [InlineKeyboardButton(
            f"{mode_smart} Умный (за 20% времени)",
            callback_data="settings_mode_smart"
        )],
        [InlineKeyboardButton(
            f"{mode_fixed} Фиксированный (за 7/3/1 дней)",
            callback_data="settings_mode_fixed"
        )],
        [InlineKeyboardButton(
            f"{mode_both} Оба варианта",
            callback_data="settings_mode_both"
        )],
        # Сохранить
        [InlineKeyboardButton("💾 Сохранить настройки", callback_data="settings_save")],
    ])


def _settings_text(settings: dict) -> str:
    morning_hour = settings.get("morning_hour", 9)
    evening_hour = settings.get("evening_hour", 20)
    morning_enabled = settings.get("morning_enabled", 1)
    evening_enabled = settings.get("evening_enabled", 1)
    mode = settings.get("deadline_mode", "both")

    mode_names = {
        "smart": "умный (за 20% времени до дедлайна)",
        "fixed": "фиксированный (за 7, 3 и 1 день)",
        "both": "оба: и 20%, и 7/3/1 дней",
    }

    return (
        "⚙️ *Настройки напоминаний*\n\n"
        f"☀️ Утренний дайджест: {'включён в ' + str(morning_hour) + ':00' if morning_enabled else 'выключен'}\n"
        f"🌙 Вечерний дайджест: {'включён в ' + str(evening_hour) + ':00' if evening_enabled else 'выключен'}\n"
        f"🔔 Режим дедлайнов: {mode_names.get(mode, mode)}\n\n"
        "Используй кнопки ниже, затем нажми *Сохранить*:"
    )


@require_subscription
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = db.get_user_settings(user_id)
    # Сохраняем настройки во временном хранилище для редактирования
    context.user_data["settings_draft"] = dict(settings)

    await update.message.reply_text(
        _settings_text(settings),
        parse_mode="Markdown",
        reply_markup=_settings_keyboard(settings)
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех inline-кнопок настроек."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    # Загрузить черновик настроек (или из БД если нет)
    draft = context.user_data.get("settings_draft") or db.get_user_settings(user_id)
    action = query.data

    if action == "settings_noop":
        return

    elif action == "settings_toggle_morning":
        draft["morning_enabled"] = 0 if draft.get("morning_enabled", 1) else 1

    elif action == "settings_toggle_evening":
        draft["evening_enabled"] = 0 if draft.get("evening_enabled", 1) else 1

    elif action == "settings_morning_minus":
        draft["morning_hour"] = (draft.get("morning_hour", 9) - 1) % 24

    elif action == "settings_morning_plus":
        draft["morning_hour"] = (draft.get("morning_hour", 9) + 1) % 24

    elif action == "settings_evening_minus":
        draft["evening_hour"] = (draft.get("evening_hour", 20) - 1) % 24

    elif action == "settings_evening_plus":
        draft["evening_hour"] = (draft.get("evening_hour", 20) + 1) % 24

    elif action == "settings_mode_smart":
        draft["deadline_mode"] = "smart"

    elif action == "settings_mode_fixed":
        draft["deadline_mode"] = "fixed"

    elif action == "settings_mode_both":
        draft["deadline_mode"] = "both"

    elif action == "settings_save":
        db.save_user_settings(
            user_id,
            morning_hour=draft.get("morning_hour", 9),
            evening_hour=draft.get("evening_hour", 20),
            morning_enabled=draft.get("morning_enabled", 1),
            evening_enabled=draft.get("evening_enabled", 1),
            deadline_mode=draft.get("deadline_mode", "both"),
        )
        context.user_data.pop("settings_draft", None)
        await query.edit_message_text(
            mot.get_settings_saved() + "\n\nНастройки изменить можно в /settings",
            parse_mode="Markdown"
        )
        return

    context.user_data["settings_draft"] = draft

    # Обновить клавиатуру
    await query.edit_message_text(
        _settings_text(draft),
        parse_mode="Markdown",
        reply_markup=_settings_keyboard(draft)
    )


# ========================
# Обработка текста (fallback)
# ========================
async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()

    positive_words = ["сделал", "выполни", "готов", "закончи", "успе"]
    negative_words = ["не мог", "сложно", "трудно", "устал", "не получ", "не успе", "сдаюсь"]

    if any(w in text for w in negative_words):
        await update.message.reply_text(mot.get_motivation())
    elif any(w in text for w in positive_words):
        await update.message.reply_text(
            f"{mot.get_milestone_praise()}\n\nНе забудь отметить выполненное через /done!"
        )
    else:
        await update.message.reply_text(
            "Я пока не очень понимаю свободный текст 🙈\n"
            "Используй команды — /help покажет всё, что я умею!"
        )


def create_application(token: str) -> Application:
    """Создать и настроить Application бота."""
    app = Application.builder().token(token).build()

    # ConversationHandler для создания цели
    goal_conv = ConversationHandler(
        entry_points=[CommandHandler("newgoal", newgoal_start)],
        states={
            GOAL_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_title_received)],
            GOAL_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_description_received)],
            GOAL_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_deadline_received)],
            GOAL_MILESTONES_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_milestones_count_received)],
            GOAL_MILESTONE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_milestone_title_received)],
            GOAL_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )

    # Регистрация handlers
    app.add_handler(goal_conv)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("goals", goals_command))
    app.add_handler(CommandHandler("motivate", motivate_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("complete", complete_goal_command))
    app.add_handler(CommandHandler("cancel_goal", cancel_goal_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("mystatus", mystatus_command))
    app.add_handler(CommandHandler("settings", settings_command))

    # /analyze — анализ личности
    app.add_handler(build_analyze_conversation())

    # Обработка /goal_ID
    app.add_handler(MessageHandler(
        filters.Regex(r"^/goal_\d+$"),
        goal_detail_command
    ))

    # Оплата через Telegram Stars
    app.add_handler(CallbackQueryHandler(pay_subscription_callback, pattern=r"^pay_subscription$"))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Callback queries для настроек
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^settings_"))

    # Callback queries для целей
    app.add_handler(CallbackQueryHandler(complete_milestone_callback, pattern=r"^complete_ms_"))
    app.add_handler(CallbackQueryHandler(complete_goal_callback, pattern=r"^complete_goal_"))
    app.add_handler(CallbackQueryHandler(cancel_goal_callback, pattern=r"^cancel_goal_"))

    # Текстовый fallback
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback))

    return app
