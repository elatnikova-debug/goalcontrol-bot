"""
Модуль анализа личности через GPT-4o.
Роли: персональный профиль, интерпретация паттернов, числовой анализ, коуч, психолог.
"""

import os
import logging
import asyncio
import httpx
from openai import AsyncOpenAI

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



MEGA_SYSTEM_PROMPT = """
Ты — элитный персональный коуч и психолог-практик с 20-летним опытом.
Твоя задача — составить ГЛУБОКИЙ персональный профиль человека и дать конкретные рекомендации.

На основе данных клиента составь ПОДРОБНЫЙ персональный профиль (минимум 2000 символов):

1. 🔢 ЧИСЛОВОЙ ПРОФИЛЬ ЛИЧНОСТИ
   Вычисли число жизненного пути из даты рождения. Опиши что оно означает для характера и судьбы.
   Вычисли число имени. Опиши его влияние.

2. 🌟 ПСИХОЛОГИЧЕСКИЙ ПОРТРЕТ
   Подробно опиши тип личности, темперамент, ведущие черты характера.
   Как этот человек принимает решения? Как общается? Что его мотивирует?

3. 💪 СИЛЬНЫЕ СТОРОНЫ
   Минимум 5 конкретных сильных сторон с пояснениями.

4. ⚡ ЗОНЫ РОСТА
   Минимум 3 области где стоит развиваться. Конкретные рекомендации.

5. 💼 КАРЬЕРА И БИЗНЕС
   Какие направления бизнеса/карьеры наиболее подходят? Стиль лидерства.

6. 💕 ОТНОШЕНИЯ И КОММУНИКАЦИЯ
   Стиль общения, совместимость, как строить отношения.

7. 🎯 ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ
   5 конкретных действий для роста и развития прямо сейчас.

Пиши на русском языке. Стиль — тёплый, мотивирующий, но конкретный.
Это НЕ гороскоп — это профессиональный коучинговый анализ.
Давай конкретику, а не общие фразы.
"""


async def analyze_personality(
    full_name: str,
    birth_date: str,
    birth_city: str,
    birth_time: str | None,
    goals: list[dict],
) -> str:
    """
    Запустить GPT-4o анализ личности по текстовым данным.
    Фото НЕ отправляются в GPT (content policy блокирует palmistry/physiognomy).
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
        f"Данные клиента:\n"
        f"- Имя: {full_name}\n"
        f"- Дата рождения: {birth_date}\n"
        f"- Город рождения: {birth_city}\n"
        f"- Время рождения: {birth_time_str}\n"
        f"{goals_text}\n\n"
        "Составь глубокий персональный коучинговый профиль на основе даты, "
        "времени и места рождения. Включи числовой анализ имени, "
        "психологический портрет, сильные стороны, зоны роста, рекомендации.\n"
        "Особое внимание удели стратегии достижения КОНКРЕТНЫХ ЦЕЛЕЙ этого человека."
    )

    messages = [
        {"role": "system", "content": MEGA_SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    logger.info("Starting text-only analysis for user: %s", full_name)

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
