"""
ConversationHandler для команды /analyze.
Последовательно собирает данные: ФИО → дата рождения → город → время.
Затем отправляет всё в GPT-4o и возвращает анализ личности.
"""

import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import database as db
import analyzer as ai
from analyzer import GPTRefusalError

logger = logging.getLogger(__name__)

# Состояния диалога
(
    ASK_CONSENT,
    ASK_NAME,
    ASK_BIRTHDATE,
    ASK_BIRTHCITY,
    ASK_BIRTHTIME,
    ANALYZING,
    ASK_FOLLOWUP,
) = range(7)

TOTAL_STEPS = 4  # шагов с данными (без согласия и анализа)

STEP_LABELS = [
    "ФИО",
    "Дата рождения",
    "Город рождения",
    "Время рождения",
]


def _progress(step: int) -> str:
    """Строка прогресса: Шаг 3 из 7 ████░░░░"""
    filled = "█" * step
    empty = "░" * (TOTAL_STEPS - step)
    return f"Шаг {step} из {TOTAL_STEPS} [{filled}{empty}]"


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Точка входа — /analyze."""
    user_id = update.effective_user.id

    # Проверить есть ли цели
    goals = db.get_active_goals(user_id)
    if not goals:
        await update.message.reply_text(
            "🎯 Сначала добавь хотя бы одну цель — /newgoal\n\n"
            "Анализ личности будет учитывать твои цели и даст персонализированную стратегию достижения!"
        )
        return ConversationHandler.END

    # Если анализ уже есть — предложить пересмотреть
    if db.profile_analysis_done(user_id):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Показать мой анализ", callback_data="show_analysis")],
            [InlineKeyboardButton("🔄 Сделать новый анализ", callback_data="redo_analysis")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
        ])
        await update.message.reply_text(
            "✨ У тебя уже есть анализ личности!\n\nЧто хочешь сделать?",
            reply_markup=keyboard
        )
        return ASK_CONSENT

    # Проверка оплаты: подписка (любая) или разовая оплата разбора
    has_subscription = db.get_user_tier(user_id) != "free"
    has_paid = db.get_has_analysis(user_id)

    if not has_subscription and not has_paid:
        # FREE-пользователь без оплаты — направляем в меню оплаты
        await update.message.reply_text(
            "🔮 Для персонального разбора нужна оплата или подписка."
            + chr(10)
            + "Нажми кнопку *🔮 Персональный разбор* в меню для оформления.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # Начать сбор данных
    await _ask_consent(update, context)
    return ASK_CONSENT


async def _ask_consent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, хочу!", callback_data="consent_yes")],
        [InlineKeyboardButton("❌ Не сейчас", callback_data="consent_no")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ])
    text = (
        "🔮 *Персональный разбор — глубокий анализ твоей личности:*\n"
        "• Астрологический профиль (натальная карта)\n"
        "• Нумерологический профиль\n"
        "• Психологический портрет\n"
        "• Рекомендации по карьере и развитию\n\n"
        "Мне понадобятся 4 данных о тебе. Начнём!"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def consent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "consent_no":
        await query.edit_message_text(
            "Хорошо! Когда будешь готова — просто напиши /analyze 😊"
        )
        return ConversationHandler.END

    # Согласилась — начинаем
    await query.edit_message_text(
        f"Отлично! 🎉 Начинаем!\n\n"
        f"{_progress(1)}\n\n"
        "📝 *Шаг 1: Как тебя зовут?*\n\n"
        "Напиши фамилию, имя и отчество (полностью):",
        parse_mode="Markdown"
    )
    return ASK_NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 3:
        await update.message.reply_text("Пожалуйста, напиши полное ФИО:")
        return ASK_NAME

    context.user_data["profile_name"] = name
    await update.message.reply_text(
        f"Прекрасное имя! ✨\n\n"
        f"{_progress(2)}\n\n"
        "📅 *Шаг 2: Дата рождения*\n\n"
        "Напиши в формате: `ДД.ММ.ГГГГ`\n"
        "Например: `15.03.1990`",
        parse_mode="Markdown"
    )
    return ASK_BIRTHDATE


async def got_birthdate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Базовая валидация формата
    import re
    if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", text):
        await update.message.reply_text(
            "Пожалуйста, используй формат ДД.ММ.ГГГГ\n"
            "Например: `15.03.1990`",
            parse_mode="Markdown"
        )
        return ASK_BIRTHDATE

    context.user_data["profile_birthdate"] = text
    await update.message.reply_text(
        f"{_progress(3)}\n\n"
        "🌍 *Шаг 3: Город рождения*\n\n"
        "Напиши полное название города, где ты родилась.\n"
        "Например: `Москва` или `Санкт-Петербург`",
        parse_mode="Markdown"
    )
    return ASK_BIRTHCITY


async def got_birthcity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    if len(city) < 2:
        await update.message.reply_text("Напиши название города:")
        return ASK_BIRTHCITY

    context.user_data["profile_birthcity"] = city
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить (не знаю точно)", callback_data="skip_birthtime")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ])
    await update.message.reply_text(
        f"{_progress(4)}\n\n"
        "🕐 *Шаг 4: Время рождения*\n\n"
        "Напиши время в формате `ЧЧ:ММ`\n"
        "Например: `14:35`\n\n"
        "Если не знаешь — нажми кнопку ниже (анализ будет чуть менее точным):",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    return ASK_BIRTHTIME


async def skip_birthtime_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["profile_birthtime"] = None
    await query.edit_message_text("Хорошо, пропустим! 👍")
    return await _run_analysis(update, context)


async def got_birthtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    import re
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Пропустить", callback_data="skip_birthtime")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
        ])
        await update.message.reply_text(
            "Используй формат ЧЧ:ММ, например `14:35`\n\nИли пропусти:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return ASK_BIRTHTIME

    context.user_data["profile_birthtime"] = text
    await update.message.reply_text("Отлично! ⏰")
    return await _run_analysis(update, context)


async def _run_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Все данные собраны — запускаем GPT-анализ."""
    chat_id = update.effective_chat.id

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🌟 *Все данные получены!*\n\n"
            "Начинаю глубокий анализ...\n\n"
            "🔮 Строю натальную карту...\n"
            "🔢 Рассчитываю нумерологический код...\n"
            "🧠 Определяю психотип...\n\n"
            "Это займёт около 30-60 секунд. Пожалуйста, подожди ✨"
        ),
        parse_mode="Markdown",
    )

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    user_id = update.effective_user.id
    logger.info("Starting analysis pipeline for user %s", user_id)

    try:
        goals = db.get_active_goals(user_id)

        logger.info("Calling GPT-4o analyze_personality (text-only) for user %s", user_id)
        result = await ai.analyze_personality(
            full_name=context.user_data["profile_name"],
            birth_date=context.user_data["profile_birthdate"],
            birth_city=context.user_data["profile_birthcity"],
            birth_time=context.user_data.get("profile_birthtime"),
            goals=goals,
        )

        logger.info("GPT-4o analysis done for user %s, saving to DB", user_id)
        from datetime import datetime
        db.save_user_profile(
            user_id,
            full_name=context.user_data["profile_name"],
            birth_date=context.user_data["profile_birthdate"],
            birth_city=context.user_data["profile_birthcity"],
            birth_time=context.user_data.get("profile_birthtime"),
            analysis_result=result,
            analysis_done_at=datetime.utcnow().isoformat(),
            has_analysis=1,
        )

        logger.info("Sending analysis result to user %s (%d chars)", user_id, len(result))
        await _send_long_message_to_chat(context.bot, chat_id, result)

        await context.bot.send_message(
            chat_id=chat_id,
            text="✨ *Анализ сохранён!* Готовлю ТОП-10 сильных и слабых качеств...",
            parse_mode="Markdown",
        )
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # === ВТОРОЙ ЗАПРОС GPT: ТОП-10 сильных и слабых качеств ===
        try:
            top10_result = await _get_top10_analysis(result)
            logger.info("Top-10 analysis done for user %s (%d chars)", user_id, len(top10_result))
            await _send_long_message_to_chat(context.bot, chat_id, top10_result)

            # Дописываем к основному анализу в БД
            combined = result + "\n\n" + top10_result
            db.save_user_profile(
                user_id,
                analysis_result=combined,
            )
            context.user_data["analysis_for_followup"] = combined
        except Exception as e:
            logger.error("Top-10 analysis error for user %s: %s", user_id, e, exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Не удалось составить ТОП-10 качеств. Основной анализ сохранён.",
            )
            context.user_data["analysis_for_followup"] = result

        # Показываем кнопки: вопросы по разбору или главное меню
        context.user_data["followup_count"] = 0
        followup_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "❓ Задать вопрос по разбору",
                callback_data="ask_followup"
            )],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
        ])
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✨ *Анализ завершён и сохранён в твоём профиле!*"
                + chr(10) + chr(10)
                + "Ты можешь задать до 5 вопросов по своему разбору."
            ),
            parse_mode="Markdown",
            reply_markup=followup_kb,
        )

        # Очищаем временные данные профиля
        for key in ["profile_name", "profile_birthdate", "profile_birthcity",
                    "profile_birthtime"]:
            context.user_data.pop(key, None)

        return ASK_FOLLOWUP

    except GPTRefusalError:
        logger.warning("GPT refused analysis for user %s — NOT saving to DB", user_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ Анализ временно недоступен. Пожалуйста, попробуй позже.\n\n"
                "Напиши /analyze чтобы начать заново."
            ),
        )

    except ValueError as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔧 Анализ личности временно недоступен. "
                "Администратор уже работает над этим.\n\n"
                "Попробуй снова чуть позже!"
            ),
        )
        logger.error("OpenAI API key missing: %s", e, exc_info=True)

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)[:200]
        logger.error(
            "Analysis error for user %s: %s: %s",
            user_id, error_type, error_msg,
            exc_info=True,
        )

        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            user_msg = (
                "Анализ занял слишком много времени. "
                "Попробуй снова — напиши /analyze"
            )
        elif "rate_limit" in error_msg.lower() or "429" in error_msg:
            user_msg = (
                "Слишком много запросов. Попробуй через 2-3 минуты.\n"
                "Напиши /analyze чтобы начать заново."
            )
        elif "api_key" in error_msg.lower() or "auth" in error_msg.lower():
            user_msg = (
                "Анализ временно недоступен. Администратор уже работает над этим.\n"
                "Попробуй чуть позже!"
            )
        else:
            user_msg = (
                "Произошла ошибка при анализе (" + error_type + "). "
                "Попробуй снова через минуту.\n"
                "Если ошибка повторяется — напиши /analyze заново."
            )
        await context.bot.send_message(chat_id=chat_id, text=user_msg)

    # Очищаем временные данные (при ошибке)
    for key in ["profile_name", "profile_birthdate", "profile_birthcity",
                "profile_birthtime"]:
        context.user_data.pop(key, None)

    return ConversationHandler.END


async def show_analysis_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать сохранённый анализ."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    profile = db.get_user_profile(user_id)
    if not profile or not profile.get("analysis_result"):
        await query.edit_message_text(
            "Анализ не найден. Запусти /analyze чтобы создать новый."
        )
        return

    result = profile["analysis_result"]

    # Проверяем, не является ли сохранённый результат отказом GPT
    if ai.is_gpt_refusal(result):
        db.clear_analysis(user_id)
        await query.edit_message_text(
            "Твой предыдущий анализ был некорректным."
            + chr(10)
            + "Пройди анализ заново — нажми /analyze"
        )
        return ConversationHandler.END

    await query.edit_message_text("📋 Загружаю твой анализ...")
    await _send_long_message_to_chat(
        context.bot,
        query.message.chat.id,
        result
    )

    # Кнопки после показа
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Сделать новый анализ", callback_data="redo_analysis")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ])
    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text="Что хочешь сделать дальше?",
        reply_markup=keyboard,
    )
    return ConversationHandler.END


async def redo_analysis_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать анализ заново."""
    query = update.callback_query
    await query.answer()
    await _ask_consent(update, context)
    return ASK_CONSENT


async def _menu_main_from_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline-кнопка 🏠 Главное меню внутри /analyze — выходим."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    return ConversationHandler.END


async def cancel_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Анализ отменён. Можешь вернуться в любое время командой /analyze 😊"
    )
    return ConversationHandler.END


async def _send_long_message(update: Update, text: str, chunk_size: int = 4000):
    """Разбить длинное сообщение на части и отправить."""
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            # Markdown parse error — send as plain text
            await update.message.reply_text(chunk)


async def _send_long_message_to_chat(bot, chat_id: int, text: str, chunk_size: int = 4000):
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")
        except Exception:
            await bot.send_message(chat_id=chat_id, text=chunk)


async def _get_top10_analysis(analysis_result: str) -> str:
    """Второй GPT-запрос: ТОП-10 сильных и слабых качеств."""
    client = ai.get_client()

    prompt = (
        "На основе только что составленного персонального разбора, составь:"
        + chr(10) + chr(10)
        + "🏆 ТОП-10 СИЛЬНЫХ КАЧЕСТВ ЛИЧНОСТИ"
        + chr(10)
        + "Перечисли 10 самых сильных качеств этого человека, которые помогают "
        + "в достижении целей. Для каждого качества — краткое пояснение (1-2 "
        + "предложения) почему это сила и как её использовать."
        + chr(10) + chr(10)
        + "⚠️ ТОП-10 КАЧЕСТВ, КОТОРЫЕ МОГУТ МЕШАТЬ"
        + chr(10)
        + "Перечисли 10 качеств или паттернов поведения, которые могут мешать "
        + "в достижении целей. Для каждого — краткое пояснение и конкретная "
        + "рекомендация как с этим работать."
        + chr(10) + chr(10)
        + "Пиши на русском. Конкретно, без воды."
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты — профессиональный коуч-астролог, "
             "эксперт по анализу личности. Отвечаешь на русском. Конкретно и по делу."},
            {"role": "user", "content": "Вот персональный разбор клиента:"
             + chr(10) + chr(10) + analysis_result[:4000]
             + chr(10) + chr(10) + prompt},
        ],
        max_tokens=3000,
        temperature=0.7,
    )
    return response.choices[0].message.content


async def followup_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback: пользователь нажал 'Задать вопрос по разбору'."""
    query = update.callback_query
    await query.answer()

    count = context.user_data.get("followup_count", 0)
    remaining = 5 - count
    if remaining <= 0:
        await query.edit_message_text(
            "Ты уже задал 5 вопросов. Спасибо за интерес к разбору!"
            + chr(10) + chr(10)
            + "Используй /analyze чтобы пройти новый анализ."
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "❓ Задай свой вопрос по разбору (осталось: "
        + str(remaining) + "):"
    )
    return ASK_FOLLOWUP


async def handle_followup_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка вопроса по анализу личности."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    question = update.message.text.strip()

    count = context.user_data.get("followup_count", 0)
    if count >= 5:
        await update.message.reply_text(
            "Ты уже задал 5 вопросов. Спасибо за интерес к разбору!"
            + chr(10) + chr(10)
            + "Используй /analyze чтобы пройти новый анализ.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
        return ConversationHandler.END

    # Получаем анализ из context или из БД
    analysis = context.user_data.get("analysis_for_followup")
    if not analysis:
        profile = db.get_user_profile(user_id)
        if profile and profile.get("analysis_result"):
            analysis = profile["analysis_result"]
            # Кэшируем для следующих вопросов
            context.user_data["analysis_for_followup"] = analysis

    if not analysis:
        await update.message.reply_text(
            "Разбор не найден. Пройди /analyze заново.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
        return ConversationHandler.END

    await update.message.chat.send_action("typing")

    try:
        client = ai.get_client()

        # Обрезаем анализ до 4000 символов чтобы не превысить контекст
        analysis_trimmed = analysis[:4000]

        followup_prompt = (
            "Ты — персональный коуч-астролог. Вот разбор клиента:"
            + chr(10) + chr(10) + analysis_trimmed
            + chr(10) + chr(10)
            + "Клиент задаёт вопрос по своему разбору. Ответь подробно и "
            + "конкретно, опираясь на его персональный профиль."
            + chr(10) + chr(10)
            + "Вопрос: " + question
        )

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты — персональный коуч-астролог. "
                 "Отвечаешь на русском. Конкретно, по делу, опираясь на профиль клиента."},
                {"role": "user", "content": followup_prompt},
            ],
            max_tokens=1000,
            temperature=0.7,
        )

        answer = response.choices[0].message.content
        if not answer:
            raise ValueError("GPT returned empty response")

        await _send_long_message_to_chat(update.message.bot, chat_id, answer)

        # Увеличиваем счётчик ТОЛЬКО после успешной отправки ответа
        context.user_data["followup_count"] = count + 1
        remaining = 5 - (count + 1)

        if remaining > 0:
            followup_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "❓ Ещё вопрос (осталось: " + str(remaining) + ")",
                    callback_data="ask_followup"
                )],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
            await update.message.reply_text(
                "Осталось вопросов: " + str(remaining),
                reply_markup=followup_kb,
            )
            return ASK_FOLLOWUP
        else:
            await update.message.reply_text(
                "Ты задал все 5 вопросов. Спасибо за интерес к разбору! 🌟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
                ])
            )
            return ConversationHandler.END

    except Exception as e:
        logger.error(
            "Followup question error for user %s: %s: %s",
            user_id, type(e).__name__, str(e),
            exc_info=True,
        )
        remaining = 5 - count
        await update.message.reply_text(
            "Ошибка при обработке вопроса. Попробуй ещё раз."
            + chr(10)
            + "(Вопрос не засчитан, осталось: " + str(remaining) + ")",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "❓ Попробовать снова (осталось: " + str(remaining) + ")",
                    callback_data="ask_followup"
                )],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
            ])
        )
        return ASK_FOLLOWUP


def build_analyze_conversation(extra_fallbacks=None) -> ConversationHandler:
    """Собрать и вернуть ConversationHandler для /analyze."""
    fallbacks = [CommandHandler("cancel", cancel_analyze)]
    if extra_fallbacks:
        fallbacks = extra_fallbacks + fallbacks

    return ConversationHandler(
        entry_points=[CommandHandler("analyze", analyze_command)],
        states={
            ASK_CONSENT: [
                CallbackQueryHandler(consent_callback, pattern="^consent_"),
                CallbackQueryHandler(show_analysis_callback, pattern="^show_analysis$"),
                CallbackQueryHandler(redo_analysis_callback, pattern="^redo_analysis$"),
                CallbackQueryHandler(_menu_main_from_analyze, pattern="^menu_main$"),
            ],
            ASK_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)
            ],
            ASK_BIRTHDATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_birthdate)
            ],
            ASK_BIRTHCITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_birthcity)
            ],
            ASK_BIRTHTIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_birthtime),
                CallbackQueryHandler(skip_birthtime_callback, pattern="^skip_birthtime$"),
            ],
            ASK_FOLLOWUP: [
                CallbackQueryHandler(followup_entry, pattern="^ask_followup$"),
                CallbackQueryHandler(_menu_main_from_analyze, pattern="^menu_main$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_followup_question),
            ],
        },
        fallbacks=fallbacks,
        allow_reentry=True,
        name="analyze_conv",
        persistent=True,
        conversation_timeout=600,
    )
