import os

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import github_dispatch
import tmdb_search
from logger import get_logger

log = get_logger(__name__)
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN)

SUBS_DIR = "subs"
os.makedirs(SUBS_DIR, exist_ok=True)

user_sessions: dict[int, dict] = {}


def _reset_session(chat_id: int) -> None:
    user_sessions[chat_id] = {
        "mode": None,  # "single" or "batch"
        "tmdb_id": None,
        "drama_name": None,
        "season": None,
        "episode": None,
        "search_results": None,
        "srt_path": None,
        "srt_paths": [],
        "sub_type": None,
        "quality": None,
        "sub_format": None,
        "stage": "awaiting_mode",
    }


@bot.message_handler(commands=["start"])
def send_welcome(message):
    chat_id = message.chat.id
    _reset_session(chat_id)

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("1️⃣ Single Episode", callback_data="mode_single"),
        InlineKeyboardButton("2️⃣ Full Season (Archive)", callback_data="mode_batch"),
    )
    bot.reply_to(message, "👋 Welcome! What would you like to process?", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("mode_"))
def handle_mode_select(call):
    chat_id = call.message.chat.id
    mode = call.data.split("_")[1]

    if chat_id not in user_sessions:
        _reset_session(chat_id)

    user_sessions[chat_id]["mode"] = mode
    user_sessions[chat_id]["stage"] = "awaiting_drama_name"

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="🔍 Type the drama name to search (e.g. 'Agent Kim').",
    )


@bot.message_handler(func=lambda m: user_sessions.get(m.chat.id, {}).get("stage") == "awaiting_drama_name")
def handle_drama_search(message):
    chat_id = message.chat.id
    query = message.text.strip()

    results = tmdb_search.search_drama(query)
    if not results:
        bot.reply_to(message, "❌ No results found. Try a different name.")
        return

    user_sessions[chat_id]["search_results"] = results

    markup = InlineKeyboardMarkup(row_width=1)
    for i, r in enumerate(results):
        markup.add(InlineKeyboardButton(f"{i + 1}. {r['name']} ({r['year']})", callback_data=f"drama_{i}"))

    user_sessions[chat_id]["stage"] = "awaiting_drama_select"
    bot.send_message(chat_id, "🔍 Select the correct drama:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("drama_"))
def handle_drama_select(call):
    chat_id = call.message.chat.id
    idx = int(call.data.split("_")[1])

    session = user_sessions.get(chat_id)
    if not session or not session.get("search_results"):
        bot.answer_callback_query(call.id, "Session expired, please /start again.")
        return

    selected = session["search_results"][idx]
    session["tmdb_id"] = selected["id"]
    session["drama_name"] = selected["name"]
    session["stage"] = "awaiting_season"

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"✅ Selected: {selected['name']} ({selected['year']}) - TMDB ID: {selected['id']}\n\n📺 Now send the Season Number (e.g. 1):",
    )


@bot.message_handler(func=lambda m: user_sessions.get(m.chat.id, {}).get("stage") == "awaiting_season")
def handle_season(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if not text.isdigit():
        bot.reply_to(message, "❌ Please send a valid number for season.")
        return

    session = user_sessions[chat_id]
    session["season"] = text

    if session["mode"] == "single":
        session["stage"] = "awaiting_episode"
        bot.reply_to(message, "🎬 Now send the Episode Number (e.g. 7):")
    else:  # batch mode - no single episode number needed
        session["stage"] = "awaiting_archive"
        bot.reply_to(message, "📦 Now send the archive link (Pixeldrain 7z/zip with all episode videos):")


@bot.message_handler(func=lambda m: user_sessions.get(m.chat.id, {}).get("stage") == "awaiting_episode")
def handle_episode(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if not text.isdigit():
        bot.reply_to(message, "❌ Please send a valid number for episode.")
        return

    user_sessions[chat_id]["episode"] = text
    user_sessions[chat_id]["stage"] = "awaiting_quality"

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("1080p", callback_data="q_1080p"),
        InlineKeyboardButton("720p", callback_data="q_720p"),
        InlineKeyboardButton("540p", callback_data="q_540p"),
        InlineKeyboardButton("480p", callback_data="q_480p"),
    )
    bot.reply_to(message, "🎞️ What quality is the source video?", reply_markup=markup)


@bot.message_handler(func=lambda m: user_sessions.get(m.chat.id, {}).get("stage") == "awaiting_archive")
def handle_archive_link(message):
    chat_id = message.chat.id
    url = message.text.strip()

    if not url.startswith("http"):
        bot.reply_to(message, "❌ Please send a valid archive link.")
        return

    user_sessions[chat_id]["archive_url"] = url
    user_sessions[chat_id]["stage"] = "awaiting_batch_srt"
    user_sessions[chat_id]["srt_paths"] = []
    bot.reply_to(
        message,
        "📄 Now send the .srt files one by one, IN EPISODE ORDER (Episode 1 first, then 2, 3...).\n"
        "Type 'Done' when you've sent them all.",
    )


@bot.message_handler(
    content_types=["document"],
    func=lambda m: user_sessions.get(m.chat.id, {}).get("stage") == "awaiting_batch_srt",
)
def handle_batch_srt(message):
    chat_id = message.chat.id
    filename = message.document.file_name or ""
    if not filename.lower().endswith(".srt"):
        bot.reply_to(message, "❌ Please send a .srt subtitle file.")
        return

    file_info = bot.get_file(message.document.file_id)
    downloaded = bot.download_file(file_info.file_path)
    idx = len(user_sessions[chat_id]["srt_paths"]) + 1
    srt_path = os.path.join(SUBS_DIR, f"{chat_id}_batch_ep{idx}.srt")
    with open(srt_path, "wb") as f:
        f.write(downloaded)

    user_sessions[chat_id]["srt_paths"].append(srt_path)
    bot.reply_to(message, f"✅ Episode {idx} srt saved. Send the next one, or type 'Done'.")


@bot.message_handler(
    func=lambda m: user_sessions.get(m.chat.id, {}).get("stage") == "awaiting_batch_srt"
    and m.text
    and m.text.strip().lower() == "done"
)
def handle_batch_done(message):
    chat_id = message.chat.id
    session = user_sessions[chat_id]

    if not session["srt_paths"]:
        bot.reply_to(message, "❌ You haven't sent any .srt files yet.")
        return

    session["stage"] = "awaiting_batch_sub_type"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🇺🇸 English SRTs (Translate & Burn)", callback_data="btype_english"),
        InlineKeyboardButton("🇱🇰 Sinhala SRTs (Direct Burn)", callback_data="btype_sinhala"),
    )
    bot.send_message(
        chat_id,
        f"🎯 Got {len(session['srt_paths'])} subtitle files. What type are they?",
        reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("btype_"))
def handle_batch_sub_type(call):
    chat_id = call.message.chat.id
    sub_type = call.data.split("_")[1]

    session = user_sessions.get(chat_id)
    if not session or not session.get("srt_paths"):
        bot.answer_callback_query(call.id, "Session expired, please /start again.")
        return

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="⏳ Starting batch processing on GitHub Actions... This may take 1-2 hours.",
    )

    ok, result_msg = github_dispatch.run_batch_via_github_actions(
        session["srt_paths"],
        session["archive_url"],
        sub_type,
        chat_id,
        tmdb_id=session["tmdb_id"],
        season_number=session["season"],
    )
    bot.send_message(chat_id, result_msg)
    _reset_session(chat_id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("q_"))
def handle_quality_select(call):
    chat_id = call.message.chat.id
    quality = call.data.split("_", 1)[1]

    session = user_sessions.get(chat_id)
    if not session:
        bot.answer_callback_query(call.id, "Session expired, please /start again.")
        return

    session["quality"] = quality
    session["stage"] = "awaiting_sub_format"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(".srt file", callback_data="fmt_srt"),
        InlineKeyboardButton(".ass file", callback_data="fmt_ass"),
    )
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"✅ Quality: {quality}\n\n📄 What subtitle format will you upload?",
        reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("fmt_"))
def handle_format_select(call):
    chat_id = call.message.chat.id
    fmt = call.data.split("_", 1)[1]

    session = user_sessions.get(chat_id)
    if not session:
        bot.answer_callback_query(call.id, "Session expired, please /start again.")
        return

    session["sub_format"] = fmt
    session["stage"] = "awaiting_srt"

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"📄 Now upload the .{fmt} subtitle file.",
    )


@bot.message_handler(content_types=["document"], func=lambda m: user_sessions.get(m.chat.id, {}).get("stage") == "awaiting_srt")
def handle_srt(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id, {})
    expected_ext = session.get("sub_format", "srt")
    filename = message.document.file_name or ""
    if not filename.lower().endswith(f".{expected_ext}"):
        bot.reply_to(message, f"❌ Please send a .{expected_ext} subtitle file.")
        return

    if message.document.file_size and message.document.file_size > config.MAX_SRT_MB * 1024 * 1024:
        bot.reply_to(message, f"❌ That file is too large (limit {config.MAX_SRT_MB}MB).")
        return

    file_info = bot.get_file(message.document.file_id)
    downloaded = bot.download_file(file_info.file_path)
    srt_path = os.path.join(SUBS_DIR, f"{chat_id}_input.{expected_ext}")
    with open(srt_path, "wb") as f:
        f.write(downloaded)

    user_sessions[chat_id]["srt_path"] = srt_path
    user_sessions[chat_id]["stage"] = "awaiting_sub_type"

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

    session = user_sessions.get(chat_id)
    if not session or not session.get("srt_path"):
        bot.answer_callback_query(call.id, "Please upload the SRT file first.")
        return

    session["sub_type"] = sub_type
    session["stage"] = "awaiting_video_link"
    msg = (
        "👍 I'll translate this to Sinhala before burning.\n\n🔗 Now send your **video link** (Pixeldrain etc)."
        if sub_type == "english"
        else "🔥 Sinhala subtitles — no translation needed.\n\n🔗 Now send your **video link** (Pixeldrain etc)."
    )
    bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=msg, parse_mode="Markdown")


@bot.message_handler(func=lambda m: user_sessions.get(m.chat.id, {}).get("stage") == "awaiting_video_link")
def handle_video_link(message):
    chat_id = message.chat.id
    url = message.text.strip()

    session = user_sessions.get(chat_id)
    if not session or not session.get("srt_path") or not session.get("sub_type"):
        bot.reply_to(message, "❌ Please complete the previous steps first (/start).")
        return

    if not url.startswith("http"):
        bot.reply_to(message, "❌ Please send a valid video link (e.g. a Pixeldrain URL).")
        return

    bot.send_message(chat_id, "⏳ Preparing your job for GitHub Actions...")
    ok, result_msg = github_dispatch.run_via_github_actions(
        session["srt_path"],
        url,
        session["sub_type"],
        chat_id,
        tmdb_id=session["tmdb_id"],
        season_number=session["season"],
        episode_number=session["episode"],
        quality=session.get("quality", "1080p"),
        sub_format=session.get("sub_format", "srt"),
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
