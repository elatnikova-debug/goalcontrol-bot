"""
Точка входа для Коуч-Трекер бота.
Запускает бота и планировщик напоминаний.
"""

import os
import sys
import asyncio
import logging

from bot import create_application
from database import init_db
from scheduler import scheduler_loop

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def main():
    # Получаем токен
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN не задан! Установите переменную окружения BOT_TOKEN.")
        logger.error("Пример: export BOT_TOKEN='123456:ABC-DEF...'")
        sys.exit(1)

    # Инициализируем БД
    init_db()
    logger.info("Database initialized")

    # Создаём приложение
    app = create_application(token)
    logger.info("Bot application created")

    # Запускаем планировщик напоминаний в фоне
    async def post_init(application):
        """Запустить scheduler после инициализации бота."""
        asyncio.create_task(scheduler_loop(application.bot))
        logger.info("Scheduler task created")

    app.post_init = post_init

    # Запускаем бота
    logger.info("Starting bot... (press Ctrl+C to stop)")
    app.run_polling(
        allowed_updates=[
            "message",
            "callback_query",
            "pre_checkout_query",
        ],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
