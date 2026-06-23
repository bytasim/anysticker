import asyncio
import io
import logging
import re
import zipfile
from contextlib import suppress
from typing import Any, Awaitable, Callable

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, Filter
from aiogram.types import BotCommand, BufferedInputFile, Message, Sticker, Update

import config
import convert

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

router = Router()
PACK_RE = re.compile(r"https?://t\.me/(addstickers|addemoji)/(\w+)", re.I)
BOT_USERNAME: str = "AnyStickerDownloadBot"

_STAT_COLS = {"stickers_dl", "emoji_dl", "packs_dl"}

async def init_db(db: aiosqlite.Connection) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY,
            username     TEXT,
            first_name   TEXT,
            stickers_dl  INTEGER DEFAULT 0,
            emoji_dl     INTEGER DEFAULT 0,
            packs_dl     INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    for col in _STAT_COLS:
        with suppress(Exception):
            await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
    await db.commit()

async def save_user(db: aiosqlite.Connection, user) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO users (id, username, first_name) VALUES (?, ?, ?)",
        (user.id, user.username, user.first_name),
    )
    await db.execute(
        "UPDATE users SET username = ?, first_name = ? WHERE id = ?",
        (user.username, user.first_name, user.id),
    )
    await db.commit()

async def increment_stat(db: aiosqlite.Connection, user_id: int, col: str, amount: int = 1) -> None:
    if col not in _STAT_COLS:
        return
    await db.execute(f"UPDATE users SET {col} = {col} + ? WHERE id = ?", (amount, user_id))
    await db.commit()

async def user_stats(db: aiosqlite.Connection, user_id: int) -> dict:
    async with db.execute(
        "SELECT stickers_dl, emoji_dl, packs_dl FROM users WHERE id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return {"stickers": row[0] or 0, "emoji": row[1] or 0, "packs": row[2] or 0} if row else {}

async def global_stats(db: aiosqlite.Connection) -> dict:
    async with db.execute(
        "SELECT COUNT(*), SUM(stickers_dl), SUM(emoji_dl), SUM(packs_dl) FROM users"
    ) as cur:
        row = await cur.fetchone()
    return {
        "users": row[0] or 0,
        "stickers": row[1] or 0,
        "emoji": row[2] or 0,
        "packs": row[3] or 0,
    }

async def all_user_ids(db: aiosqlite.Connection) -> list[int]:
    async with db.execute("SELECT id FROM users") as cur:
        return [row[0] for row in await cur.fetchall()]

class DbMiddleware(BaseMiddleware):
    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        data["db"] = self.db
        return await handler(event, data)

class HasCustomEmoji(Filter):
    async def __call__(self, message: Message) -> bool:
        return bool(
            message.entities
            and any(e.type == "custom_emoji" for e in message.entities)
        )

def stem(st: Sticker) -> str:
    return f"anysticker_{st.file_unique_id}"

def zip_name(st: Sticker) -> str:
    return f"{BOT_USERNAME}_{st.file_unique_id}.zip"

async def fetch(bot: Bot, file_id: str) -> bytes:
    f = await bot.get_file(file_id)
    bio = await bot.download_file(f.file_path)
    return bio.read()

def progress_bar(current: int, total: int, width: int = 10) -> str:
    filled = round(width * current / total) if total else 0
    return f"{'█' * filled}{'░' * (width - filled)}  {int(100 * current / total) if total else 0}%"

def emoji_icon(st: Sticker) -> str:
    if st.custom_emoji_id:
        fallback = st.emoji or "✨"
        return f'<tg-emoji emoji-id="{st.custom_emoji_id}">{fallback}</tg-emoji>'
    return st.emoji or ("🎬" if st.is_animated else "📹" if st.is_video else "🖼")

def caption_animated(st: Sticker) -> str:
    return (
        f"{emoji_icon(st)}\n\n"
        "<code>.tgs</code>  <code>.json</code>  <code>.lottie</code>"
        "  <code>.png</code>  <code>.gif</code>"
    )

def caption_static(st: Sticker) -> str:
    return (
        f"{emoji_icon(st)}\n\n"
        "<code>.webp</code>  <code>.png</code>  <code>.jpg</code>\n"
        "<code>.tgs</code>  <code>.json</code>  <code>.lottie</code>  <code>.gif</code>"
        "  —  not available for static stickers"
    )

def caption_video(st: Sticker) -> str:
    return (
        f"{emoji_icon(st)}\n\n"
        "<code>.webm</code>\n"
        "<code>.tgs</code>  <code>.json</code>  <code>.lottie</code>"
        "  <code>.png</code>  <code>.gif</code>  —  not available for video stickers"
    )

async def deliver_animated(msg: Message, bot: Bot, st: Sticker, raw: bytes) -> None:
    s = stem(st)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{s}.tgs", raw)
        zf.writestr(f"{s}.json", convert.tgs_to_json(raw))
        zf.writestr(f"{s}.lottie", convert.tgs_to_dotlottie(raw, s))
        png = convert.tgs_to_png(raw)
        if png:
            zf.writestr(f"{s}.png", png)
        gif = convert.tgs_to_gif(raw)
        if gif:
            zf.writestr(f"{s}.gif", gif)
    buf.seek(0)
    await msg.answer_document(
        BufferedInputFile(buf.read(), zip_name(st)),
        caption=caption_animated(st),
    )

async def deliver_static(msg: Message, st: Sticker, raw: bytes) -> None:
    s = stem(st)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{s}.webp", raw)
        zf.writestr(f"{s}.png", convert.webp_to_png(raw))
        zf.writestr(f"{s}.jpg", convert.webp_to_jpg(raw))
    buf.seek(0)
    await msg.answer_document(
        BufferedInputFile(buf.read(), zip_name(st)),
        caption=caption_static(st),
    )

async def deliver_video(msg: Message, st: Sticker, raw: bytes) -> None:
    s = stem(st)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{s}.webm", raw)
    buf.seek(0)
    await msg.answer_document(
        BufferedInputFile(buf.read(), zip_name(st)),
        caption=caption_video(st),
    )

@router.message(CommandStart())
async def cmd_start(message: Message, db: aiosqlite.Connection) -> None:
    await save_user(db, message.from_user)
    s = await user_stats(db, message.from_user.id)

    text = (
        "<b>Any Sticker</b>\n\n"
        "Send a sticker, paste a pack link, or send a message with custom emoji.\n"
        "You'll get every available format in one ZIP."
    )

    parts = []
    if s["stickers"]:
        parts.append(f"{s['stickers']:,} sticker{'s' if s['stickers'] != 1 else ''}")
    if s["emoji"]:
        parts.append(f"{s['emoji']:,} emoji")
    if s["packs"]:
        parts.append(f"{s['packs']:,} pack{'s' if s['packs'] != 1 else ''}")
    if parts:
        text += f"\n\n<i>You've converted {', '.join(parts)}.</i>"

    await message.answer(text)

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Animated</b>  →  <code>.tgs</code>  <code>.json</code>  <code>.lottie</code>  <code>.png</code>  <code>.gif</code>\n"
        "<b>Static</b>  →  <code>.webp</code>  <code>.png</code>  <code>.jpg</code>\n"
        "<b>Video</b>  →  <code>.webm</code>\n\n"
        "Paste <code>t.me/addstickers/Name</code> or <code>t.me/addemoji/Name</code> to download a full pack.\n"
        "Send a message with custom emoji to extract them."
    )

@router.message(F.sticker)
async def on_sticker(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
    st = message.sticker
    tip = await message.answer("⏳")
    try:
        async with asyncio.timeout(90):
            raw = await fetch(bot, st.file_id)
            await tip.delete()
            if st.is_animated:
                await deliver_animated(message, bot, st, raw)
            elif st.is_video:
                await deliver_video(message, st, raw)
            else:
                await deliver_static(message, st, raw)
        await increment_stat(db, message.from_user.id, "stickers_dl")
    except asyncio.TimeoutError:
        with suppress(Exception):
            await tip.delete()
        await message.answer("❌ Timed out. Please try again.")
    except Exception:
        log.exception("sticker error")
        with suppress(Exception):
            await tip.edit_text("❌ Something went wrong.")

@router.message(F.text.regexp(PACK_RE))
async def on_pack(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
    m = PACK_RE.search(message.text)
    pack_name = m.group(2)
    tip = await message.answer("⏳")

    try:
        sticker_set = await bot.get_sticker_set(pack_name)
    except Exception:
        await tip.edit_text("❌ Pack not found.")
        return

    total = len(sticker_set.stickers)
    await tip.edit_text(f"⏳  {progress_bar(0, total)}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, st in enumerate(sticker_set.stickers, 1):
            if i % 10 == 0:
                with suppress(Exception):
                    await tip.edit_text(f"⏳  {progress_bar(i, total)}")
            try:
                async with asyncio.timeout(30):
                    raw = await fetch(bot, st.file_id)
                s = stem(st)
                if st.is_animated:
                    zf.writestr(f"{s}.tgs", raw)
                    zf.writestr(f"{s}.json", convert.tgs_to_json(raw))
                    zf.writestr(f"{s}.lottie", convert.tgs_to_dotlottie(raw, s))
                    png = convert.tgs_to_png(raw)
                    if png:
                        zf.writestr(f"{s}.png", png)
                    gif = convert.tgs_to_gif(raw)
                    if gif:
                        zf.writestr(f"{s}.gif", gif)
                elif st.is_video:
                    zf.writestr(f"{s}.webm", raw)
                else:
                    zf.writestr(f"{s}.webp", raw)
                    zf.writestr(f"{s}.png", convert.webp_to_png(raw))
                    zf.writestr(f"{s}.jpg", convert.webp_to_jpg(raw))
            except asyncio.TimeoutError:
                log.warning(f"pack item {i} timed out, skipping")
            except Exception:
                log.exception(f"pack item {i} failed")

    buf.seek(0)
    with suppress(Exception):
        await tip.delete()
    await message.answer_document(
        BufferedInputFile(buf.read(), f"{BOT_USERNAME}_{pack_name}.zip"),
        caption=f"<b>{sticker_set.title}</b>  —  {total} stickers"
    )
    await increment_stat(db, message.from_user.id, "packs_dl")

@router.message(HasCustomEmoji())
async def on_custom_emoji(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
    ids = list({
        e.custom_emoji_id
        for e in message.entities
        if e.type == "custom_emoji"
    })
    tip = await message.answer("⏳")
    try:
        stickers = await bot.get_custom_emoji_stickers(ids)
    except Exception:
        await tip.edit_text("❌ Something went wrong.")
        return
    await tip.delete()
    for st in stickers:
        try:
            raw = await fetch(bot, st.file_id)
            if st.is_animated:
                await deliver_animated(message, bot, st, raw)
            elif st.is_video:
                await deliver_video(message, st, raw)
            else:
                await deliver_static(message, st, raw)
        except Exception:
            log.exception(f"emoji {st.file_id} failed")
    await increment_stat(db, message.from_user.id, "emoji_dl", len(stickers))

@router.message(Command("stats"))
async def cmd_stats(message: Message, db: aiosqlite.Connection) -> None:
    if message.from_user.id not in config.ADMIN_IDS:
        return
    g = await global_stats(db)
    await message.answer(
        f"<b>Stats</b>\n\n"
        f"Users      {g['users']:,}\n"
        f"Stickers   {g['stickers']:,}\n"
        f"Emoji      {g['emoji']:,}\n"
        f"Packs      {g['packs']:,}"
    )

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot, db: aiosqlite.Connection) -> None:
    if message.from_user.id not in config.ADMIN_IDS:
        return
    parts = message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Usage: /broadcast &lt;message&gt;")
        return
    text = parts[1].strip()
    ids = await all_user_ids(db)
    ok = fail = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(f"✅  {ok} sent   {fail} failed")

async def main() -> None:
    global BOT_USERNAME
    bot = Bot(
        config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    BOT_USERNAME = me.username

    await bot.set_my_commands([
        BotCommand(command="start", description="Start"),
        BotCommand(command="help", description="Formats & usage"),
    ])

    dp = Dispatcher()
    dp.include_router(router)

    async with aiosqlite.connect(config.DB_PATH) as db:
        await init_db(db)
        dp.update.middleware(DbMiddleware(db))
        log.info(f"@{BOT_USERNAME} polling…")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
