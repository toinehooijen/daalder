"""Admin-only reporting commands (read-only), gated on config.ADMIN_USER_ID.

Plan/payment *mutations* (grant/revoke/refund) live in payments.py — this
module is for read-only visibility into users, plans and usage.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from daalder import config, db, texts

_USERS_PER_MESSAGE = 30


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.ADMIN_USER_ID or update.effective_user.id != config.ADMIN_USER_ID:
        await update.message.reply_html(texts.ADMIN_ONLY)
        return

    users = await db.list_users_with_usage()
    total = len(users)
    plus = sum(1 for u in users if u["plan"] == "plus")
    free = total - plus

    await update.message.reply_html(texts.users_summary(total, plus, free))

    for start in range(0, total, _USERS_PER_MESSAGE):
        chunk = users[start : start + _USERS_PER_MESSAGE]
        await update.message.reply_html("\n".join(texts.users_row(u) for u in chunk))
