import logging
import os
import sqlite3

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]

raw_admin_ids = os.environ.get(
    "ADMIN_IDS",
    os.environ.get("ADMIN_ID", ""),
)
ADMIN_IDS = {
    int(item.strip())
    for item in raw_admin_ids.split(",")
    if item.strip()
}
if not ADMIN_IDS:
    raise ValueError("Set ADMIN_IDS in Render Environment.")

BASE_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))
GROUP_CHAT_ID = int(os.environ["GROUP_CHAT_ID"])

WEBHOOK_PATH = "innovex-telegram-webhook"
DB_PATH = os.environ.get("DB_PATH", "/tmp/innovex.db")


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS replies_map_v2 (
                admin_chat_id INTEGER NOT NULL,
                admin_message_id INTEGER NOT NULL,
                user_chat_id INTEGER NOT NULL,
                PRIMARY KEY (admin_chat_id, admin_message_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS pending_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_chat_id INTEGER NOT NULL,
                user_message_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)


def save_mapping(admin_id, admin_message_id, user_chat_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO replies_map_v2
            (admin_chat_id, admin_message_id, user_chat_id)
            VALUES (?, ?, ?)
            """,
            (admin_id, admin_message_id, user_chat_id),
        )


def get_user_chat(admin_id, admin_message_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            """
            SELECT user_chat_id FROM replies_map_v2
            WHERE admin_chat_id = ? AND admin_message_id = ?
            """,
            (admin_id, admin_message_id),
        ).fetchone()
    return row[0] if row else None


def create_pending(user_chat_id, user_message_id):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            """
            INSERT INTO pending_messages
            (user_chat_id, user_message_id, status)
            VALUES (?, ?, 'pending')
            """,
            (user_chat_id, user_message_id),
        )
        return cursor.lastrowid


def get_pending(pending_id):
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(
            """
            SELECT user_chat_id, user_message_id, status
            FROM pending_messages
            WHERE id = ?
            """,
            (pending_id,),
        ).fetchone()


def change_status(pending_id, old_status, new_status):
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute(
            """
            UPDATE pending_messages
            SET status = ?
            WHERE id = ? AND status = ?
            """,
            (new_status, pending_id, old_status),
        )
        return cursor.rowcount == 1


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    if user.id in ADMIN_IDS:
        await message.reply_text(
            "INNOVEX Admin is ready.\n\n"
            "• ইউজারের কপি করা মেসেজে Reply করলে "
            "ব্যক্তিগত উত্তর যাবে।\n"
            "• Approve করলে মেসেজ গ্রুপে প্রকাশ হবে।\n"
            "• Reject করলে মেসেজ প্রকাশ হবে না।"
        )
    else:
        await message.reply_text(
            "👋 Welcome to INNOVEX Support!\n\n"
            "আপনার মেসেজ লিখে পাঠান। আমাদের টিম উত্তর দেবে।"
        )


async def receive_user_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or user.id in ADMIN_IDS:
        return

    pending_id = create_pending(
        message.chat_id,
        message.message_id,
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve:{pending_id}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject:{pending_id}",
            ),
        ]
    ])

    delivered = 0

    for admin_id in ADMIN_IDS:
        try:
            # ইউজারের মূল মেসেজের কপি এবং Approve/Reject বাটন।
            copied = await context.bot.copy_message(
                chat_id=admin_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
                reply_markup=keyboard,
            )

            # এই কপি করা মেসেজে Reply করলে ইউজারকে উত্তর যাবে।
            save_mapping(
                admin_id,
                copied.message_id,
                message.chat_id,
            )

            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📩 INNOVEX SUPPORT\n\n"
                    f"Name: {user.first_name}\n"
                    f"User ID: {user.id}\n\n"
                    "ব্যক্তিগত উত্তর দিতে উপরের কপি করা "
                    "মেসেজে Reply করুন।\n"
                    "শুধু Approve চাপলে মূল মেসেজ গ্রুপে যাবে।"
                ),
            )
            delivered += 1

        except TelegramError:
            log.exception("Could not deliver to admin %s", admin_id)

    if delivered == 0:
        await message.reply_text(
            "দুঃখিত, মেসেজটি অ্যাডমিনের কাছে পাঠানো যায়নি। "
            "পরে আবার চেষ্টা করুন।"
        )
    else:
        await message.reply_text(
            "✅ আপনার মেসেজটি টিমের কাছে পাঠানো হয়েছে। "
            "অনুমোদন পেলে এটি গ্রুপে প্রকাশ হবে।"
        )


async def approval_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    user = update.effective_user

    if not query or not user:
        return

    if user.id not in ADMIN_IDS:
        await query.answer(
            "শুধু অনুমোদিত অ্যাডমিন এই কাজ করতে পারবেন।",
            show_alert=True,
        )
        return

    await query.answer()

    try:
        action, pending_id_text = query.data.split(":", 1)
        pending_id = int(pending_id_text)
    except (ValueError, AttributeError):
        await query.answer("অবৈধ অনুরোধ।", show_alert=True)
        return

    pending = get_pending(pending_id)

    if not pending:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    user_chat_id, user_message_id, status = pending

    if status != "pending":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"এই মেসেজের অবস্থা: {status}"
        )
        return

    if action == "approve":
        # একাধিক অ্যাডমিন একসঙ্গে চাপলেও একই মেসেজ
        # একাধিকবার অনুমোদিত হওয়া আটকানোর চেষ্টা।
        if not change_status(pending_id, "pending", "publishing"):
            await query.message.reply_text(
                "এই মেসেজটি ইতিমধ্যে প্রসেস করা হচ্ছে।"
            )
            return

        try:
            await context.bot.copy_message(
                chat_id=GROUP_CHAT_ID,
                from_chat_id=user_chat_id,
                message_id=user_message_id,
            )

            change_status(pending_id, "publishing", "approved")

            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(
                f"✅ মেসেজটি গ্রুপে প্রকাশ হয়েছে। ID: {pending_id}"
            )

            try:
                await context.bot.send_message(
                    chat_id=user_chat_id,
                    text="✅ আপনার মেসেজটি অনুমোদিত হয়েছে।",
                )
            except TelegramError:
                log.exception("Could not notify user about approval")

        except TelegramError:
            log.exception("Could not publish approved message")
            change_status(pending_id, "publishing", "pending")
            await query.message.reply_text(
                "❌ গ্রুপে প্রকাশ করা যায়নি। "
                "গ্রুপের ID ও বটের অনুমতি পরীক্ষা করুন।"
            )

    elif action == "reject":
        if not change_status(pending_id, "pending", "rejected"):
            await query.message.reply_text(
                "এই মেসেজটি ইতিমধ্যে প্রসেস করা হয়েছে।"
            )
            return

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"❌ মেসেজটি Reject করা হয়েছে। ID: {pending_id}"
        )

        try:
            await context.bot.send_message(
                chat_id=user_chat_id,
                text="আপনার মেসেজটি এবার অনুমোদিত হয়নি।",
            )
        except TelegramError:
            log.exception("Could not notify user about rejection")


async def admin_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or user.id not in ADMIN_IDS:
        return

    if not message.reply_to_message:
        return

    user_chat_id = get_user_chat(
        message.chat_id,
        message.reply_to_message.message_id,
    )

    if user_chat_id is None:
        await message.reply_text(
            "⚠️ ইউজারের কপি করা মূল মেসেজে Reply করুন।"
        )
        return

    try:
        await context.bot.copy_message(
            chat_id=user_chat_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        await message.reply_text("✅ ব্যক্তিগত উত্তর পাঠানো হয়েছে।")

    except TelegramError:
        log.exception("Could not send admin reply")
        await message.reply_text(
            "❌ উত্তর পাঠানো যায়নি। "
            "ইউজার বট Start করেছেন কি না দেখুন।"
        )


def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        CallbackQueryHandler(
            approval_callback,
            pattern=r"^(approve|reject):\d+$",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.REPLY
            & ~filters.COMMAND,
            admin_reply,
        ),
        group=0,
    )

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            receive_user_message,
        ),
        group=1,
    )

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=f"{BASE_URL}/{WEBHOOK_PATH}",
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
