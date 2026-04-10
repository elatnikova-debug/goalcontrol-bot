"""
Коуч-Трекер Telegram Bot — версия 2.3
Главное меню на кнопках. 3 тарифа. Коуч с лимитом + анкета.
Умные триггеры. Астро-советы. Предложение закрепить. SMART-подсказки.
Редактирование целей и этапов.
"""

import os

BOT_VERSION = "2.4.4"

# ========================
# Админ
# ========================
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
import logging

from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
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
# Цены — 3 тарифа
# ========================
LITE_PRICE_STARS = db.LITE_PRICE_STARS        # 350 Stars ≈ $7/мес
PRO_PRICE_STARS = db.PRO_PRICE_STARS          # 750 Stars ≈ $15/мес
PRO_SUB_PRICE_STARS = db.PRO_SUB_PRICE_STARS  # 1450 Stars ≈ $29/мес
ANALYSIS_PRICE_STARS = int(os.getenv("ANALYSIS_PRICE_STARS", "500"))  # 500 Stars ≈ $10

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

# Состояния анкеты коуча
(CQ_BUSINESS_AREA, CQ_EXPERIENCE, CQ_CHALLENGE, CQ_TEAM_SIZE, CQ_REVENUE) = range(200, 205)

creating_goals = {}

# Хранилище данных анкеты (по user_id)
cq_data_store = {}


# ========================
# Главное меню
# ========================

def get_main_keyboard():
    """Reply-клавиатура — главное меню."""
    keyboard = [
        [KeyboardButton("🎯 Мои цели и проекты"), KeyboardButton("⚡ Фокус на сегодня")],
        [KeyboardButton("✅ Отметить прогресс"), KeyboardButton("🔥 Энергия и драйв")],
        [KeyboardButton("🤖 Коуч"), KeyboardButton("🔮 Персональный разбор")],
        [KeyboardButton("💎 PRO-доступ")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


# ========================
# /start
# ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("=== START HANDLER CALLED ===")
    try:
        user = update.effective_user
        logger.info(f"User: {user.id} / {user.first_name}")
        db.ensure_user(user.id, user.username, user.first_name)

        name = user.first_name or "предприниматель"

        try:
            tier = db.get_user_tier(user.id)
            tier_labels = {
                "free": "🆓 FREE-доступ — бесплатно навсегда. Хочешь больше? /subscribe",
                "lite": "⭐ LITE-доступ: активен",
                "pro": "💎 PRO-доступ: активен",
                "premium": "👑 PREMIUM-доступ: активен",
            }
            tier_text = tier_labels.get(tier, "")
        except Exception as e:
            logger.error(f"Error checking tier: {e}")
            tier_text = ""

        text = (
            f"Привет, {name}! 🚀\n\n"
            f"Я твой персональный коуч-трекер для предпринимателей.\n"
            f"Помогаю ставить цели, держать фокус и расти быстрее.\n\n"
            f"{tier_text}\n\n"
            f"Выбери действие в меню ниже 👇\n"
            f"\n• v{BOT_VERSION}"
        )

        kb = get_main_keyboard()
        logger.info(f"Keyboard created: {type(kb).__name__}, buttons={len(kb.keyboard)} rows")

        await update.message.reply_text(
            text.strip(),
            reply_markup=kb
        )
        logger.info("=== START MESSAGE SENT WITH KEYBOARD ===")

        # Предложение закрепить бот (один раз при первом входе)
        try:
            user_data = db.get_user(user.id)
            if user_data:
                created = datetime.fromisoformat(user_data["created_at"])
                if (datetime.utcnow() - created).total_seconds() < 60:
                    await update.message.reply_text(
                        "📌 *Совет:* Закрепи этот чат вверху списка диалогов — "
                        "так ты не пропустишь ни одно напоминание и мотивацию.\n\n"
                        "Для этого: зажми этот чат → Закрепить 📌",
                        parse_mode="Markdown"
                    )
        except Exception as e:
            logger.error(f"Error in pin suggestion: {e}")

    except Exception as e:
        logger.error(f"CRITICAL ERROR in start(): {e}", exc_info=True)
        # Аварийная отправка — без базы, без ничего, просто клавиатура
        try:
            await update.message.reply_text(
                "Привет! 🚀 Выбери действие в меню ниже 👇",
                reply_markup=get_main_keyboard()
            )
        except Exception as e2:
            logger.error(f"FATAL: even fallback start failed: {e2}", exc_info=True)


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
    elif text == "🔮 Персональный разбор":
        await personal_analysis_menu(update, context)
    elif text == "💎 PRO-доступ":
        await show_pro_menu(update, context)
    elif text == "🏠 Главное меню":
        await update.message.reply_text(
            "Главное меню 👇",
            reply_markup=get_main_keyboard()
        )


# ========================
# 🎯 Мои цели и проекты
# ========================

async def show_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    goals = db.get_active_goals(user_id)

    if not goals:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать первую цель", callback_data="new_goal")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
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
    keyboard_buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard_buttons)
    )


async def show_goal_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, goal_id: int):
    query = update.callback_query
    goal = db.get_goal(goal_id)
    if not goal:
        logger.warning("Goal not found: goal_id=%s, user_id=%s", goal_id, query.from_user.id)
        await query.edit_message_text(
            "Эта цель была удалена или не найдена.\n\n"
            "Нажми 🎯 Мои цели и проекты в меню, чтобы увидеть актуальные цели."
        )
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
        [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{goal_id}")],
        [InlineKeyboardButton("🏁 Завершить цель", callback_data=f"complete_{goal_id}")],
        [InlineKeyboardButton("❌ Отменить цель", callback_data=f"cancel_{goal_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_goals"),
         InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Создать цель", callback_data="new_goal")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
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

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())


# ========================
# ✅ Отметить прогресс
# ========================

async def mark_progress_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    milestones = db.get_pending_milestones_for_user(user_id)

    if not milestones:
        await update.message.reply_text(
            "✅ Нет незавершённых этапов. Все цели выполнены — или пора создать новые!",
            reply_markup=get_main_keyboard()
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
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")])

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
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=more_buttons)


# ========================
# 🔮 Персональный разбор
# ========================

ANALYSIS_MARKETING_TEXT = (
    "🔮 *Персональный разбор — твой уникальный код*"
    + chr(10) + chr(10)
    + "Это не гороскоп из интернета. Это глубокий анализ именно тебя:"
    + chr(10) + chr(10)
    + "✨ Астрология — твои сильные планеты и периоды роста"
    + chr(10)
    + "🤲 Хиромантия — линии судьбы и таланты на ладонях"
    + chr(10)
    + "🧠 Нумерология — твой жизненный путь и предназначение"
    + chr(10)
    + "👁 Психотип по лицу — как ты принимаешь решения"
    + chr(10) + chr(10)
    + "Результат: персональный отчёт на 2000+ слов, который остаётся с тобой навсегда."
    + chr(10) + chr(10)
    + "💎 Разовая оплата — 500 Stars (~$10)"
    + chr(10)
    + "После оплаты результаты сохраняются и доступны в любое время."
)


async def personal_analysis_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Персональный разбор — платный одноразовый анализ."""
    user_id = update.effective_user.id

    # Если уже есть оплаченный анализ — показать результат
    if db.get_has_analysis(user_id):
        profile = db.get_user_profile(user_id)
        if profile and profile.get("analysis_result"):
            await update.message.reply_text(
                "🔮 *Твой персональный разбор уже готов!*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Показать мой разбор", callback_data="show_paid_analysis")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                ])
            )
            return

    # Если у пользователя PRO/PREMIUM — анализ включён бесплатно
    tier = db.get_user_tier(user_id)
    if tier in ("pro", "premium"):
        await update.message.reply_text(
            "🔮 *Персональный разбор*"
            + chr(10) + chr(10)
            + "У тебя " + ("💎 PRO" if tier == "pro" else "👑 PREMIUM")
            + "-подписка — персональный разбор включён бесплатно!"
            + chr(10) + chr(10)
            + "Нажми кнопку ниже, чтобы начать анализ.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔮 Начать разбор", callback_data="start_free_analysis")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
        return

    # Показываем маркетинговый текст и кнопку оплаты
    await update.message.reply_text(
        ANALYSIS_MARKETING_TEXT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💳 Оплатить 500 Stars",
                callback_data="analysis_buy"
            )],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
        ])
    )


async def send_analysis_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправить счёт на оплату персонального разбора."""
    query = update.callback_query
    user_id = query.from_user.id

    # Проверка — вдруг уже оплачен
    if db.get_has_analysis(user_id):
        await query.edit_message_text(
            "✨ У тебя уже есть персональный разбор! Используй /analyze чтобы посмотреть."
        )
        return

    # Если PRO/PREMIUM — бесплатно
    tier = db.get_user_tier(user_id)
    if tier in ("pro", "premium"):
        db.set_has_analysis(user_id)
        await query.edit_message_text(
            "✨ Разбор активирован бесплатно по твоей подписке!"
            + chr(10)
            + "Запусти /analyze чтобы начать."
        )
        return

    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title="🔮 Персональный разбор",
            description="Глубокий анализ: астрология + хиромантия + нумерология + психотип. Навсегда.",
            payload="personal_analysis",
            currency="XTR",
            prices=[LabeledPrice(label="Персональный разбор", amount=ANALYSIS_PRICE_STARS)],
        )
    except Exception as e:
        logger.error(f"Analysis invoice error: {e}")
        await query.message.reply_text(
            "Ошибка при создании счёта. Попробуй через минуту.",
            reply_markup=get_main_keyboard()
        )


async def start_free_analysis_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PRO/PREMIUM пользователь начинает бесплатный анализ."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db.set_has_analysis(user_id)
    await query.edit_message_text(
        "✨ Персональный разбор активирован!"
        + chr(10) + chr(10)
        + "Запусти /analyze — я задам несколько вопросов и проведу глубокий анализ."
    )


async def show_paid_analysis_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать сохранённый оплаченный разбор."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    profile = db.get_user_profile(user_id)
    if not profile or not profile.get("analysis_result"):
        await query.edit_message_text(
            "Разбор ещё не выполнен. Запусти /analyze чтобы начать."
        )
        return

    await query.edit_message_text("📋 Загружаю твой разбор...")
    result = profile["analysis_result"]
    chat_id = query.message.chat.id
    for i in range(0, len(result), 4000):
        chunk = result[i:i + 4000]
        await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")


# ========================
# 🤖 Коуч — анкета (5 вопросов)
# ========================

async def start_coach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Точка входа: кнопка 🤖 Коуч — проверяем анкету, лимиты, запускаем."""
    user_id = update.effective_user.id
    has_paid = db.has_lite_access(user_id)
    remaining = db.FREE_COACH_MESSAGES_PER_DAY - db.get_coach_messages_today(user_id)

    if not has_paid and remaining <= 0:
        await update.message.reply_text(
            mot.COACH_LIMIT_REACHED,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ LITE — 350 Stars/мес", callback_data="lite_buy")],
                [InlineKeyboardButton("💎 PRO — 750 Stars/мес", callback_data="pro_buy")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
        return ConversationHandler.END

    # Проверяем анкету — если не пройдена, запускаем ConversationHandler
    if not db.has_coach_questionnaire(user_id):
        cq_data_store[user_id] = {}
        await update.message.reply_text(
            "🤖 *Перед началом — короткая анкета*\n\n"
            "Чтобы коуч работал максимально эффективно, "
            "мне нужно понять контекст твоего бизнеса.\n"
            "5 вопросов — займёт 1 минуту.\n\n"
            "📌 *Вопрос 1/5:* В какой сфере твой бизнес?\n\n"
            "Напиши кратко, например: IT, маркетинг, ресторан, строительство, e-commerce...",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return CQ_BUSINESS_AREA

    # Анкета пройдена — открываем коуч
    if has_paid:
        coach_text = (
            "🤖 *Коуч на связи*\n\n"
            "Я твой персональный AI-коуч. Задавай любые вопросы:\n"
            "• Как разблокироваться если застрял\n"
            "• Как расставить приоритеты\n"
            "• Стратегия роста бизнеса\n"
            "• Работа с командой и делегирование\n\n"
            "👑 У тебя подписка — без ограничений. Что тебя беспокоит прямо сейчас?"
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
    return ConversationHandler.END


# --- Анкета: 5 шагов ---

async def cq_business_area(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    cq_data_store.setdefault(user_id, {})["business_area"] = update.message.text.strip()
    await update.message.reply_text(
        "📌 *Вопрос 2/5:* Сколько лет ты в бизнесе?\n\n"
        "Напиши примерно: меньше года, 1-3 года, 3-5 лет, 5-10 лет, больше 10 лет",
        parse_mode="Markdown"
    )
    return CQ_EXPERIENCE


async def cq_experience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    cq_data_store.setdefault(user_id, {})["experience_years"] = update.message.text.strip()
    await update.message.reply_text(
        "📌 *Вопрос 3/5:* Какой главный вызов или проблема в бизнесе прямо сейчас?\n\n"
        "Напиши одно предложение — то, что больше всего мешает расти.",
        parse_mode="Markdown"
    )
    return CQ_CHALLENGE


async def cq_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    cq_data_store.setdefault(user_id, {})["main_challenge"] = update.message.text.strip()
    await update.message.reply_text(
        "📌 *Вопрос 4/5:* Размер команды?\n\n"
        "Один, 2-5 человек, 6-20, 21-50, больше 50",
        parse_mode="Markdown"
    )
    return CQ_TEAM_SIZE


async def cq_team_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    cq_data_store.setdefault(user_id, {})["team_size"] = update.message.text.strip()
    await update.message.reply_text(
        "📌 *Вопрос 5/5:* Примерный годовой оборот?\n\n"
        "Только начинаю, до $100K, $100K-$500K, $500K-$1M, больше $1M\n\n"
        "_(Информация конфиденциальна и используется только для коуч-сессий)_",
        parse_mode="Markdown"
    )
    return CQ_REVENUE


async def cq_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    cq_data_store.setdefault(user_id, {})["annual_revenue"] = update.message.text.strip()
    data = cq_data_store.pop(user_id, {})

    # Сохраняем анкету
    db.save_coach_questionnaire(
        user_id,
        business_area=data.get("business_area", ""),
        experience_years=data.get("experience_years", ""),
        main_challenge=data.get("main_challenge", ""),
        team_size=data.get("team_size", ""),
        annual_revenue=data.get("annual_revenue", ""),
    )

    has_paid = db.has_lite_access(user_id)
    remaining = db.FREE_COACH_MESSAGES_PER_DAY - db.get_coach_messages_today(user_id)

    if has_paid:
        coach_text = (
            "✅ Анкета сохранена! Теперь коуч знает твой контекст.\n\n"
            "🤖 *Коуч на связи*\n\n"
            "👑 У тебя подписка — без ограничений. Что тебя беспокоит прямо сейчас?"
        )
    else:
        coach_text = (
            "✅ Анкета сохранена! Теперь коуч знает твой контекст.\n\n"
            + mot.COACH_WELCOME.format(remaining=remaining)
        )

    context.user_data["coach_mode"] = True
    await update.message.reply_text(
        coach_text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🏠 Главное меню")]],
            resize_keyboard=True
        )
    )
    return ConversationHandler.END


# ========================
# Коуч — обработка сообщений
# ========================

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
    has_paid = db.has_lite_access(user_id)

    if not has_paid:
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
    if not has_paid:
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

    # Получаем анкету для контекста
    questionnaire = db.get_coach_questionnaire(user_id)
    quest_context = ""
    if questionnaire:
        quest_context = (
            f"\n\nКонтекст предпринимателя (из анкеты):"
            f"\n- Сфера бизнеса: {questionnaire.get('business_area', 'не указана')}"
            f"\n- Опыт: {questionnaire.get('experience_years', 'не указан')}"
            f"\n- Главный вызов: {questionnaire.get('main_challenge', 'не указан')}"
            f"\n- Команда: {questionnaire.get('team_size', 'не указана')}"
            f"\n- Оборот: {questionnaire.get('annual_revenue', 'не указан')}"
        )

    system_prompt = (
        "Ты — элитный коуч для предпринимателей. Мировой уровень. "
        "Объединяешь лучшие методологии: OKR, SMART, GTD, принципы Коллинза и Друкера, "
        "нейронауку мотивации, психологию достижений Дуэк, антихрупкость Талеба. "
        "Стиль: прямой, конкретный, без воды. Как разговор с умным другом-предпринимателем. "
        "Даёшь конкретные действия, а не общие советы. "
        "Отвечаешь только на русском языке. Максимум 300 слов за ответ."
        + goals_context
        + quest_context
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
            timeout=60,
        )

        reply = response.choices[0].message.content
        db.save_coach_message(user_id, "assistant", reply)

        remaining_after = db.FREE_COACH_MESSAGES_PER_DAY - db.get_coach_messages_today(user_id)

        footer = ""
        if not has_paid and remaining_after <= 5:
            footer = f"\n\n_Осталось сообщений сегодня: {remaining_after}_"
            if remaining_after <= 2:
                footer += "\n\n⭐ Хочешь без ограничений? Подключи подписку: /pro"

        coach_exit_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Главное меню", callback_data="exit_coach")]
        ])
        await update.message.reply_text(
            reply + footer,
            parse_mode="Markdown",
            reply_markup=coach_exit_kb
        )

    except Exception as e:
        logger.error("Coach GPT error: %s: %s", type(e).__name__, e, exc_info=True)
        coach_exit_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Главное меню", callback_data="exit_coach")]
        ])
        await update.message.reply_text(
            "Что-то пошло не так. Попробуй отправить сообщение ещё раз или зайди чуть позже.",
            reply_markup=coach_exit_kb
        )


# ========================
# 💎 PRO-доступ — 3 тарифа
# ========================

async def show_pro_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tier = db.get_user_tier(user_id)

    if tier == "premium":
        await update.message.reply_text(
            "👑 *У тебя PREMIUM-доступ — всё включено!*\n\n"
            "Безлимитный коуч, профиль, профайлинг, стратегия роста, "
            "еженедельные разборы и приоритетная поддержка.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🧠 Мой профиль", callback_data="pro_profile"),
                 InlineKeyboardButton("🧪 Профайлинг", callback_data="pro_tests")],
                [InlineKeyboardButton("🚀 Стратегия роста", callback_data="pro_roadmap")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
    elif tier == "pro":
        await update.message.reply_text(
            "💎 *У тебя PRO-доступ!*\n\n"
            "Доступны: безлимитный коуч, профиль, профайлинг, стратегия роста.\n\n"
            "Хочешь еженедельные AI-разборы и приоритет?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🧠 Профиль", callback_data="pro_profile"),
                 InlineKeyboardButton("🧪 Профайлинг", callback_data="pro_tests")],
                [InlineKeyboardButton("🚀 Стратегия роста", callback_data="pro_roadmap")],
                [InlineKeyboardButton("👑 PREMIUM — 1450 Stars/мес", callback_data="premium_buy")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
    elif tier == "lite":
        await update.message.reply_text(
            "⭐ *У тебя LITE-доступ!*\n\n"
            "Безлимитный коуч и персональные звёзды.\n\n"
            "Хочешь профиль, профайлинг и стратегию роста?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 PRO — 750 Stars/мес", callback_data="pro_buy")],
                [InlineKeyboardButton("👑 PREMIUM — 1450 Stars/мес", callback_data="premium_buy")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
    else:
        # free
        await update.message.reply_text(
            mot.TARIFF_OVERVIEW,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ LITE — 350 Stars/мес", callback_data="lite_buy")],
                [InlineKeyboardButton("💎 PRO — 750 Stars/мес", callback_data="pro_buy")],
                [InlineKeyboardButton("👑 PREMIUM — 1450 Stars/мес", callback_data="premium_buy")],
                [InlineKeyboardButton("❓ Подробнее о тарифах", callback_data="tariff_info")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )


# ========================
# Callback-обработчики
# ========================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    # Ветки, которым нужен свой query.answer() с текстом — обрабатываем ДО blanket answer
    if data.startswith("ed_del_ok_"):
        ms_id = int(data.split("_")[3])
        ms = db.get_milestone(ms_id)
        if ms:
            goal_id = ms["goal_id"]
            db.delete_milestone(ms_id)
            await query.answer("🗑 Этап удалён.")
            goal = db.get_goal(goal_id)
            if goal:
                await show_goal_detail(update, context, goal_id)
        else:
            await query.answer("Этап уже удалён.")
        return

    await query.answer()

    # --- Редактирование целей ---
    if data.startswith("edit_") and not data.startswith("edit_conv"):
        goal_id = int(data.split("_")[1])
        goal = db.get_goal(goal_id)
        if not goal:
            logger.warning("Edit: goal not found: goal_id=%s, user_id=%s", goal_id, user_id)
            await query.edit_message_text(
                "Эта цель была удалена или не найдена.\n\n"
                "Нажми 🎯 Мои цели и проекты в меню."
            )
            return
        milestones = db.get_milestones(goal_id)
        buttons = []
        for m in milestones:
            buttons.append([
                InlineKeyboardButton(f"✏️ {m['title'][:25]}", callback_data=f"ed_rename_{m['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"ed_del_{m['id']}"),
            ])
        buttons.append([InlineKeyboardButton("➕ Добавить этап", callback_data=f"ed_add_{goal_id}")])
        buttons.append([InlineKeyboardButton("📅 Изменить дедлайн", callback_data=f"ed_deadline_{goal_id}")])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data=f"goal_{goal_id}")])
        await query.edit_message_text(
            f"✏️ *Редактирование: {goal['title']}*\n\n"
            "Выбери действие:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("ed_rename_"):
        ms_id = int(data.split("_")[2])
        ms = db.get_milestone(ms_id)
        if not ms:
            logger.warning("Rename: milestone not found: ms_id=%s, user_id=%s", ms_id, user_id)
            await query.edit_message_text(
                "Этап не найден. Возможно, он был удалён.\n\n"
                "Нажми 🎯 Мои цели и проекты в меню."
            )
            return
        context.user_data["edit_rename_ms_id"] = ms_id
        context.user_data["edit_rename_goal_id"] = ms["goal_id"]
        await query.edit_message_text(
            f"Текущее название: *{ms['title']}*\n\nНапиши новое название этапа:",
            parse_mode="Markdown"
        )
        return

    elif data.startswith("ed_del_ms_"):
        # Это не используется, но на всякий случай
        pass

    elif data.startswith("ed_del_"):
        ms_id = int(data.split("_")[2])
        ms = db.get_milestone(ms_id)
        if not ms:
            logger.warning("Delete: milestone not found: ms_id=%s, user_id=%s", ms_id, user_id)
            await query.edit_message_text(
                "Этап не найден. Возможно, он уже был удалён.\n\n"
                "Нажми 🎯 Мои цели и проекты в меню."
            )
            return
        await query.edit_message_text(
            f"Удалить этап *{ms['title']}*?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да, удалить", callback_data=f"ed_del_ok_{ms_id}")],
                [InlineKeyboardButton("◀️ Отмена", callback_data=f"edit_{ms['goal_id']}")],
            ])
        )

    elif data.startswith("ed_add_"):
        goal_id = int(data.split("_")[2])
        context.user_data["edit_add_goal_id"] = goal_id
        await query.edit_message_text(
            "Напиши название нового этапа:"
        )

    elif data.startswith("ed_deadline_"):
        goal_id = int(data.split("_")[2])
        goal = db.get_goal(goal_id)
        context.user_data["edit_deadline_goal_id"] = goal_id
        await query.edit_message_text(
            f"Текущий дедлайн: *{goal['deadline'] if goal else '—'}*\n\n"
            "Напиши новый дедлайн в формате ДД.ММ.ГГГГ:",
            parse_mode="Markdown"
        )

    # --- Навигация по целям ---
    elif data == "new_goal":
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
        buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")])
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
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
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
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
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
        if not goal:
            await query.edit_message_text(
                "Эта цель была удалена или не найдена.\n\n"
                "Нажми 🎯 Мои цели и проекты в меню."
            )
            return
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
    elif data.startswith("cancel_") and data.split("_")[1].isdigit():
        goal_id = int(data.split("_")[1])
        goal = db.get_goal(goal_id)
        if not goal:
            await query.edit_message_text(
                "Эта цель была удалена или не найдена.\n\n"
                "Нажми 🎯 Мои цели и проекты в меню."
            )
            return
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

    # --- 3 тарифа: покупка ---
    elif data == "lite_buy":
        await send_pro_invoice(update, context, "lite_sub")

    elif data == "pro_buy":
        await send_pro_invoice(update, context, "pro_sub")

    elif data in ("premium_buy", "pro_sub_buy"):
        await send_pro_invoice(update, context, "premium_sub")

    elif data in ("tariff_info", "pro_info"):
        await query.edit_message_text(
            mot.TARIFF_OVERVIEW + "\n\n" + mot.LITE_OFFER + "\n\n" + mot.PRO_OFFER + "\n\n" + mot.PRO_SUB_OFFER,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ LITE — 350 Stars/мес", callback_data="lite_buy")],
                [InlineKeyboardButton("💎 PRO — 750 Stars/мес", callback_data="pro_buy")],
                [InlineKeyboardButton("👑 PREMIUM — 1450 Stars/мес", callback_data="premium_buy")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
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
            "После этого ты получишь подробный профиль с персональной стратегией.",
            parse_mode="Markdown"
        )

    elif data == "analysis_buy":
        await send_analysis_invoice(update, context)

    elif data == "start_free_analysis":
        await start_free_analysis_callback(update, context)

    elif data == "show_paid_analysis":
        await show_paid_analysis_callback(update, context)

    elif data in ("exit_coach", "menu_main"):
        context.user_data.pop("coach_mode", None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "Возвращаемся в главное меню 👇",
            reply_markup=get_main_keyboard()
        )

    elif data == "start_coach_from_trigger":
        # Активируем коуч-режим
        context.user_data["coach_mode"] = True
        has_paid = db.has_lite_access(user_id)
        if has_paid:
            coach_text = (
                "🤖 *Коуч на связи*\n\n"
                "👑 У тебя подписка — без ограничений. Что тебя беспокоит прямо сейчас?"
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 PRO — 750 Stars/мес", callback_data="pro_buy")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔮 Начать анализ", callback_data="start_analyze")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )


async def handle_pro_tests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not db.has_pro_access(user_id):
        await query.edit_message_text(
            "💎 Эта функция доступна в PRO-доступе.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 PRO — 750 Stars/мес", callback_data="pro_buy")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 PRO — 750 Stars/мес", callback_data="pro_buy")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
        return

    goals = db.get_active_goals(user_id)
    profile = db.get_user_profile(user_id)

    if not goals:
        await query.edit_message_text(
            "🚀 Для стратегии роста сначала создай хотя бы одну цель.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Создать цель", callback_data="new_goal")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
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

        goals_text = chr(10).join(f"- {g['title']} (дедлайн: {g['deadline']})" for g in goals)
        analysis = profile.get("analysis_result", "") if profile else ""

        prompt = (
            f"Ты — элитный бизнес-стратег и коуч для предпринимателей.\n\n"
            f"Активные цели предпринимателя:\n{goals_text}\n\n"
            f"{('Анализ личности:' + chr(10) + analysis[:1500]) if analysis else ''}\n\n"
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
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )

    except Exception as e:
        logger.error(f"Roadmap GPT error: {e}")
        await query.message.reply_text(
            "Ошибка при генерации стратегии. Попробуй через минуту.",
            reply_markup=get_main_keyboard()
        )


# ========================
# Оплата — 3 тарифа
# ========================

async def send_pro_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, product: str):
    query = update.callback_query
    user_id = query.from_user.id

    products = {
        "lite_sub": {
            "title": "⭐ LITE-подписка",
            "description": "Безлимитный коуч 24/7 + персональные астро-советы. 30 дней.",
            "price": LITE_PRICE_STARS,
        },
        "pro_sub": {
            "title": "💎 PRO-подписка",
            "description": "Всё из LITE + профиль + профайлинг + стратегия роста. 30 дней.",
            "price": PRO_PRICE_STARS,
        },
        "premium_sub": {
            "title": "👑 PREMIUM-подписка",
            "description": "Всё из PRO + еженедельные AI-разборы + приоритетная поддержка. 30 дней.",
            "price": PRO_SUB_PRICE_STARS,
        },
    }

    info = products.get(product)
    if not info:
        await query.message.reply_text("Неизвестный тариф.")
        return

    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=info["title"],
            description=info["description"],
            payload=product,
            currency="XTR",
            prices=[LabeledPrice(label=info["title"], amount=info["price"])],
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

    # Сохраняем платёж
    db.save_payment(user_id, charge_id, "", amount, "XTR", payload)

    # Персональный разбор — разовая покупка
    if payload == "personal_analysis":
        db.set_has_analysis(user_id)
        await update.message.reply_text(
            "🎉 *Оплата прошла! Персональный разбор активирован!*"
            + chr(10) + chr(10)
            + "Теперь запусти /analyze — я задам несколько вопросов "
            + "и проведу глубокий анализ твоей личности."
            + chr(10) + chr(10)
            + "Результат сохранится навсегда.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    tier_labels = {
        "lite_sub": ("⭐", "LITE"),
        "pro_sub": ("💎", "PRO"),
        "premium_sub": ("👑", "PREMIUM"),
    }

    icon, label = tier_labels.get(payload, ("💎", "PRO"))

    # Активируем подписку
    db.activate_subscription(user_id, days=30)
    db.save_pro_purchase(user_id, payload, charge_id, amount)

    await update.message.reply_text(
        f"🎉 *{icon} {label}-подписка активирована на 30 дней!*"
        + chr(10) + chr(10)
        + f"Все функции тарифа {label} теперь твои."
        + chr(10) + chr(10)
        + "Нажми 🤖 Коуч — никаких ограничений.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


# ========================
# /newgoal — SMART-подсказка
# ========================

async def newgoal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    db.ensure_user(user_id)
    creating_goals[user_id] = {}

    smart_text = (
        "🎯 *Новая цель или проект*\n\n"
        "💡 *Совет: используй метод SMART*\n\n"
        "Правильно поставленная цель — уже половина успеха. "
        "Исследования показывают: предприниматели, которые формулируют цели по SMART, "
        "достигают их в 2-3 раза чаще.\n\n"
        "✅ *S* — Конкретная (что именно?)\n"
        "✅ *M* — Измеримая (как понять, что достигнута?)\n"
        "✅ *A* — Достижимая (реально ли за этот срок?)\n"
        "✅ *R* — Релевантная (зачем это бизнесу?)\n"
        "✅ *T* — Ограниченная по времени (когда дедлайн?)\n\n"
        "❌ Плохо: «Увеличить продажи»\n"
        "✅ Хорошо: «Увеличить выручку на 30% за 3 месяца через запуск нового канала»\n\n"
        "Как называется твоя цель? Напиши кратко и ёмко 👇"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            smart_text,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            smart_text,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
    return GOAL_TITLE


async def goal_title_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id not in creating_goals:
        await update.message.reply_text("Сессия создания цели истекла. Используй /newgoal чтобы начать заново.")
        return ConversationHandler.END
    creating_goals[user_id]["title"] = update.message.text
    await update.message.reply_text(
        "Отлично! Теперь коротко опиши — что именно ты хочешь достичь? (или /skip)"
    )
    return GOAL_DESCRIPTION


async def goal_description_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id not in creating_goals:
        await update.message.reply_text("Сессия создания цели истекла. Используй /newgoal чтобы начать заново.")
        return ConversationHandler.END
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
    if user_id not in creating_goals:
        await update.message.reply_text("Сессия создания цели истекла. Используй /newgoal чтобы начать заново.")
        return ConversationHandler.END
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
    if user_id not in creating_goals:
        await update.message.reply_text("Сессия создания цели истекла. Используй /newgoal чтобы начать заново.")
        return ConversationHandler.END
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
    if user_id not in creating_goals:
        await update.message.reply_text("Сессия создания цели истекла. Используй /newgoal чтобы начать заново.")
        return ConversationHandler.END
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
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ])
    await context.bot.send_message(
        chat_id=user_id,
        text=trigger_text,
        reply_markup=trigger_buttons
    )

    # === Предложение закрепить бот ===
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "📌 *Совет:* Закрепи этот чат, чтобы видеть напоминания и не терять фокус.\n\n"
            "Зажми чат → Закрепить 📌"
        ),
        parse_mode="Markdown"
    )

    # Возвращаем главное меню
    await context.bot.send_message(
        chat_id=user_id,
        text="Главное меню 👇",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# ========================
# /stats — с тарифами
# ========================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug("stats_command: user_id=%s, ADMIN_ID=%s, match=%s",
                 user_id, ADMIN_ID, user_id == ADMIN_ID)

    # Если это админ — показываем дашборд бота
    if ADMIN_ID and user_id == ADMIN_ID:
        try:
            await _admin_stats(update)
        except Exception as e:
            logger.error("_admin_stats error: %s", e, exc_info=True)
            await update.message.reply_text(f"❌ Ошибка админ-статистики: {e}")
        return

    # Обычный пользователь — личная статистика
    stats = db.get_user_stats(user_id)
    sub = db.get_subscription_status(user_id)
    tier = db.get_user_tier(user_id)

    if sub["status"] == "active":
        sub_text = f"👑 Подписка до {sub['expires_at']}"
    else:
        sub_text = "🆓 FREE — бесплатно навсегда"

    tier_labels = {
        "free": "🆓 FREE",
        "lite": "⭐ LITE",
        "pro": "💎 PRO",
        "premium": "👑 PREMIUM",
    }
    tier_text = tier_labels.get(tier, "🆓 FREE")

    text = (
        f"📊 *Твоя статистика*\n\n"
        f"🎯 Целей создано: {stats['total_goals']}\n"
        f"✅ Целей достигнуто: {stats['completed_goals']}\n"
        f"🔄 Активных проектов: {stats['active_goals']}\n\n"
        f"📌 Этапов выполнено: {stats['completed_milestones']} из {stats['total_milestones']}\n"
        f"🔥 Стрик: {stats['streak']} дней\n\n"
        f"💳 Статус: {sub_text}\n"
        f"🏷 Тариф: {tier_text}"
    )

    await update.message.reply_text(text.strip(), parse_mode="Markdown", reply_markup=get_main_keyboard())


async def _admin_stats(update: Update):
    """Дашборд администратора с полной аналитикой бота."""
    s = db.get_admin_stats()

    lines = [
        "📊 Статистика бота",
        "",
        "👥 Пользователи",
        "├ Всего: {}".format(s["total_users"]),
        "├ Активных (7д): {}".format(s["active_7d"]),
        "├ Активных (30д): {}".format(s["active_30d"]),
        "├ Новых сегодня: {}".format(s["new_today"]),
        "└ Новых (7д): {}".format(s["new_7d"]),
        "",
        "🎯 Цели",
        "├ Всего: {}".format(s["total_goals"]),
        "├ Активных: {}".format(s["active_goals"]),
        "└ Завершённых: {}".format(s["completed_goals"]),
        "",
        "💰 Тарифы",
        "├ FREE: {}".format(s["tier_free"]),
        "├ LITE ($7): {}".format(s["tier_lite"]),
        "├ PRO ($15): {}".format(s["tier_pro"]),
        "├ PREMIUM ($29): {}".format(s["tier_premium"]),
        "└ Выручка: {} Stars".format(s["total_revenue_stars"]),
        "",
        "🤖 Коуч",
        "├ Прошли анкету: {}".format(s["questionnaire_count"]),
        "└ Сообщений коучу: {}".format(s["total_coach_messages"]),
        "",
        "📈 Конверсия",
        "├ Создали цель: {}/{} ({:.1f}%)".format(
            s["users_with_goals"], s["total_users"], s["goal_conversion"]
        ),
        "└ Оплатили тариф: {}/{} ({:.1f}%)".format(
            s["users_with_payment"], s["total_users"], s["payment_conversion"]
        ),
    ]

    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode=None)


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
        "🔮 *Персональный разбор* — глубокий анализ личности\n"
        "💎 *PRO-доступ* — разблокировать мощные инструменты\n\n"
        "*Команды:*\n"
        "/newgoal — создать новую цель\n"
        "/analyze — анализ личности\n"
        "/stats — твоя статистика\n"
        "/settings — настройки напоминаний\n"
        "/start — главное меню\n\n"
        "*Тарифы:*\n"
        "⭐ LITE ($7/мес) — безлимит коуч\n"
        "💎 PRO ($15/мес) — + профиль + профайлинг + стратегия\n"
        "👑 PREMIUM ($29/мес) — + еженедельные разборы + приоритет"
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
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
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
    """Маршрутизация: коуч-режим, редактирование или помощь."""
    # Проверяем ожидание ввода для редактирования
    user_id = update.effective_user.id
    text = update.message.text

    # Переименование этапа
    if "edit_rename_ms_id" in context.user_data:
        ms_id = context.user_data.pop("edit_rename_ms_id")
        goal_id = context.user_data.pop("edit_rename_goal_id", None)
        db.rename_milestone(ms_id, text.strip())
        await update.message.reply_text("✅ Этап переименован!")
        if goal_id:
            # Отправляем обновлённый вид цели
            goal = db.get_goal(goal_id)
            if goal:
                milestones = db.get_milestones(goal_id)
                done = sum(1 for m in milestones if m["status"] == "completed")
                total = len(milestones)
                bar = mot.format_progress_bar(done, total)
                ms_text = ""
                for m in milestones:
                    icon = "✅" if m["status"] == "completed" else "⬜"
                    ms_text += f"{icon} {m['title']}\n"
                await update.message.reply_text(
                    f"🎯 *{goal['title']}*\n\nПрогресс: {bar}\n\n*Этапы:*\n{ms_text}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{goal_id}")],
                        [InlineKeyboardButton("◀️ К цели", callback_data=f"goal_{goal_id}")],
                        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                    ])
                )
        return

    # Добавление этапа
    if "edit_add_goal_id" in context.user_data:
        goal_id = context.user_data.pop("edit_add_goal_id")
        db.add_milestone_to_goal(goal_id, text.strip())
        await update.message.reply_text("✅ Этап добавлен!")
        goal = db.get_goal(goal_id)
        if goal:
            milestones = db.get_milestones(goal_id)
            done = sum(1 for m in milestones if m["status"] == "completed")
            total = len(milestones)
            bar = mot.format_progress_bar(done, total)
            ms_text = ""
            for m in milestones:
                icon = "✅" if m["status"] == "completed" else "⬜"
                ms_text += f"{icon} {m['title']}\n"
            await update.message.reply_text(
                f"🎯 *{goal['title']}*\n\nПрогресс: {bar}\n\n*Этапы:*\n{ms_text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{goal_id}")],
                    [InlineKeyboardButton("◀️ К цели", callback_data=f"goal_{goal_id}")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                ])
            )
        return

    # Изменение дедлайна
    if "edit_deadline_goal_id" in context.user_data:
        goal_id = context.user_data.pop("edit_deadline_goal_id")
        try:
            dl = datetime.strptime(text.strip(), "%d.%m.%Y").date()
            if dl <= datetime.now().date():
                await update.message.reply_text("Дедлайн должен быть в будущем. Попробуй ещё раз (ДД.ММ.ГГГГ):")
                context.user_data["edit_deadline_goal_id"] = goal_id
                return
            db.update_goal_deadline(goal_id, dl.strftime("%Y-%m-%d"))
            await update.message.reply_text(
                f"✅ Дедлайн изменён на {dl.strftime('%d.%m.%Y')}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К цели", callback_data=f"goal_{goal_id}")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                ])
            )
        except ValueError:
            await update.message.reply_text("Неверный формат. Используй ДД.ММ.ГГГГ:")
            context.user_data["edit_deadline_goal_id"] = goal_id
        return

    # Коуч-режим
    if context.user_data.get("coach_mode"):
        await handle_coach_message(update, context)
        return

    if text and text.startswith("/"):
        return

    # Если пользователь пишет что-то не из меню
    await update.message.reply_text(
        "Используй кнопки меню ниже 👇\nИли /help для списка команд.",
        reply_markup=get_main_keyboard()
    )


# ========================
# Обработчик ошибок
# ========================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error("Exception while handling an update:", exc_info=context.error)


# ========================
# Fallback /start для ConversationHandler
# ========================

async def start_and_end_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/start из conversation — показываем меню и ВЫХОДИМ из диалога."""
    logger.info("start_and_end_conversation called — exiting ConversationHandler")
    await start(update, context)
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancel — отмена любого диалога, возврат к меню."""
    logger.info("cancel_conversation called")
    await update.message.reply_text(
        "Отменено. Выбери действие в меню ниже 👇",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# ========================
# Обработчик кнопок меню внутри ConversationHandler
# ========================

MENU_BUTTONS_PATTERN = "^(🎯 Мои цели и проекты|⚡ Фокус на сегодня|✅ Отметить прогресс|🔥 Энергия и драйв|🤖 Коуч|🔮 Персональный разбор|💎 PRO-доступ|🏠 Главное меню)$"


async def menu_button_exits_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Кнопка меню нажата внутри conversation — выходим и обрабатываем."""
    logger.info(f"Menu button '{update.message.text}' pressed inside conversation — exiting")
    await handle_menu_button(update, context)
    return ConversationHandler.END


async def menu_main_exits_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inline-кнопка 🏠 Главное меню нажата внутри conversation — выходим."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop("coach_mode", None)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "Возвращаемся в главное меню 👇",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# ========================
# Сборка приложения
# ========================

def build_application(token: str) -> Application:
    from telegram.ext import PicklePersistence

    persistence = PicklePersistence(filepath="/data/bot_data.pickle")
    app = Application.builder().token(token).persistence(persistence).build()

    # Обработчик ошибок
    app.add_error_handler(error_handler)

    # Общие fallback'ы для ConversationHandler'ов:
    common_fallbacks = [
        CommandHandler("start", start_and_end_conversation),
        CommandHandler("cancel", cancel_conversation),
        MessageHandler(
            filters.TEXT & filters.Regex(MENU_BUTTONS_PATTERN),
            menu_button_exits_conversation
        ),
        CallbackQueryHandler(menu_main_exits_conversation, pattern="^menu_main$"),
    ]

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
        fallbacks=common_fallbacks,
        allow_reentry=True,
        name="goal_conv",
        persistent=True,
    )

    # Анкета коуча (BEFORE menu handler!)
    coach_quest_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex("^🤖 Коуч$"), start_coach),
        ],
        states={
            CQ_BUSINESS_AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, cq_business_area)],
            CQ_EXPERIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cq_experience)],
            CQ_CHALLENGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cq_challenge)],
            CQ_TEAM_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cq_team_size)],
            CQ_REVENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cq_revenue)],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
        name="coach_quest_conv",
        persistent=True,
    )

    # Анализ личности
    analyze_conv = build_analyze_conversation(extra_fallbacks=common_fallbacks)


    # === Порядок важен! ===
    # 1. Команды (они должны работать всегда, даже вне диалогов)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("settings", settings_command))

    # 2. ConversationHandler'ы (перехватывают текст когда активны)
    app.add_handler(coach_quest_conv)   # Анкета коуча — ДО menu handler
    app.add_handler(goal_conv)
    app.add_handler(analyze_conv)

    # 3. Кнопки главного меню
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(MENU_BUTTONS_PATTERN),
        handle_menu_button
    ))

    # 4. Настройки (callback)
    app.add_handler(CallbackQueryHandler(
        settings_callback,
        pattern="^(set_mh_|set_eh_|toggle_morning|toggle_evening|set_mode_|save_settings|noop)"
    ))

    # 5. Оплата
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # 6. Все остальные callback (включая edit_, ed_rename_, ed_del_ и т.д.)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # 7. Свободный текст (коуч, редактирование и прочее)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app
