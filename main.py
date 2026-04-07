"""
Точка входа для Коуч-Трекер бота.
Запускает бота и планировщик напоминаний.
"""

import os
import sys
import asyncio
import logging

from bot import build_application
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
    app = build_application(token)
    logger.info("Bot application created")

    # Запускаем планировщик и настраиваем команды при старте
    async def post_init(application):
        """После инициализации бота: планировщик + команды."""
        # Запускаем scheduler
        asyncio.create_task(scheduler_loop(application.bot))
        logger.info("Scheduler task created")

        # Удаляем старое меню команд и убираем кнопку Menu
        # чтобы она не мешала reply keyboard
        try:
            from telegram import BotCommand, MenuButtonDefault
            # Удаляем все команды из меню
            await application.bot.set_my_commands([])
            # Ставим кнопку меню на дефолт (не "commands")
            await application.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
            logger.info("Bot commands cleared, menu button set to default")
        except Exception as e:
            logger.warning(f"Could not update menu button: {e}")

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
