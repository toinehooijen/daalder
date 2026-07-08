"""/start, /help, /over handlers."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from daalder import db, texts


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await db.get_or_create_user(update.effective_user.id)
    await update.message.reply_html(texts.WELCOME)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(texts.HELP)


async def over_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(texts.OVER)
