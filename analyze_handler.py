"""
ConversationHandler для команды /analyze.
Последовательно собирает данные: ФИО → дата рождения → город → время → фото лица → правая ладонь → левая ладонь.
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
    ASK_FACE_PHOTO,
    ASK_RIGHT_PALM,
    ASK_LEFT_PALM,
    ANALYZING,
) = range(9)

TOTAL_STEPS = 7  # шагов с данными (без согласия и анализа)

STEP_LABELS = [
    "ФИО",
    "Дата рождения",
    "Город рождения",
    "Время рождения",
    "Фото лица",
    "Правая ладонь",
    "Левая ладонь",
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
        "🌟 *Могу открыть твою личность по некоторым данным...*\n\n"
        "Я проведу глубокий анализ, объединяя:\n"
        "🔮 Астрологию • ✋ Хиромантию • 👤 Физиономику\n"
        "🔢 Нумерологию • 🧠 Психологию • 🏆 Коучинг\n\n"
        "На основе анализа дам персонализированную стратегию достижения *именно твоих целей*.\n\n"
        "Для анализа понадобится:\n"
        "• ФИО, дата и время рождения, город\n"
        "• Фото лица (без очков)\n"
        "• Фото ладоней (правой и левой, линии чтобы были видны)\n\n"
        "Готова?"
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
    await query.edit_message_text(
        f"Хорошо, пропустим! 👍\n\n"
        f"{_progress(5)}\n\n"
        "📸 *Шаг 5: Фото лица*\n\n"
        "Пришли чёткое фото лица *без очков* и головных уборов.\n"
        "Лицо должно быть хорошо освещено и смотреть прямо в камеру.",
        parse_mode="Markdown"
    )
    return ASK_FACE_PHOTO


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
    await update.message.reply_text(
        f"Отлично! ⏰\n\n"
        f"{_progress(5)}\n\n"
        "📸 *Шаг 5: Фото лица*\n\n"
        "Пришли чёткое фото лица *без очков* и головных уборов.\n"
        "Лицо должно быть хорошо освещено и смотреть прямо в камеру.",
        parse_mode="Markdown"
    )
    return ASK_FACE_PHOTO


async def got_face_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "Пришли именно фотографию (не файл и не стикер) 📸"
        )
        return ASK_FACE_PHOTO

    # Берём наибольшее разрешение
    photo = update.message.photo[-1]
    context.user_data["face_file_id"] = photo.file_id

    await update.message.reply_text(
        f"Фото лица получила! 📸✅\n\n"
        f"{_progress(6)}\n\n"
        "✋ *Шаг 6: Правая ладонь*\n\n"
        "Пришли фото правой ладони *внутренней стороной* (линии должны быть хорошо видны).\n"
        "Хорошее освещение, рука расслаблена и раскрыта.",
        parse_mode="Markdown"
    )
    return ASK_RIGHT_PALM


async def got_right_palm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "Пришли фотографию правой ладони ✋"
        )
        return ASK_RIGHT_PALM

    photo = update.message.photo[-1]
    context.user_data["right_palm_file_id"] = photo.file_id

    await update.message.reply_text(
        f"Правая ладонь — есть! ✅\n\n"
        f"{_progress(7)}\n\n"
        "🤚 *Шаг 7: Левая ладонь*\n\n"
        "Теперь пришли фото *левой* ладони, тоже внутренней стороной.\n"
        "Это последний шаг — почти готово!",
        parse_mode="Markdown"
    )
    return ASK_LEFT_PALM


async def got_left_palm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "Пришли фотографию левой ладони 🤚"
        )
        return ASK_LEFT_PALM

    photo = update.message.photo[-1]
    context.user_data["left_palm_file_id"] = photo.file_id

    # Все данные собраны — запускаем анализ
    await update.message.reply_text(
        "🌟 *Все данные получены!*\n\n"
        "Начинаю глубокий анализ...\n\n"
        "🔮 Строю натальную карту...\n"
        "✋ Изучаю линии ладоней...\n"
        "👤 Анализирую черты лица...\n"
        "🔢 Рассчитываю нумерологический код...\n"
        "🧠 Определяю психотип...\n\n"
        "Это займёт около 30-60 секунд. Пожалуйста, подожди ✨",
        parse_mode="Markdown"
    )

    # Показываем индикатор печатания
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    user_id = update.effective_user.id
    logger.info("Starting analysis pipeline for user %s", user_id)

    try:
        # Скачиваем фотографии (каждую отдельно с обработкой ошибок)
        logger.info("Fetching photos for user %s", user_id)
        photo_ids = {
            "face": context.user_data["face_file_id"],
            "right_palm": context.user_data["right_palm_file_id"],
            "left_palm": context.user_data["left_palm_file_id"],
        }
        photo_labels = {
            "face": "лица",
            "right_palm": "правой ладони",
            "left_palm": "левой ладони",
        }
        photo_bytes = {}
        for key, file_id in photo_ids.items():
            try:
                f = await context.bot.get_file(file_id, read_timeout=30, connect_timeout=15)
                data = await f.download_as_bytearray()
                if not data or len(data) < 1000:
                    await update.message.reply_text(
                        "Фото " + photo_labels[key] + " не получено. Попробуй ещё раз: /analyze"
                    )
                    return ConversationHandler.END
                photo_bytes[key] = bytes(data)
            except Exception as e:
                logger.error(
                    "Failed to download %s photo for user %s: %s: %s",
                    key, user_id, type(e).__name__, e, exc_info=True,
                )
                await update.message.reply_text(
                    "Не удалось скачать фото " + photo_labels[key]
                    + ". Попробуй ещё раз: /analyze"
                )
                return ConversationHandler.END

        logger.info(
            "Photos downloaded for user %s: face=%dB, right=%dB, left=%dB",
            user_id, len(photo_bytes["face"]),
            len(photo_bytes["right_palm"]),
            len(photo_bytes["left_palm"]),
        )

        # Получаем цели пользователя
        goals = db.get_active_goals(user_id)

        # Запускаем AI анализ
        logger.info("Calling GPT-4o analyze_personality for user %s", user_id)
        result = await ai.analyze_personality(
            full_name=context.user_data["profile_name"],
            birth_date=context.user_data["profile_birthdate"],
            birth_city=context.user_data["profile_birthcity"],
            birth_time=context.user_data.get("profile_birthtime"),
            face_photo_bytes=photo_bytes["face"],
            right_palm_bytes=photo_bytes["right_palm"],
            left_palm_bytes=photo_bytes["left_palm"],
            goals=goals,
        )

        # Сохраняем в профиль
        logger.info("GPT-4o analysis done for user %s, saving to DB", user_id)
        from datetime import datetime
        db.save_user_profile(
            user_id,
            full_name=context.user_data["profile_name"],
            birth_date=context.user_data["profile_birthdate"],
            birth_city=context.user_data["profile_birthcity"],
            birth_time=context.user_data.get("profile_birthtime"),
            face_photo_id=context.user_data["face_file_id"],
            right_palm_photo_id=context.user_data["right_palm_file_id"],
            left_palm_photo_id=context.user_data["left_palm_file_id"],
            analysis_result=result,
            analysis_done_at=datetime.utcnow().isoformat(),
            has_analysis=1,
        )

        # Отправляем результат сразу пользователю
        logger.info("Sending analysis result to user %s (%d chars)", user_id, len(result))
        await _send_long_message(update, result)

        # Кнопки после анализа
        from telegram import ReplyKeyboardMarkup, KeyboardButton
        after_kb = ReplyKeyboardMarkup(
            [
                [KeyboardButton("🔄 Сделать новый анализ")],
                [KeyboardButton("🏠 Главное меню")],
            ],
            resize_keyboard=True,
        )
        await update.message.reply_text(
            "✨ *Анализ сохранён в твоём профиле!*\n\n"
            "Теперь я буду учитывать твой психотип при каждом совете.",
            parse_mode="Markdown",
            reply_markup=after_kb,
        )

    except GPTRefusalError:
        logger.warning("GPT refused analysis for user %s — NOT saving to DB", user_id)
        await update.message.reply_text(
            "⚠️ Анализ временно недоступен. Пожалуйста, попробуй позже.\n\n"
            "Напиши /analyze чтобы начать заново."
        )

    except ValueError as e:
        # OPENAI_API_KEY не задан
        await update.message.reply_text(
            "🔧 Анализ личности временно недоступен. Администратор уже работает над этим.\n\n"
            "Попробуй снова чуть позже!"
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

        # Более понятное сообщение в зависимости от типа ошибки
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
        await update.message.reply_text(user_msg)

    # Очищаем временные данные
    for key in ["profile_name", "profile_birthdate", "profile_birthcity",
                "profile_birthtime", "face_file_id", "right_palm_file_id", "left_palm_file_id"]:
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

    await query.edit_message_text("📋 Загружаю твой анализ...")
    await _send_long_message_to_chat(
        context.bot,
        query.message.chat.id,
        profile["analysis_result"]
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
            ASK_FACE_PHOTO: [
                MessageHandler(filters.PHOTO, got_face_photo)
            ],
            ASK_RIGHT_PALM: [
                MessageHandler(filters.PHOTO, got_right_palm)
            ],
            ASK_LEFT_PALM: [
                MessageHandler(filters.PHOTO, got_left_palm)
            ],
        },
        fallbacks=fallbacks,
        allow_reentry=True,
        name="analyze_conv",
        persistent=True,
        conversation_timeout=600,
    )
