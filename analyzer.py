"""
Модуль анализа личности через GPT-4o.
Роли: астролог, хиромант, физиономист, нумеролог, коуч, психолог, нобелевский лауреат.
"""

import os
import io
import logging
import base64
import asyncio
import httpx
from openai import AsyncOpenAI
from PIL import Image

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

# Фразы-индикаторы отказа GPT (content policy)
_REFUSAL_MARKERS = [
    "не могу помочь",
    "не в состоянии помочь",
    "unable to assist",
    "cannot help",
    "can't help",
    "i can't assist",
    "i cannot assist",
    "не могу выполнить",
    "не могу обработать",
    "against my guidelines",
    "content policy",
    "не соответствует",
    "i'm not able to",
]


class GPTRefusalError(Exception):
    """GPT вернул отказ вместо анализа."""
    pass


def is_gpt_refusal(text: str) -> bool:
    """Проверяет, является ли ответ GPT отказом."""
    if not text or len(text) < 20:
        return True
    lower = text.lower()
    for marker in _REFUSAL_MARKERS:
        if marker in lower:
            return True
    return False


def get_client() -> AsyncOpenAI:
    """Create or return a cached AsyncOpenAI client with proper timeout."""
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY не задан в переменных окружения")
        _client = AsyncOpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(180.0, connect=10.0),
        )
    return _client


def _resize_image(image_bytes: bytes, max_size: int = 800) -> bytes:
    """Resize image to max_size on longest side, compress to JPEG quality 70."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        ratio = min(max_size / img.width, max_size / img.height, 1.0)
        if ratio < 1.0:
            new_w = int(img.width * ratio)
            new_h = int(img.height * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=70)
        return out.getvalue()
    except Exception:
        return image_bytes


MEGA_SYSTEM_PROMPT = """
Ты — элитный персональный коуч и психолог-практик мирового уровня, который составляет
комплексный психологический портрет личности и персональные рекомендации по развитию.

Ты объединяешь несколько направлений экспертизы:

🔮 ПЕРСОНАЛЬНЫЙ ПРОФИЛЬ ПО ДАТЕ И МЕСТУ РОЖДЕНИЯ — ты составляешь индивидуальный профиль
человека, используя дату, время и место рождения как входные данные. На их основе описываешь
ключевые личностные характеристики, сильные стороны, потенциал роста и благоприятные периоды.
Это творческая интерпретация, которая помогает человеку лучше понять себя.

✋ ИНТЕРПРЕТАЦИЯ СИМВОЛИЧЕСКИХ ПАТТЕРНОВ НА ИЗОБРАЖЕНИЯХ РУК — ты изучаешь фотографии ладоней
и описываешь визуальные паттерны, которые видишь. На их основе даёшь творческую интерпретацию
о характере, волевых качествах и склонностях человека. Это образный инструмент самопознания.

👤 СЧИТЫВАНИЕ ОБЩЕГО ВПЕЧАТЛЕНИЯ О ЧЕЛОВЕКЕ — на основе фотографии описываешь общее впечатление,
которое производит человек: энергетику, стиль, уверенность, открытость. Это не идентификация
по физическим признакам, а описание впечатления и образа, которые помогают в коучинге.

🔢 ЧИСЛОВОЙ ПРОФИЛЬ ЛИЧНОСТИ — ты вычисляешь персональные числа на основе даты рождения
и полного имени (число пути, число цели, число мотивации, число характера, число зрелости)
и даёшь их развёрнутую творческую интерпретацию как инструмент самопознания.

🧠 ПСИХОЛОГ-ПРАКТИК — определяешь психотип (MBTI, соционика, Большая пятёрка), ведущие мотиваторы,
стиль принятия решений, реакцию на стресс, сильные и слабые стороны.

🏆 КОУЧ — эксперт в области эффективности, постановки и достижения целей.
Владеешь методологиями: OKR, SMART, система Джима Коллинза, метод Питера Друкера,
нейронаука мотивации, теория потока Чиксентмихайи, психология достижений Дуэк,
принципы антихрупкости Талеба.

⏰ ТАЙМ-МЕНЕДЖЕР — мастер планирования времени. Используешь метод Айви Ли, GTD Дэвида Аллена,
матрицу Эйзенхауэра, технику Помодоро, временны́е блоки Илона Маска, принцип 80/20 Парето.

ВАЖНО: Ты — творческий коуч-практик. Весь анализ — это авторская творческая интерпретация,
инструмент для самопознания и мотивации. Это НЕ научный диагноз и НЕ медицинское заключение.
Ты помогаешь человеку раскрыть свой потенциал через разные призмы восприятия.

ФОРМАТ ОТВЕТА (строго соблюдай структуру):

═══════════════════════════════
✨ ПЕРСОНАЛЬНЫЙ КОУЧИНГОВЫЙ АНАЛИЗ
═══════════════════════════════

📛 ИМЯ: [имя]

🔮 ПЕРСОНАЛЬНЫЙ ПРОФИЛЬ
[Ключевые личностные характеристики на основе даты, времени и места рождения]
[Сильные стороны, потенциал роста, благоприятные периоды, влияние на достижение целей]

✋ АНАЛИЗ ПАТТЕРНОВ ЛАДОНЕЙ
[Описание визуальных паттернов на фотографиях ладоней и их творческая интерпретация]
[Что они говорят о характере, воле, способностях]

👤 ОБЩЕЕ ВПЕЧАТЛЕНИЕ И ОБРАЗ
[Описание впечатления, энергетики, стиля на основе фотографии]

🔢 ЧИСЛОВОЙ ПРОФИЛЬ
[Число пути + расшифровка, число цели, ключевые числа и их интерпретация]

═══════════════════════════════
🧠 ПСИХОТИП И МОТИВАЦИЯ
═══════════════════════════════

[Определи психотип по MBTI или соционике]
[Ведущий тип мотивации: достижение / принадлежность / власть / смысл]
[Что ЗАРЯЖАЕТ этого человека энергией]
[Что БЛОКИРУЕТ и демотивирует]
[Как реагирует на давление и дедлайны]

═══════════════════════════════
🎯 СТРАТЕГИЯ ДОСТИЖЕНИЯ ЦЕЛЕЙ
═══════════════════════════════

[Исходя из данного психотипа — КОНКРЕТНАЯ стратегия достижения целей]
[Оптимальная система планирования для этого типа личности]
[Рекомендуемый метод тайм-менеджмента]
[Как разбивать цели на этапы под этот психотип]
[Как работать с самосаботажем и прокрастинацией]

═══════════════════════════════
💡 ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ
═══════════════════════════════

[3-5 конкретных, практических рекомендаций именно для этой личности]
[Лучшее время дня для работы над целями на основе хронотипа и профиля]
[Ключевая фраза-якорь для мотивации в трудные моменты]

═══════════════════════════════

Отвечай только на русском языке. Будь конкретным, практичным, вдохновляющим.
Не используй общие фразы — каждый вывод должен быть основан на реальных данных человека.
"""


async def analyze_personality(
    full_name: str,
    birth_date: str,
    birth_city: str,
    birth_time: str | None,
    face_photo_bytes: bytes,
    right_palm_bytes: bytes,
    left_palm_bytes: bytes,
    goals: list[dict],
) -> str:
    """
    Запустить GPT-4o анализ личности по всем данным.
    Возвращает текст анализа.
    """
    client = get_client()

    # Формируем текстовый контекст
    goals_text = ""
    if goals:
        goals_text = "\n\nЦЕЛИ ЧЕЛОВЕКА:\n"
        for i, g in enumerate(goals, 1):
            g = dict(g) if not isinstance(g, dict) else g
            deadline = g['deadline'] if g['deadline'] else 'не указан'
            goals_text += f"{i}. {g['title']} (дедлайн: {deadline})\n"
            if g.get("description"):
                goals_text += f"   Описание: {g['description']}\n"

    birth_time_str = birth_time if birth_time else "не указано"

    user_text = (
        f"ДАННЫЕ ЧЕЛОВЕКА:\n"
        f"ФИО: {full_name}\n"
        f"Дата рождения: {birth_date}\n"
        f"Город рождения: {birth_city}\n"
        f"Время рождения: {birth_time_str}\n"
        f"{goals_text}\n\n"
        "Проведи полный персональный коучинговый анализ этой личности.\n"
        "На фото 1 — фотография человека (опиши общее впечатление и образ).\n"
        "На фото 2 — изображение правой руки (интерпретируй визуальные паттерны).\n"
        "На фото 3 — изображение левой руки (интерпретируй визуальные паттерны).\n"
        "Используй ВСЕ предоставленные данные. Дай максимально персонализированный анализ.\n"
        "Особое внимание удели стратегии достижения КОНКРЕТНЫХ ЦЕЛЕЙ этого человека."
    )

    # Сжимаем фото перед отправкой (макс 800px, JPEG q70)
    face_photo_bytes = _resize_image(face_photo_bytes)
    right_palm_bytes = _resize_image(right_palm_bytes)
    left_palm_bytes = _resize_image(left_palm_bytes)

    # Кодируем фото в base64
    face_b64 = base64.b64encode(face_photo_bytes).decode()
    right_b64 = base64.b64encode(right_palm_bytes).decode()
    left_b64 = base64.b64encode(left_palm_bytes).decode()

    messages = [
        {"role": "system", "content": MEGA_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{face_b64}", "detail": "low"},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{right_b64}", "detail": "low"},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{left_b64}", "detail": "low"},
                },
            ],
        },
    ]

    logger.info("Starting analysis for user: %s", full_name)
    logger.info(
        "Image sizes: face=%dB, right=%dB, left=%dB",
        len(face_photo_bytes), len(right_palm_bytes), len(left_palm_bytes),
    )

    # Retry logic: GPT-4o vision can be flaky
    last_error = None
    for attempt in range(3):
        try:
            logger.info("Calling GPT-4o (attempt %d/3) for user: %s", attempt + 1, full_name)
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=4000,
                temperature=0.7,
            )
            result = response.choices[0].message.content
            logger.info("GPT-4o response received, length=%d chars", len(result))

            # Проверяем на отказ GPT (content policy)
            if is_gpt_refusal(result):
                logger.warning("GPT-4o returned a refusal for user: %s", full_name)
                raise GPTRefusalError(result)

            return result
        except Exception as e:
            last_error = e
            logger.error(
                "GPT-4o attempt %d/3 failed: %s: %s",
                attempt + 1, type(e).__name__, e,
                exc_info=True,
            )
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s backoff

    logger.error("GPT-4o analysis FAILED after 3 attempts: %s", last_error, exc_info=True)
    raise last_error


async def get_goal_advice(
    analysis_result: str,
    goal_title: str,
    goal_description: str,
    milestones: list[str],
) -> str:
    """
    Получить персонализированный совет по конкретной цели
    на основе уже готового анализа личности.
    """
    client = get_client()

    milestones_text = "\n".join(f"- {m}" for m in milestones) if milestones else "этапы не заданы"

    prompt = (
        f"На основе анализа личности ниже дай КОНКРЕТНЫЙ персонализированный план "
        f"достижения следующей цели.\n\n"
        f"ЦЕЛЬ: {goal_title}\n"
        f"ОПИСАНИЕ: {goal_description or 'не указано'}\n"
        f"ЭТАПЫ:\n{milestones_text}\n\n"
        f"АНАЛИЗ ЛИЧНОСТИ:\n{analysis_result[:2000]}\n\n"
        "Дай:\n"
        "1. Оценку реалистичности цели для данного психотипа\n"
        "2. Главные риски и как их нейтрализовать\n"
        "3. Оптимальный режим работы над целью\n"
        "4. Мотивационную фразу-якорь именно для этой цели\n"
        "5. Один неочевидный совет, который изменит всё\n\n"
        "Ответ на русском, конкретно и по делу. Максимум 400 слов."
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты элитный коуч и эксперт по достижению целей. Отвечаешь только на русском. Конкретно, практично, вдохновляюще."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=800,
        temperature=0.7,
    )

    return response.choices[0].message.content
