"""Entry point: build the Application, register handlers, run polling."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from daalder import config, db, scheduler
from daalder.handlers import payments as payment_handlers
from daalder.handlers import start as start_handlers
from daalder.handlers import tracking as tracking_handlers
from daalder.scraping import fetch as fetch_module
from daalder.scraping import llm as llm_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    await db.init_pool(config.DATABASE_URL)
    await db.init_schema()
    fetch_module.init_client()
    logger.info("Daalder gestart en verbonden met de database.")


async def post_shutdown(application: Application) -> None:
    await fetch_module.close_client()
    await llm_module.close_client()
    await db.close_pool()
    logger.info("Daalder netjes afgesloten.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Onverwachte fout tijdens verwerken van update", exc_info=context.error)
    if config.ADMIN_USER_ID:
        try:
            await context.bot.send_message(config.ADMIN_USER_ID, f"⚠️ Daalder fout: {context.error}")
        except Exception:
            logger.exception("Kon foutmelding niet naar beheerder sturen")


def build_application() -> Application:
    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_handlers.start_command))
    application.add_handler(CommandHandler("help", start_handlers.help_command))
    application.add_handler(CommandHandler("over", start_handlers.over_command))
    application.add_handler(CommandHandler("lijst", tracking_handlers.list_command))
    application.add_handler(CommandHandler("status", payment_handlers.status_command))
    application.add_handler(CommandHandler("upgrade", payment_handlers.upgrade_command))
    application.add_handler(CommandHandler("paysupport", payment_handlers.paysupport_command))
    application.add_handler(CommandHandler("refund", payment_handlers.refund_command))

    application.add_handler(CallbackQueryHandler(tracking_handlers.detail_callback, pattern=r"^detail:\d+$"))
    application.add_handler(CallbackQueryHandler(tracking_handlers.target_prompt_callback, pattern=r"^target:\d+$"))
    application.add_handler(CallbackQueryHandler(tracking_handlers.remove_prompt_callback, pattern=r"^remove:\d+$"))
    application.add_handler(CallbackQueryHandler(tracking_handlers.remove_confirm_callback, pattern=r"^remove_yes:\d+$"))
    application.add_handler(CallbackQueryHandler(tracking_handlers.remove_cancel_callback, pattern=r"^remove_no:\d+$"))
    application.add_handler(CallbackQueryHandler(payment_handlers.upgrade_callback, pattern=r"^upgrade_(monthly|annual)$"))
    application.add_handler(CallbackQueryHandler(payment_handlers.go_upgrade_callback, pattern=r"^go_upgrade$"))

    application.add_handler(PreCheckoutQueryHandler(payment_handlers.precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_handlers.successful_payment_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_handlers.handle_text_message))

    application.add_error_handler(on_error)

    job_queue = application.job_queue
    job_queue.run_repeating(
        scheduler.check_due_products_job,
        interval=config.SCHEDULER_INTERVAL_MINUTES * 60,
        first=15,
        name="check_due_products",
    )
    job_queue.run_repeating(
        scheduler.check_lapsed_plans_job,
        interval=config.LAPSE_CHECK_INTERVAL_HOURS * 3600,
        first=45,
        name="check_lapsed_plans",
    )

    return application


def main() -> None:
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
