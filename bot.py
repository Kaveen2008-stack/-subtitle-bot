import os

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import github_dispatch
from logger import get_logger

log = get_logger(__name__)
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN)

SUBS_DIR = "subs"
os.makedirs(SUBS_DIR, exist_ok=True)

# chat_id -> {"srt_path": str|None, "sub_type": str|None}
user_sessions: dict[int, dict] = {}


def _reset_session(chat_id: int) -> None:
    user_sessions[chat_id] = {"srt_path": None, "sub_type": None}


@bot.message_handler(commands=["start"])
def send_welcome(message):
    chat_id = message.chat.id
    _reset_session(chat_id)
    bot.reply_to(message, "👋 Welcome! Upload your SRT subtitle file (English or Sinhala) to start.")


@bot.message_handler(content_types=["document"])
def handle_srt(message):
    chat_id = message.chat.id
    filename = message.document.file_name or ""
    if not filename.lower().endswith(".srt"):
        bot.reply_to(message, "❌ Please send a .srt subtitle file.")
        return

    if message.document.file_size and message.document.file_size > config.MAX_SRT_MB * 1024 * 1024:
        bot.reply_to(message, f"❌ That SRT is too large (limit {config.MAX_SRT_MB}MB).")
        return

    file_info = bot.get_file(message.document.file_id)
    downloaded = bot.download_file(file_info.file_path)
    srt_path = os.path.join(SUBS_DIR, f"{chat_id}_input.srt")
    with open(srt_path, "wb") as f:
        f.write(downloaded)

    user_sessions[chat_id] = {"srt_path": srt_path, "sub_type": None}

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🇺🇸 English SRT (Translate & Burn)", callback_data="type_english"),
        InlineKeyboardButton("🇱🇰 Sinhala SRT (Direct Burn)", callback_data="type_sinhala"),
    )
    bot.send_message(chat_id, "🎯 Got the subtitles. What type is it?", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("type_"))
def handle_sub_type(call):
    chat_id = call.message.chat.id
    sub_type = call.data.split("_")[1]

    if chat_id not in user_sessions or not user_sessions[chat_id].get("srt_path"):
        bot.answer_callback_query(call.id, "Please upload the SRT file first.")
        return

    user_sessions[chat_id]["sub_type"] = sub_type
    msg = (
        "👍 I'll translate this to Sinhala before burning.\n\n🔗 Now send your **video link** (Pixeldrain etc)."
        if sub_type == "english"
        else "🔥 Sinhala subtitles — no translation needed.\n\n🔗 Now send your **video link** (Pixeldrain etc)."
    )
    bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=msg, parse_mode="Markdown")


@bot.message_handler(func=lambda message: True)
def handle_video_link(message):
    chat_id = message.chat.id
    url = message.text.strip()

    session = user_sessions.get(chat_id)
    if not session or not session.get("srt_path") or not session.get("sub_type"):
        bot.reply_to(message, "❌ Please upload the SRT file and choose its type first (/start).")
        return

    if not url.startswith("http"):
        bot.reply_to(message, "❌ Please send a valid video link (e.g. a Pixeldrain URL).")
        return

    bot.send_message(chat_id, "⏳ Preparing your job for GitHub Actions...")
    ok, result_msg = github_dispatch.run_via_github_actions(
        session["srt_path"], url, session["sub_type"], chat_id
    )
    bot.send_message(chat_id, result_msg)
    _reset_session(chat_id)


def main():
    log.info("Bot starting (polling)...")
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception:
            log.exception("Polling crashed, restarting in 5s...")
            import time
            time.sleep(5)
            

if __name__ == "__main__":
    main()
