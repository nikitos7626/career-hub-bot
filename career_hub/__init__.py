"""
Career Hub - Autonomous Multi-Platform IT Vacancy Aggregator, AI Analyzer, and Auto-Apply Engine.
Refactored MVC Architecture.
"""

import logging
import threading

from career_hub.controllers.bot_controller import (
    telegram_updates_worker,
    vacancy_monitor_worker,
)
from career_hub.views.http_server import http_server_worker

logger = logging.getLogger("career_hub")


def run():
    logger.info("Старт Мульти-платформенного сервиса Career AI Bot (MVC)...")

    # 1. Фоновый слушатель Telegram
    t_tg = threading.Thread(target=telegram_updates_worker, daemon=True)
    t_tg.start()

    # 2. Фоновый HTTP сервер для Telegram Mini App
    t_http = threading.Thread(target=http_server_worker, daemon=True)
    t_http.start()

    # 3. Основной цикл мониторинга вакансий
    vacancy_monitor_worker()


def main():
    run()


if __name__ == "__main__":
    main()
