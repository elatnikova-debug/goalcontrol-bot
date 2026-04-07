"""
Коуч-Трекер Telegram Bot — версия 2.1
Главное меню на кнопках. PRO-монетизация. Коуч с лимитом.
Умные триггеры. Астро-советы. Предложение закрепить.
"""

import os
import logging
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, PreCheckoutQueryHandler, filters, ContextTypes
)

import database as db
import motivation as mot
from analyze_handler import build_analyze_conversation

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========================
# Цены
# ========================
PRO_PRICE_STARS = db.PRO_PRICE_STARS        # 950 Stars ≈ $19
PRO_SUB_PRICE_STARS = db.PRO_SUB_PRICE_STARS  # 1450 Stars ≈ $29/мес

# ========================
# Состояния ConversationHandler
# ========================
(
    GOAL_TITLE,
    GOAL_DESCRIPTION,
    GOAL_DEADLINE,
    GOAL_MILESTONES_COUNT,
    GOAL_MILESTONE_TITLE,
    GOAL_CONFIRM,
) = range(6)

COACH_CHAT = 100

creating_goals = {}


# ========================
# Главное меню
# ========================

def get_main_keyboard():
    """Reply-клавиатура — главное меню."""
    keyboard = [
        [KeyboardButton("🎯 Мои цели и проекты"), KeyboardButton("⚡ Фокус на сегодня")],
        [KeyboardButton("✅ Отметить прогресс"), KeyboardButton("🔥 Энергия и драйв")],
        [KeyboardButton("🤖 Коуч"), KeyboardButton("🌟 Звёзды сегодня")],
        [KeyboardButton("💎 PRO-доступ")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


# ========================
# /start
# ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username, user.first_name)

    name = user.first_name or "предприниматель"
    sub = db.get_subscription_status(user.id)

    if sub["status"] == "trial":
        status_text = f"🆓 Пробный период: {sub['days_left']} дней осталось"
    elif sub["status"] == "active":
        status_text = f"👑 PRO-подписка активна до {sub['expires_at']}"
    else:
        status_text = "⏰ Пробный период завершён"

    has_pro = db.has_pro_access(user.id)
    pro_text = "💎 PRO-доступ: активен" if has_pro else ""

    text = (
        f"Привет, {name}! 🚀\n\n"
        f"Я твой персональный коуч-трекер для предпринимателей.\n"
        f"Помогаю ставить цели, держать фокус и расти быстрее.\n\n"
        f"{status_text}\n"
        f"{pro_text}\n\n"
        f"Выбери действие в меню ниже 👇"
    )

    await update.message.reply_text(
        text.strip(),
        reply_markup=get_main_keyboard()
    )

    # Предложение закрепить бот (один раз при первом входе)
    user_data = db.get_user(user.id)
    if user_data:
        created = datetime.fromisoformat(user_data["created_at"])
        # Показываем только если пользователь создан меньше минуты назад (новый)
        if (datetime.utcnow() - created).total_seconds() < 60:
            await update.message.reply_text(
                "📌 *Совет:* Закрепи этот чат вверху списка диалогов — "
                "так ты не пропустишь ни одно напоминание и мотивацию.\n\n"
                "Для этого: зажми этот чат → Закрепить 📌",
                parse_mode="Markdown"
            )


# ========================
# Обработчики кнопок главного меню
# ========================

async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    context.user_data.pop("coach_mode", None)  # выход из коуч-режима при нажатии любой кнопки меню

    if text == "🎯 Мои цели и проекты":
        await show_goals(update, context)
    elif text == "⚡ Фокус на сегодня":
        await today_plan(update, context)
    elif text == "✅ Отметить прогресс":
        await mark_progress_menu(update, context)
    elif text == "🔥 Энергия и драйв":
        await send_motivation(update, context)
    elif text == "🤖 Коуч":
        await start_coach(update, context)
    elif text == "🌟 Звёзды сегодня":
        await stars_today(update, context)
    elif text == "💎 PRO-доступ":
        await show_pro_menu(update, context)


# ========================
# 🎯 Мои цели и проекты
# ========================

async def show_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    goals = db.get_active_goals(user_id)

    if not goals:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать первую цель", callback_data="new_goal")]
        ])
        await update.message.reply_text(
            mot.get_no_goals(),
            reply_markup=keyboard
        )
        return

    text = "🎯 *Твои активные цели и проекты:*\n\n"
    keyboard_buttons = []

    for goal in goals:
        milestones = db.get_milestones(goal["id"])
        done = sum(1 for m in milestones if m["status"] == "completed")
        total = len(milestones)
        bar = mot.format_progress_bar(done, total)

        deadline_str = ""
        try:
            dl = datetime.strptime(goal["deadline"], "%Y-%m-%d").date()
            days_left = (dl - datetime.now().date()).days
            if days_left < 0:
                deadline_str = f"⚠️ просрочено на {abs(days_left)} дн."
            elif days_left == 0:
                deadline_str = "🔴 сегодня дедлайн!"
            elif days_left <= 3:
                deadline_str = f"🟡 {days_left} дн."
            else:
                deadline_str = f"📅 {days_left} дн."
        except Exception:
            deadline_str = goal["deadline"]

        text += f"*{goal['title']}*\n{bar} | {deadline_str}\n\n"
        keyboard_buttons.append([
            InlineKeyboardButton(f"📋 {goal['title'][:30]}", callback_data=f"goal_{goal['id']}")
        ])

    keyboard_buttons.append([InlineKeyboardButton("➕ Новая цель", callback_data="new_goal")])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard_buttons)
    )


async def show_goal_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, goal_id: int):
    query = update.callback_query
    goal = db.get_goal(goal_id)
    if not goal:
        await query.edit_message_text("Цель не найдена.")
        return

    milestones = db.get_milestones(goal_id)
    done = sum(1 for m in milestones if m["status"] == "completed")
    total = len(milestones)
    bar = mot.format_progress_bar(done, total)

    text = f"🎯 *{goal['title']}*\n\n"
    if goal["description"]:
        text += f"_{goal['description']}_\n\n"
    text += f"Прогресс: {bar} ({done}/{total})\n"
    text += f"Дедлайн: {goal['deadline']}\n\n"

    if milestones:
        text += "*Этапы:*\n"
        for m in milestones:
            icon = "✅" if m["status"] == "completed" else "⬜"
            text += f"{icon} {m['title']}\n"

    buttons = [
        [InlineKeyboardButton("✅ Отметить этап", callback_data=f"done_{goal_id}")],
        [InlineKeyboardButton("🏁 Завершить цель", callback_data=f"complete_{goal_id}")],
        [InlineKeyboardButton("❌ Отменить цель", callback_data=f"cancel_{goal_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_goals")],
    ]

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ========================
# ⚡ Фокус на сегодня
# ========================

async def today_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    goals = db.get_active_goals(user_id)

    if not goals:
        await update.message.reply_text(
            "⚡ *Фокус на сегодня*\n\nУ тебя нет активных целей. Создай первую — и я помогу держать фокус каждый день!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➕ Создать цель", callback_data="new_goal")
            ]])
        )
        return

    text = "⚡ *Фокус на сегодня:*\n\n"
    today = datetime.now().date()
    urgent = []

    for goal in goals:
        milestones = db.get_milestones(goal["id"])
        pending = [m for m in milestones if m["status"] == "pending"]

        try:
            dl = datetime.strptime(goal["deadline"], "%Y-%m-%d").date()
            days_left = (dl - today).days
        except Exception:
            days_left = 999

        if pending:
            next_ms = pending[0]
            urgency = "🔴" if days_left <= 3 else "🟡" if days_left <= 7 else "🟢"
            text += f"{urgency} *{goal['title']}*\n"
            text += f"   → Следующий шаг: {next_ms['title']}\n"
            text += f"   Осталось: {days_left} дн.\n\n"
            if days_left <= 7:
                urgent.append(goal["title"])
        else:
            text += f"✅ *{goal['title']}* — все этапы выполнены!\n\n"

    if urgent:
        text += f"⚠️ *Срочно:* {', '.join(urgent)}\n"

    text += f"\n_{mot.get_morning_motivation()}_"

    await update.message.reply_text(text, parse_mode="Markdown")


# ========================
# ✅ Отметить прогресс
# ========================

async def mark_progress_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    milestones = db.get_pending_milestones_for_user(user_id)

    if not milestones:
        await update.message.reply_text(
            "✅ Нет незавершённых этапов. Все цели выполнены — или пора создать новые!"
        )
        return

    text = "✅ *Выбери выполненный этап:*\n\n"
    buttons = []
    for ms in milestones[:10]:
        text += f"• {ms['goal_title']} → {ms['title']}\n"
        buttons.append([
            InlineKeyboardButton(
                f"✅ {ms['title'][:40]}",
                callback_data=f"ms_done_{ms['id']}"
            )
        ])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ========================
# 🔥 Энергия и драйв
# ========================

async def send_motivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = db.get_user_stats(user_id)

    # Главное — цитата из базы 100
    quote = mot.get_random_quote()
    text = f"🔥 *Энергия и драйв*\n\n{quote}\n\n"

    if stats["streak"] > 0:
        streak_msg = mot.get_streak_message(stats["streak"])
        if streak_msg:
            text += f"{streak_msg}\n\n"
        text += f"🔥 Твой стрик: *{stats['streak']} дней подряд*\n"

    text += f"\n📊 Твой прогресс:\n"
    text += f"• Целей завершено: {stats['completed_goals']}\n"
    text += f"• Этапов выполнено: {stats['completed_milestones']}\n"
    text += f"• Активных проектов: {stats['active_goals']}\n"

    more_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Ещё цитату", callback_data="quick_motivation"),
         InlineKeyboardButton("⚡ Фокус", callback_data="quick_focus")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=more_buttons)


# ========================
# 🌟 Звёзды сегодня
# ========================

async def stars_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Астро-советы — только после разбора личности (нужна дата/время рождения)."""
    user_id = update.effective_user.id

    # Проверяем профиль
    profile = db.get_user_profile(user_id)
    if not profile or not profile.get("birth_date"):
        await update.message.reply_text(
            "🌟 *Звёзды сегодня*\n\n"
            "Чтобы получить персональный астро-брифинг, мне нужны твои данные рождения.\n\n"
            "Пройди разбор личности — это займёт 3 минуты, "
            "и после этого каждый день будешь получать личный астро-брифинг "
            "с советами по переговорам, финансам и бизнесу.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔮 Пройти разбор личности", callback_data="start_analyze")],
            ])
        )
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        await update.message.reply_text("Функция временно недоступна. Попробуй позже.")
        return

    await update.message.chat.send_action("typing")

    goals = db.get_active_goals(user_id)
    today = datetime.now().strftime("%d.%m.%Y, %A")
    name = profile.get("full_name", "")
    birth_date = profile.get("birth_date", "")
    birth_city = profile.get("birth_city", "")
    birth_time = profile.get("birth_time", "не указано")
    analysis = profile.get("analysis_result", "")[:800]

    goals_text = ""
    if goals:
        goals_text = "\nАктивные цели: " + ", ".join(g['title'] for g in goals[:5])

    prompt = (
        f"Персональный астро-брифинг для предпринимателя.\n\n"
        f"Имя: {name}\n"
        f"Дата рождения: {birth_date}\n"
        f"Город рождения: {birth_city}\n"
        f"Время рождения: {birth_time}\n"
        f"Сегодня: {today}\n"
        f"{goals_text}\n\n"
        f"Краткий психопрофиль:\n{analysis}\n\n"
        "Дай персональный астро-брифинг именно для этого человека на сегодня:\n"
        "🌟 ЭНЕРГИЯ ДНЯ — транзиты относительно его натальной карты, что это значит для бизнеса\n"
        "🤝 ПЕРЕГОВОРЫ — лучшее время для важных встреч, стиль переговоров на сегодня, чего избегать\n"
        "💰 ДЕНЬГИ — финансовый совет с учётом его психотипа и звёзд\n"
        "🎯 ГЛАВНЫЙ СОВЕТ — одно конкретное действие для продвижения к его целям\n\n"
        "Стиль: уверенный, конкретный, как личный консультант. "
        "Обращайся по имени. Отвечай на русском. Максимум 300 слов."
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": (
                    "Ты — персональный астролог-консультант для предпринимателей и топ-менеджеров. "
                    "Ты знаешь натальную карту клиента и текущие транзиты. "
                    "Твои советы опираются на астрологию, но подаются как практичные бизнес-рекомендации. "
                    "Особое внимание: переговоры, финансы, тайминг важных решений. "
                    "Отвечай на русском."
                )},
                {"role": "user", "content": prompt},
            ],
            max_tokens=600,
            temperature=0.8,
        )

        result = response.choices[0].message.content
        await update.message.reply_text(
            f"🌟 *Звёзды сегодня*\n\n{result}",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Stars today error: {e}")
        await update.message.reply_text("Не удалось получить астро-брифинг. Попробуй через минуту.")


# ========================
# 🤖 Коуч
# ========================

async def start_coach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    has_pro = db.has_pro_access(user_id) or db.has_pro_subscription(user_id)
    remaining = db.FREE_COACH_MESSAGES_PER_DAY - db.get_coach_messages_today(user_id)

    if not has_pro and remaining <= 0:
        await update.message.reply_text(
            mot.COACH_LIMIT_REACHED,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Открыть PRO-доступ", callback_data="pro_buy")
            ]])
        )
        return

    if has_pro:
        coach_text = (
            "🤖 *Коуч на связи*\n\n"
            "Я твой персональный AI-коуч. Задавай любые вопросы:\n"
            "• Как разблокироваться если застрял\n"
            "• Как расставить приоритеты\n"
            "• Стратегия роста бизнеса\n"
            "• Работа с командой и делегирование\n\n"
            "👑 У тебя PRO — без ограничений. Что тебя беспокоит прямо сейчас?"
        )
    else:
        coach_text = mot.COACH_WELCOME.format(remaining=remaining)

    context.user_data["coach_mode"] = True

    await update.message.reply_text(
        coach_text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🏠 Главное меню")]],
            resize_keyboard=True
        )
    )


async def handle_coach_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает сообщения в режиме коуч-чата."""
    if update.message.text == "🏠 Главное меню":
        context.user_data.pop("coach_mode", None)
        await update.message.reply_text(
            "Возвращаемся в главное меню 👇",
            reply_markup=get_main_keyboard()
        )
        return

    user_id = update.effective_user.id
    has_pro = db.has_pro_access(user_id) or db.has_pro_subscription(user_id)

    if not has_pro:
        remaining = db.FREE_COACH_MESSAGES_PER_DAY - db.get_coach_messages_today(user_id)
        if remaining <= 0:
            context.user_data.pop("coach_mode", None)
            await update.message.reply_text(
                mot.COACH_LIMIT_REACHED,
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )
            return

    # Получаем ключ OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        await update.message.reply_text(
            "Коуч временно недоступен. Администратор работает над устранением неполадок.",
            reply_markup=get_main_keyboard()
        )
        context.user_data.pop("coach_mode", None)
        return

    user_message = update.message.text
    await update.message.chat.send_action("typing")

    # Сохраняем сообщение пользователя
    db.save_coach_message(user_id, "user", user_message)
    if not has_pro:
        db.increment_coach_messages(user_id)

    # Получаем историю
    history = db.get_coach_history(user_id, limit=10)

    # Получаем цели пользователя для контекста
    goals = db.get_active_goals(user_id)
    goals_context = ""
    if goals:
        goals_context = "\n\nАктивные цели пользователя:\n"
        for g in goals[:5]:
            goals_context += f"- {g['title']} (дедлайн: {g['deadline']})\n"

    system_prompt = (
        "Ты — элитный коуч для предпринимателей. Мировой уровень. "
        "Объединяешь лучшие методологии: OKR, SMART, GTD, принципы Коллинза и Друкера, "
        "нейронауку мотивации, психологию достижений Дуэк, антихрупкость Талеба. "
        "Стиль: прямой, конкретный, без воды. Как разговор с умным другом-предпринимателем. "
        "Даёшь конкретные действия, а не общие советы. "
        "Отвечаешь только на русском языке. Максимум 300 слов за ответ."
        + goals_context
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=600,
            temperature=0.8,
        )

        reply = response.choices[0].message.content
        db.save_coach_message(user_id, "assistant", reply)

        remaining_after = db.FREE_COACH_MESSAGES_PER_DAY - db.get_coach_messages_today(user_id)

        footer = ""
        if not has_pro and remaining_after <= 5:
            footer = f"\n\n_Осталось сообщений сегодня: {remaining_after}_"
            if remaining_after <= 2:
                footer += "\n\n💎 Хочешь без ограничений? /pro"

        await update.message.reply_text(
            reply + footer,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Coach GPT error: {e}")
        await update.message.reply_text(
            "Что-то пошло не так. Попробуй ещё раз через минуту."
        )


# ========================
# 💎 PRO-доступ
# ========================

async def show_pro_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    has_pro = db.has_pro_access(user_id)
    has_sub = db.has_pro_subscription(user_id)

    if has_pro and has_sub:
        await update.message.reply_text(
            "👑 У тебя активен полный PRO-доступ и подписка!\n\nВсе возможности разблокированы.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🧠 Мой предпринимательский профиль", callback_data="pro_profile"),
                InlineKeyboardButton("🧪 Профайлинг", callback_data="pro_tests"),
            ], [
                InlineKeyboardButton("🚀 Стратегия роста", callback_data="pro_roadmap"),
            ]])
        )
        return

    if has_pro:
        text = (
            "💎 *У тебя активен PRO-доступ!*\n\n"
            "Доступны:\n"
            "🧠 Предпринимательский профиль\n"
            "🧪 Профайлинг\n"
            "🚀 Стратегия роста\n\n"
            "Хочешь добавить безлимитный коуч и еженедельные разборы?"
        )
        buttons = [
            [InlineKeyboardButton("🧠 Профиль", callback_data="pro_profile"),
             InlineKeyboardButton("🧪 Профайлинг", callback_data="pro_tests")],
            [InlineKeyboardButton("🚀 Стратегия роста", callback_data="pro_roadmap")],
            [InlineKeyboardButton("👑 PRO-подписка $29/мес", callback_data="pro_sub_buy")],
        ]
    else:
        text = mot.PRO_OFFER
        buttons = [
            [InlineKeyboardButton("💎 Купить PRO-доступ — 950 Stars", callback_data="pro_buy")],
            [InlineKeyboardButton("👑 PRO-подписка — 1450 Stars/мес", callback_data="pro_sub_buy")],
            [InlineKeyboardButton("❓ Что входит в PRO?", callback_data="pro_info")],
        ]

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ========================
# Callback-обработчики
# ========================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # Навигация по целям
    if data == "new_goal":
        await query.edit_message_text("Используй команду /newgoal чтобы создать новую цель.")

    elif data.startswith("goal_"):
        goal_id = int(data.split("_")[1])
        await show_goal_detail(update, context, goal_id)

    elif data == "back_goals":
        goals = db.get_active_goals(user_id)
        if not goals:
            await query.edit_message_text(mot.get_no_goals())
            return
        text = "🎯 *Твои активные цели:*\n\n"
        buttons = []
        for goal in goals:
            milestones = db.get_milestones(goal["id"])
            done = sum(1 for m in milestones if m["status"] == "completed")
            total = len(milestones)
            bar = mot.format_progress_bar(done, total)
            text += f"*{goal['title']}* — {bar}\n"
            buttons.append([InlineKeyboardButton(f"📋 {goal['title'][:30]}", callback_data=f"goal_{goal['id']}")])
        buttons.append([InlineKeyboardButton("➕ Новая цель", callback_data="new_goal")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    # Завершение этапа
    elif data.startswith("ms_done_"):
        ms_id = int(data.split("_")[2])
        db.complete_milestone(ms_id)
        await query.edit_message_text(
            f"{mot.get_milestone_praise()}\n\nЭтап отмечен как выполненный! ✅"
        )

        # === УМНЫЙ ТРИГГЕР: после каждого этапа — CTA кнопки ===
        cta_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔥 Порция мотивации", callback_data="quick_motivation"),
             InlineKeyboardButton("⚡ Фокус", callback_data="quick_focus")],
        ])
        await query.message.reply_text(
            f"{mot.get_reminder_cta()} Продолжай в том же духе!",
            reply_markup=cta_buttons
        )

        # === УМНЫЙ ТРИГГЕР: после 3-го этапа — предложить анализ или коуч ===
        completed_total = db.get_user_stats(user_id).get("completed_milestones", 0)
        if completed_total > 0 and completed_total % 3 == 0:
            trigger_3_text = mot.get_milestone_3_trigger()
            trigger_3_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔮 Разбор личности", callback_data="start_analyze")],
                [InlineKeyboardButton("🤖 Поговорить с коучем", callback_data="start_coach_from_trigger")],
            ])
            await query.message.reply_text(
                trigger_3_text,
                reply_markup=trigger_3_buttons
            )

    # Отметить этап из детали цели
    elif data.startswith("done_"):
        goal_id = int(data.split("_")[1])
        milestones = db.get_milestones(goal_id)
        pending = [m for m in milestones if m["status"] == "pending"]
        if not pending:
            await query.edit_message_text("✅ Все этапы этой цели уже выполнены!")
            return
        buttons = [
            [InlineKeyboardButton(f"✅ {m['title'][:40]}", callback_data=f"ms_done_{m['id']}")]
            for m in pending
        ]
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data=f"goal_{goal_id}")])
        await query.edit_message_text(
            "Выбери выполненный этап:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # Завершить цель
    elif data.startswith("complete_"):
        goal_id = int(data.split("_")[1])
        goal = db.get_goal(goal_id)
        buttons = [
            [InlineKeyboardButton("✅ Да, цель достигнута!", callback_data=f"confirm_complete_{goal_id}")],
            [InlineKeyboardButton("◀️ Назад", callback_data=f"goal_{goal_id}")],
        ]
        await query.edit_message_text(
            f"Подтвердить завершение цели *{goal['title']}*?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("confirm_complete_"):
        goal_id = int(data.split("_")[2])
        db.complete_goal(goal_id)
        await query.edit_message_text(
            f"{mot.get_goal_praise()}\n\nЦель закрыта! 🏆"
        )

    # Отменить цель
    elif data.startswith("cancel_"):
        goal_id = int(data.split("_")[1])
        goal = db.get_goal(goal_id)
        buttons = [
            [InlineKeyboardButton("❌ Да, отменить", callback_data=f"confirm_cancel_{goal_id}")],
            [InlineKeyboardButton("◀️ Назад", callback_data=f"goal_{goal_id}")],
        ]
        await query.edit_message_text(
            f"Отменить цель *{goal['title']}*?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("confirm_cancel_"):
        goal_id = int(data.split("_")[2])
        db.cancel_goal(goal_id)
        await query.edit_message_text("Цель отменена.")

    # PRO-покупка
    elif data == "pro_buy":
        await send_pro_invoice(update, context, "pro_bundle")

    elif data == "pro_sub_buy":
        await send_pro_invoice(update, context, "pro_subscription")

    elif data == "pro_info":
        await query.edit_message_text(
            mot.PRO_OFFER + "\n\n" + mot.PRO_SUB_OFFER,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Купить PRO — 950 Stars", callback_data="pro_buy")],
                [InlineKeyboardButton("👑 Подписка — 1450 Stars/мес", callback_data="pro_sub_buy")],
            ])
        )

    # PRO-функции
    elif data == "pro_profile":
        await handle_pro_profile(update, context)

    elif data == "pro_tests":
        await handle_pro_tests(update, context)

    elif data == "pro_roadmap":
        await handle_pro_roadmap(update, context)

    # === УМНЫЕ ТРИГГЕРЫ: CTA кнопки ===
    elif data == "quick_motivation":
        quote = mot.get_random_quote()
        await query.edit_message_text(
            f"🔥 *Энергия и драйв*\n\n{quote}",
            parse_mode="Markdown"
        )

    elif data == "quick_focus":
        goals = db.get_active_goals(user_id)
        if not goals:
            await query.edit_message_text("У тебя нет активных целей. Создай первую — кнопка 🎯 в меню!")
        else:
            text = "⚡ *Фокус:*\n\n"
            for goal in goals[:3]:
                milestones = db.get_milestones(goal["id"])
                pending = [m for m in milestones if m["status"] == "pending"]
                if pending:
                    text += f"🎯 *{goal['title']}*\n→ {pending[0]['title']}\n\n"
            text += f"\n_{mot.get_morning_motivation()}_"
            await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "setup_reminders":
        # Перенаправляем на настройки напоминаний
        settings = db.get_user_settings(user_id)
        await query.edit_message_text(
            _settings_text(settings),
            parse_mode="Markdown",
            reply_markup=_settings_keyboard(settings)
        )

    elif data == "start_analyze":
        # Запуск анализа личности
        await query.edit_message_text(
            "🔮 Для разбора личности используй команду /analyze\n\n"
            "Я задам несколько вопросов — это займёт около 3 минут.\n"
            "После этого ты получишь подробный профиль и доступ к 🌟 Звёзды сегодня.",
            parse_mode="Markdown"
        )

    elif data == "start_coach_from_trigger":
        # Активируем коуч-режим
        context.user_data["coach_mode"] = True
        has_pro = db.has_pro_access(user_id) or db.has_pro_subscription(user_id)
        if has_pro:
            coach_text = (
                "🤖 *Коуч на связи*\n\n"
                "👑 У тебя PRO — без ограничений. Что тебя беспокоит прямо сейчас?"
            )
        else:
            remaining = db.FREE_COACH_MESSAGES_PER_DAY - db.get_coach_messages_today(user_id)
            coach_text = mot.COACH_WELCOME.format(remaining=remaining)
        await query.edit_message_text(coach_text, parse_mode="Markdown")
        await query.message.reply_text(
            "Напиши свой вопрос 👇",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🏠 Главное меню")]],
                resize_keyboard=True
            )
        )


# ========================
# PRO-функции
# ========================

async def handle_pro_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not db.has_pro_access(user_id):
        await query.edit_message_text(
            "💎 Эта функция доступна в PRO-доступе.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Купить PRO — 950 Stars", callback_data="pro_buy")
            ]])
        )
        return

    if db.profile_analysis_done(user_id):
        profile = db.get_user_profile(user_id)
        # Показываем сохранённый анализ
        result = profile.get("analysis_result", "")
        # Разбиваем на части если длинный
        if len(result) > 3500:
            chunks = [result[i:i+3500] for i in range(0, len(result), 3500)]
            for chunk in chunks:
                await query.message.reply_text(chunk)
        else:
            await query.edit_message_text(result[:3500])
    else:
        await query.edit_message_text(
            "🧠 *Предпринимательский профиль*\n\n"
            "Для создания профиля нужно пройти анализ личности.\n"
            "Используй команду /analyze — это займёт около 3 минут.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔮 Начать анализ", callback_data="start_analyze")
            ]])
        )


async def handle_pro_tests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not db.has_pro_access(user_id):
        await query.edit_message_text(
            "💎 Эта функция доступна в PRO-доступе.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Купить PRO — 950 Stars", callback_data="pro_buy")
            ]])
        )
        return

    text = (
        "🧪 *Профайлинг — доказательные тесты*\n\n"
        "Три теста, которые реально работают:\n\n"
        "1️⃣ *MBTI* — 16 типов личности. Покажет твой стиль мышления и принятия решений\n"
        "2️⃣ *Gallup StrengthsFinder* — топ-5 твоих природных сильных сторон\n"
        "3️⃣ *Тип предпринимательского мышления* — Visionary / Builder / Optimizer / Connector\n\n"
        "Общее время: ~25 минут. Начнём?\n\n"
        "Отправь /tests чтобы начать тестирование."
    )

    await query.edit_message_text(text, parse_mode="Markdown")


async def handle_pro_roadmap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not db.has_pro_access(user_id):
        await query.edit_message_text(
            "💎 Эта функция доступна в PRO-доступе.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Купить PRO — 950 Stars", callback_data="pro_buy")
            ]])
        )
        return

    goals = db.get_active_goals(user_id)
    profile = db.get_user_profile(user_id)

    if not goals:
        await query.edit_message_text(
            "🚀 Для стратегии роста сначала создай хотя бы одну цель.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➕ Создать цель", callback_data="new_goal")
            ]])
        )
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        await query.edit_message_text("Функция временно недоступна. Попробуй позже.")
        return

    await query.edit_message_text("🚀 Строю твою индивидуальную стратегию роста... ⏳")

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)

        goals_text = "\n".join(f"- {g['title']} (дедлайн: {g['deadline']})" for g in goals)
        analysis = profile.get("analysis_result", "") if profile else ""

        prompt = (
            f"Ты — элитный бизнес-стратег и коуч для предпринимателей.\n\n"
            f"Активные цели предпринимателя:\n{goals_text}\n\n"
            f"{'Анализ личности:\n' + analysis[:1500] if analysis else ''}\n\n"
            f"Создай ИНДИВИДУАЛЬНУЮ стратегию достижения этих целей:\n"
            f"1. Приоритизация целей (какую закрывать первой и почему)\n"
            f"2. Еженедельный ритм работы над целями\n"
            f"3. Главные риски и как их нейтрализовать\n"
            f"4. Три конкретных действия на эту неделю\n"
            f"5. Ключевой совет именно для этого предпринимателя\n\n"
            f"Отвечай на русском. Конкретно, без воды. Максимум 500 слов."
        )

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты — элитный коуч для предпринимателей. Конкретно, практично, без общих фраз."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7,
        )

        result = response.choices[0].message.content
        await query.message.reply_text(
            f"🚀 *Твоя стратегия роста:*\n\n{result}",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Roadmap GPT error: {e}")
        await query.message.reply_text("Ошибка при генерации стратегии. Попробуй через минуту.")


# ========================
# Оплата
# ========================

async def send_pro_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, product: str):
    query = update.callback_query
    user_id = query.from_user.id

    if product == "pro_bundle":
        title = "💎 PRO-доступ"
        description = "Предпринимательский профиль + Профайлинг + Стратегия роста. Разовая покупка — навсегда."
        payload = "pro_bundle"
        price = PRO_PRICE_STARS
    else:
        title = "👑 PRO-подписка"
        description = "Безлимитный коуч 24/7 + еженедельные разборы + всё из PRO-доступа. 30 дней."
        payload = "pro_subscription"
        price = PRO_SUB_PRICE_STARS

    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=price)],
        )
    except Exception as e:
        logger.error(f"Invoice error: {e}")
        await query.message.reply_text("Ошибка при создании счёта. Попробуй через минуту.")


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload

    charge_id = payment.telegram_payment_charge_id
    amount = payment.total_amount

    if payload == "pro_bundle":
        db.save_pro_purchase(user_id, "pro_bundle", charge_id, amount)
        db.save_payment(user_id, charge_id, "", amount, "XTR", payload)
        await update.message.reply_text(
            "🎉 *PRO-доступ активирован!*\n\n"
            "Теперь тебе доступны:\n"
            "🧠 Предпринимательский профиль\n"
            "🧪 Профайлинг\n"
            "🚀 Стратегия роста\n\n"
            "Нажми 💎 PRO-доступ в меню чтобы начать!",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
    elif payload == "pro_subscription":
        db.activate_subscription(user_id, days=30)
        db.save_pro_purchase(user_id, "pro_bundle", charge_id, amount)  # подписка включает всё
        db.save_payment(user_id, charge_id, "", amount, "XTR", payload)
        await update.message.reply_text(
            "👑 *PRO-подписка активирована на 30 дней!*\n\n"
            "Безлимитный коуч и все PRO-функции теперь твои!\n\n"
            "Нажми 🤖 Коуч — никаких ограничений.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )


# ========================
# /newgoal
# ========================

async def newgoal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    db.ensure_user(user_id)
    creating_goals[user_id] = {}

    text = "🎯 *Новая цель или проект*\n\nКак называется твоя цель? Напиши кратко и ёмко."

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
    return GOAL_TITLE


async def goal_title_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    creating_goals[user_id]["title"] = update.message.text
    await update.message.reply_text(
        "Отлично! Теперь коротко опиши — что именно ты хочешь достичь? (или /skip)"
    )
    return GOAL_DESCRIPTION


async def goal_description_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if update.message.text != "/skip":
        creating_goals[user_id]["description"] = update.message.text
    else:
        creating_goals[user_id]["description"] = ""
    await update.message.reply_text(
        "📅 Дедлайн? Напиши дату в формате ДД.ММ.ГГГГ\n\nНапример: 31.12.2026"
    )
    return GOAL_DEADLINE


async def goal_deadline_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    text = update.message.text.strip()
    try:
        dl = datetime.strptime(text, "%d.%m.%Y").date()
        if dl <= datetime.now().date():
            await update.message.reply_text("Дедлайн должен быть в будущем. Попробуй ещё раз:")
            return GOAL_DEADLINE
        creating_goals[user_id]["deadline"] = dl.strftime("%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Неверный формат. Используй ДД.ММ.ГГГГ, например 31.12.2026:")
        return GOAL_DEADLINE

    await update.message.reply_text(
        "На сколько этапов разобьём цель? Напиши число от 1 до 10\n\n"
        "_Совет: 3–5 этапов — оптимально_ 💡",
        parse_mode="Markdown"
    )
    return GOAL_MILESTONES_COUNT


async def goal_milestones_count_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    try:
        count = int(update.message.text.strip())
        if not 1 <= count <= 10:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Напиши число от 1 до 10:")
        return GOAL_MILESTONES_COUNT

    creating_goals[user_id]["milestones_count"] = count
    creating_goals[user_id]["milestones"] = []
    creating_goals[user_id]["current_milestone"] = 1

    await update.message.reply_text(
        f"Этап 1 из {count}: как называется первый этап?"
    )
    return GOAL_MILESTONE_TITLE


async def goal_milestone_title_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    data = creating_goals[user_id]
    title = update.message.text.strip()
    current = data["current_milestone"]
    count = data["milestones_count"]

    data["milestones"].append(title)

    if current < count:
        data["current_milestone"] += 1
        await update.message.reply_text(
            f"Этап {current + 1} из {count}:"
        )
        return GOAL_MILESTONE_TITLE
    else:
        # Показываем итог
        text = (
            f"✅ *Проверь данные:*\n\n"
            f"*Цель:* {data['title']}\n"
            f"*Описание:* {data.get('description') or '—'}\n"
            f"*Дедлайн:* {data['deadline']}\n\n"
            f"*Этапы:*\n"
        )
        for i, m in enumerate(data["milestones"], 1):
            text += f"{i}. {m}\n"

        buttons = [
            [InlineKeyboardButton("✅ Создать!", callback_data="confirm_goal")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_goal_creation")],
        ]
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return GOAL_CONFIRM


async def goal_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = creating_goals.get(user_id, {})

    if query.data == "cancel_goal_creation":
        creating_goals.pop(user_id, None)
        await query.edit_message_text("Создание цели отменено.")
        return ConversationHandler.END

    # Сохраняем цель
    milestones = data.get("milestones", [])
    deadline = data["deadline"]
    dl_date = datetime.strptime(deadline, "%Y-%m-%d").date()
    total_days = (dl_date - datetime.now().date()).days

    goal_id = db.create_goal(
        user_id,
        data["title"],
        data.get("description", ""),
        deadline
    )

    # Создаём этапы с равномерным распределением дат
    for i, ms_title in enumerate(milestones, 1):
        ms_days = int(total_days * i / len(milestones))
        ms_deadline = (datetime.now().date() + timedelta(days=ms_days)).strftime("%Y-%m-%d")
        db.create_milestone(goal_id, ms_title, ms_deadline, i)

    creating_goals.pop(user_id, None)

    await query.edit_message_text(
        f"🚀 Цель *{data['title']}* создана!\n\n"
        f"Дедлайн: {deadline}\nЭтапов: {len(milestones)}\n\n"
        f"Теперь держим фокус и выполняем шаг за шагом! 💪",
        parse_mode="Markdown"
    )

    # === УМНЫЙ ТРИГГЕР: после создания цели предложить напоминания + мотивацию ===
    trigger_text = mot.get_goal_created_trigger()
    trigger_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏰ Настроить напоминания", callback_data="setup_reminders")],
        [InlineKeyboardButton("🔥 Порция мотивации", callback_data="quick_motivation")],
        [InlineKeyboardButton("⚡ Фокус на сегодня", callback_data="quick_focus")],
    ])
    await context.bot.send_message(
        chat_id=user_id,
        text=trigger_text,
        reply_markup=trigger_buttons
    )

    # Возвращаем главное меню
    await context.bot.send_message(
        chat_id=user_id,
        text="Главное меню 👇",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# ========================
# /stats
# ========================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = db.get_user_stats(user_id)
    sub = db.get_subscription_status(user_id)
    has_pro = db.has_pro_access(user_id)

    if sub["status"] == "trial":
        sub_text = f"🆓 Пробный период: {sub['days_left']} дн."
    elif sub["status"] == "active":
        sub_text = f"👑 PRO-подписка до {sub['expires_at']}"
    else:
        sub_text = "⏰ Пробный период завершён"

    text = (
        f"📊 *Твоя статистика*\n\n"
        f"🎯 Целей создано: {stats['total_goals']}\n"
        f"✅ Целей достигнуто: {stats['completed_goals']}\n"
        f"🔄 Активных проектов: {stats['active_goals']}\n\n"
        f"📌 Этапов выполнено: {stats['completed_milestones']} из {stats['total_milestones']}\n"
        f"🔥 Стрик: {stats['streak']} дней\n\n"
        f"💳 Статус: {sub_text}\n"
        f"{'💎 PRO-доступ: активен' if has_pro else ''}"
    )

    await update.message.reply_text(text.strip(), parse_mode="Markdown")


# ========================
# /help
# ========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ *Как пользоваться ботом*\n\n"
        "*Кнопки главного меню:*\n"
        "🎯 *Мои цели и проекты* — все твои активные цели\n"
        "⚡ *Фокус на сегодня* — что делать прямо сейчас\n"
        "✅ *Отметить прогресс* — отметить выполненный этап\n"
        "🔥 *Энергия и драйв* — мотивация от лучших мировых лидеров\n"
        "🤖 *Коуч* — чат с AI-коучем (20 сообщений/день бесплатно)\n"
        "🌟 *Звёзды сегодня* — персональный астро-брифинг\n"
        "💎 *PRO-доступ* — разблокировать мощные инструменты\n\n"
        "*Команды:*\n"
        "/newgoal — создать новую цель\n"
        "/analyze — анализ личности\n"
        "/stats — твоя статистика\n"
        "/settings — настройки напоминаний\n"
        "/start — главное меню\n\n"
        "*PRO-функции:*\n"
        "🧠 Предпринимательский профиль\n"
        "🧪 Профайлинг (MBTI + Gallup)\n"
        "🚀 Индивидуальная стратегия роста\n"
        "🌟 Персональные астро-советы"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())


# ========================
# /settings — настройки напоминаний (inline)
# ========================

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    settings = db.get_user_settings(user_id)
    await update.message.reply_text(
        _settings_text(settings),
        parse_mode="Markdown",
        reply_markup=_settings_keyboard(settings)
    )


def _settings_text(s: dict) -> str:
    morning_status = "вкл" if s.get("morning_enabled", 1) else "выкл"
    evening_status = "вкл" if s.get("evening_enabled", 1) else "выкл"
    mode = s.get("deadline_mode", "both")
    mode_label = {"smart": "Умный (за 20% времени)", "fixed": "Фиксированный (7-3-1 дн.)", "both": "Оба режима"}
    return (
        f"⚙️ *Настройки напоминаний*\n\n"
        f"🌅 Утренний дайджест: {s.get('morning_hour', 9)}:00 [{morning_status}]\n"
        f"🌙 Вечерний дайджест: {s.get('evening_hour', 20)}:00 [{evening_status}]\n"
        f"📊 Режим дедлайнов: {mode_label.get(mode, mode)}"
    )


def _settings_keyboard(s: dict):
    mh = s.get("morning_hour", 9)
    eh = s.get("evening_hour", 20)
    me = s.get("morning_enabled", 1)
    ee = s.get("evening_enabled", 1)
    mode = s.get("deadline_mode", "both")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("◀", callback_data="set_mh_-"),
            InlineKeyboardButton(f"🌅 {mh}:00", callback_data="noop"),
            InlineKeyboardButton("▶", callback_data="set_mh_+"),
            InlineKeyboardButton("✅" if me else "⬜", callback_data="toggle_morning"),
        ],
        [
            InlineKeyboardButton("◀", callback_data="set_eh_-"),
            InlineKeyboardButton(f"🌙 {eh}:00", callback_data="noop"),
            InlineKeyboardButton("▶", callback_data="set_eh_+"),
            InlineKeyboardButton("✅" if ee else "⬜", callback_data="toggle_evening"),
        ],
        [
            InlineKeyboardButton(
                f"{'✅' if mode == 'smart' else '⬜'} Умный",
                callback_data="set_mode_smart"
            ),
            InlineKeyboardButton(
                f"{'✅' if mode == 'fixed' else '⬜'} Фикс.",
                callback_data="set_mode_fixed"
            ),
            InlineKeyboardButton(
                f"{'✅' if mode == 'both' else '⬜'} Оба",
                callback_data="set_mode_both"
            ),
        ],
        [InlineKeyboardButton("💾 Сохранить", callback_data="save_settings")],
    ])


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "noop":
        return

    settings = db.get_user_settings(user_id)

    if data == "set_mh_+":
        settings["morning_hour"] = (settings.get("morning_hour", 9) + 1) % 24
    elif data == "set_mh_-":
        settings["morning_hour"] = (settings.get("morning_hour", 9) - 1) % 24
    elif data == "set_eh_+":
        settings["evening_hour"] = (settings.get("evening_hour", 20) + 1) % 24
    elif data == "set_eh_-":
        settings["evening_hour"] = (settings.get("evening_hour", 20) - 1) % 24
    elif data == "toggle_morning":
        settings["morning_enabled"] = 0 if settings.get("morning_enabled", 1) else 1
    elif data == "toggle_evening":
        settings["evening_enabled"] = 0 if settings.get("evening_enabled", 1) else 1
    elif data.startswith("set_mode_"):
        settings["deadline_mode"] = data.replace("set_mode_", "")
    elif data == "save_settings":
        db.save_user_settings(user_id, **{k: v for k, v in settings.items() if k != "user_id"})
        await query.edit_message_text(
            mot.get_settings_saved() + "\n\n" + _settings_text(settings),
            parse_mode="Markdown"
        )
        return

    await query.edit_message_text(
        _settings_text(settings),
        parse_mode="Markdown",
        reply_markup=_settings_keyboard(settings)
    )


# ========================
# Обработка свободного текста
# ========================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Маршрутизация: коуч-режим или помощь."""
    if context.user_data.get("coach_mode"):
        await handle_coach_message(update, context)
        return

    text = update.message.text
    if text and text.startswith("/"):
        return

    # Если пользователь пишет что-то не из меню
    await update.message.reply_text(
        "Используй кнопки меню ниже 👇\nИли /help для списка команд.",
        reply_markup=get_main_keyboard()
    )


# ========================
# Сборка приложения
# ========================

def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()

    # Создание цели
    goal_conv = ConversationHandler(
        entry_points=[
            CommandHandler("newgoal", newgoal_start),
            CallbackQueryHandler(lambda u, c: newgoal_start(u, c), pattern="^new_goal$"),
        ],
        states={
            GOAL_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_title_received)],
            GOAL_DESCRIPTION: [
                CommandHandler("skip", goal_description_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_description_received),
            ],
            GOAL_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_deadline_received)],
            GOAL_MILESTONES_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_milestones_count_received)],
            GOAL_MILESTONE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_milestone_title_received)],
            GOAL_CONFIRM: [CallbackQueryHandler(goal_confirm_callback, pattern="^(confirm_goal|cancel_goal_creation)$")],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    # Анализ личности
    analyze_conv = build_analyze_conversation()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(goal_conv)
    app.add_handler(analyze_conv)

    # Кнопки главного меню
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^(🎯 Мои цели и проекты|⚡ Фокус на сегодня|✅ Отметить прогресс|🔥 Энергия и драйв|🤖 Коуч|🌟 Звёзды сегодня|💎 PRO-доступ)$"),
        handle_menu_button
    ))

    # Настройки (callback)
    app.add_handler(CallbackQueryHandler(
        settings_callback,
        pattern="^(set_mh_|set_eh_|toggle_morning|toggle_evening|set_mode_|save_settings|noop)"
    ))

    # Оплата
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Все остальные callback
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Свободный текст (коуч и прочее)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app
