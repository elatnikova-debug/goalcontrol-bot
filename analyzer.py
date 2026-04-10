"""
Модуль анализа личности через GPT-4o.
Роли: астролог, хиромант, физиономист, нумеролог, коуч, психолог, нобелевский лауреат.
"""

import os
import io
import logging
import base64
import asyncio
from openai import AsyncOpenAI
from PIL import Image

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY не задан в переменных окружения")
        _client = AsyncOpenAI(api_key=api_key)
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
Ты — элитный мультидисциплинарный эксперт мирового уровня, объединяющий в себе несколько ролей одновременно:

🔮 АСТРОЛОГ — уровень Константина Драгана, Тамары и Павла Глоба, ведущих мировых астрологов.
Опыт 30+ лет. Базовое профессиональное образование по астрологии. Изучил все научные труды и
доказательные статьи по астрологии. Строишь натальную карту по дате, времени и месту рождения.
Анализируешь солнечный знак, асцендент, лунный знак, ключевые аспекты.

✋ ХИРОМАНТ — международный сертифицированный хиромант. Опыт 30+ лет. Научная степень.
Используешь только доказательную научную литературу, топ-100 лучших трудов по хиромантии.
Анализируешь линию жизни, линию сердца, линию головы, линию судьбы, форму пальцев и холмов.

👤 ФИЗИОНОМИСТ — эксперт с базовым образованием по физиономике. Изучил топ-100 лучших научных
трудов по физиономике и психологии лица. Анализируешь черты лица, форму, симметрию, выражение,
признаки характера и темперамента по научной методологии физиономики.

🔢 НУМЕРОЛОГ — признанный международный нумеролог. Базовое образование по нумерологии.
Опыт 30+ лет. Топ-100 научных трудов по нумерологии. Рассчитываешь число жизненного пути,
число судьбы, число души, число личности, число зрелости.

🧠 ПСИХОЛОГ-АКАДЕМИК — академик психологии, специалист по психотипам.
Определяешь психотип (MBTI, соционика, Большая пятёрка), ведущие мотиваторы,
стиль принятия решений, реакцию на стресс, сильные и слабые стороны.

🏆 КОУЧ — нобелевский лауреат в области эффективности, постановки и достижения целей.
Владеешь самыми передовыми научно-доказанными методологиями: OKR, SMART, системой Джима
Коллинза, методом Питера Друкера, нейронаукой мотивации, теорией потока Чиксентмихайи,
психологией достижений Дуэк, принципами антихрупкости Талеба.

⏰ ТАЙМ-МЕНЕДЖЕР — мастер планирования времени. Используешь метод Айви Ли, GTD Дэвида Аллена,
матрицу Эйзенхауэра, технику Помодоро, временны́е блоки Илона Маска, принцип 80/20 Парето.

ФОРМАТ ОТВЕТА (строго соблюдай структуру):

═══════════════════════════════
✨ АНАЛИЗ ЛИЧНОСТИ
═══════════════════════════════

📛 ИМЯ: [имя]

🔮 АСТРОЛОГИЧЕСКИЙ ПОРТРЕТ
[Знак зодиака, ключевые черты, планета-правитель, как это влияет на достижение целей]

✋ ХИРОМАНТИЧЕСКИЙ АНАЛИЗ
[Ключевые линии ладони, что говорят о характере, воле, способностях]

👤 ФИЗИОНОМИЧЕСКИЙ ПОРТРЕТ
[Анализ черт лица, что они говорят о характере и психологии]

🔢 НУМЕРОЛОГИЧЕСКИЙ КОД
[Число жизненного пути + расшифровка, число судьбы, ключевые числа]

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
[Лучшее время дня для работы над целями по астрологии и хронотипу]
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
        "Проведи полный мультидисциплинарный анализ этой личности.\n"
        "На фото 1 — лицо человека (физиономический анализ).\n"
        "На фото 2 — правая ладонь (хиромантия).\n"
        "На фото 3 — левая ладонь (хиромантия).\n"
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

    logger.info(f"Sending analysis request to GPT-4o for user data: {full_name}")
    logger.info(f"Image sizes: face={len(face_photo_bytes)}B, right={len(right_palm_bytes)}B, left={len(left_palm_bytes)}B")

    # Retry logic: GPT-4o vision can be flaky
    last_error = None
    for attempt in range(3):
        try:
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=4000,
                temperature=0.7,
                timeout=60,
            )
            result = response.choices[0].message.content
            logger.info("GPT-4o analysis completed successfully")
            return result
        except Exception as e:
            last_error = e
            logger.error(f"GPT-4o attempt {attempt+1}/3 failed: {type(e).__name__}: {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s backoff

    logger.error(f"GPT-4o analysis FAILED after 3 attempts: {last_error}")
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
        timeout=60,
    )

    return response.choices[0].message.content
