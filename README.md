# AnySticker

Download and convert any Telegram sticker or custom emoji into open formats. Everything delivered in one ZIP.

Try it live: [@AnyStickerDownloadBot](https://t.me/AnyStickerDownloadBot)

---

## What it does

Send the bot a sticker, a custom emoji, or a sticker pack link and get back every useful format bundled into a single ZIP file.

**Animated sticker** — `.tgs` `.json` `.lottie`

**Static sticker** — `.webp` `.png` `.jpg`

**Video sticker** — `.webm`

**Custom emoji** — same as above, extracted from any message

**Pack link** — entire pack converted and zipped in one go

---

## Self-hosting

```bash
git clone https://github.com/bytasim/anystickerdownload
cd anystickerdownload

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# add your BOT_TOKEN to .env

python bot.py
```

## Environment

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | yes | From [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | no | Comma-separated Telegram user IDs for admin commands |
| `DB_PATH` | no | SQLite file path, defaults to `users.db` |

---

## Stack

- [aiogram 3](https://github.com/aiogram/aiogram) — async Telegram bot framework
- [rlottie-python](https://github.com/nicholasdille/rlottie-python) — renders animated stickers to PNG and GIF
- [Pillow](https://python-pillow.org) — image conversion
- [aiosqlite](https://github.com/omnilib/aiosqlite) — async SQLite for user tracking

---

Built to be simple, fast, and self-hostable. No external services, no database servers, just one Python process and a SQLite file.
