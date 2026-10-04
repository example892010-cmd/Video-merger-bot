import asyncio
import logging
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import uvicorn

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("video-merger")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ["BASE_URL"].rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
WORK_DIR = Path(os.getenv("WORK_DIR", "/tmp/video_merger"))
RETENTION_DAYS = int(os.getenv("FILE_RETENTION_DAYS", "3650"))
MAX_SIZE_GB = float(os.getenv("MAX_FILE_SIZE_GB", "25"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))

WORK_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS = WORK_DIR / "downloads"
OUTPUTS = WORK_DIR / "outputs"
DOWNLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(title="Video Merger Bot")
bot = Client("video_merger_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
sessions = {}
job_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


def size_ok(path: Path) -> bool:
    return path.stat().st_size <= MAX_SIZE_GB * 1024**3


def kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Single Audio", callback_data="mode:single"),
         InlineKeyboardButton("🎶 Multi Audio", callback_data="mode:multi")],
        [InlineKeyboardButton("❌ Cancel", callback_data="mode:cancel")]
    ])


@app.get("/health")
async def health():
    return {"ok": True, "service": "video-merger-bot"}


@app.get("/download/{token}")
async def download(token: str):
    # Token is random and only maps to files created by this service.
    matches = list(OUTPUTS.glob(f"{token}_*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Download link not found")
    path = matches[0]
    return FileResponse(
        path=str(path),
        filename=path.name.removeprefix(token + "_"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{path.name.removeprefix(token + "_")}"'}
    )


def get_user_dir(user_id: int) -> Path:
    d = DOWNLOADS / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def run_cmd(*args: str):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace")[-8000:])
    return stdout.decode(errors="replace")


async def probe(path: Path) -> dict:
    import json
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(err.decode(errors="replace"))
    return json.loads(out)


async def merge_files(user_id: int, mode: str, files: list[Path], original_name: str) -> Path:
    # Normalize every source to MP4-compatible streams first.
    # We intentionally do not ask for permission: mode is selected once by the user.
    job_dir = get_user_dir(user_id) / f"job_{int(time.time())}_{secrets.token_hex(4)}"
    job_dir.mkdir(parents=True, exist_ok=True)

    normalized = []
    try:
        for idx, src in enumerate(files, 1):
            dst = job_dir / f"normalized_{idx:04d}.mp4"
            if mode == "multi":
                # Keep all audio streams. AAC is used for MP4 compatibility.
                cmd = [
                    "ffmpeg", "-y", "-i", str(src),
                    "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k",
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=und",
                    "-movflags", "+faststart",
                    str(dst)
                ]
            else:
                cmd = [
                    "ffmpeg", "-y", "-i", str(src),
                    "-map", "0:v:0", "-map", "0:a:0?",
                    "-map", "0:s?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k",
                    "-c:s", "mov_text",
                    "-movflags", "+faststart",
                    str(dst)
                ]
            await run_cmd(*cmd)
            normalized.append(dst)

        concat = job_dir / "concat.txt"
        concat.write_text("\n".join(f"file '{p.as_posix().replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'" for p in normalized))

        token = secrets.token_urlsafe(24)
        safe_name = Path(original_name).stem[:120] or "merged_video"
        output = OUTPUTS / f"{token}_{safe_name}.mp4"

        # The normalized parts use the same MP4-compatible encoding.
        await run_cmd(
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-c", "copy", "-movflags", "+faststart", str(output)
        )
        return output
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


async def cleanup_old_outputs():
    cutoff = time.time() - RETENTION_DAYS * 86400
    for p in OUTPUTS.glob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except Exception:
            log.exception("Cleanup failed for %s", p)


@bot.on_message(filters.command("start"))
async def start(_, message):
    sessions[message.from_user.id] = {"files": []}
    await message.reply(
        "🎬 **Video Merger Bot**\n\n"
        "Send your video files **one-by-one**.\n"
        "When all files are sent, use `/done`.\n\n"
        "The files will be merged in the order you send them."
    )


@bot.on_message(filters.command("list"))
async def list_files(_, message):
    s = sessions.get(message.from_user.id, {"files": []})
    if not s["files"]:
        await message.reply("📭 No files queued.")
        return
    text = "📋 **Queued files:**\n\n" + "\n".join(
        f"{i}. `{x['name']}`" for i, x in enumerate(s["files"], 1)
    )
    await message.reply(text)


@bot.on_message(filters.command("cancel"))
async def cancel(_, message):
    sessions.pop(message.from_user.id, None)
    await message.reply("❌ Queue cancelled.")


@bot.on_message(filters.command("status"))
async def status(_, message):
    s = sessions.get(message.from_user.id)
    if not s:
        await message.reply("ℹ️ No active queue.")
        return
    await message.reply(f"📦 Files queued: **{len(s['files'])}**")


@bot.on_message(filters.document | filters.video)
async def receive_file(_, message):
    uid = message.from_user.id
    s = sessions.setdefault(uid, {"files": []})

    media = message.document or message.video
    name = getattr(media, "file_name", None) or f"part_{len(s['files']) + 1}.mkv"
    user_dir = get_user_dir(uid)
    path = user_dir / f"{len(s['files']) + 1:04d}_{Path(name).name}"

    status_msg = await message.reply(f"📥 Downloading **{name}**...")
    try:
        await bot.download_media(message, file_name=str(path))
        if not size_ok(path):
            path.unlink(missing_ok=True)
            await status_msg.edit("❌ File exceeds the configured size limit.")
            return
        s["files"].append({"path": str(path), "name": name})
        await status_msg.edit(
            f"✅ **Part {len(s['files'])} received**\n`{name}`\n\n"
            "Send the next file or use `/done` when finished."
        )
    except Exception as e:
        path.unlink(missing_ok=True)
        await status_msg.edit(f"❌ Download failed: `{str(e)[:500]}`")


@bot.on_message(filters.command("done"))
async def done(_, message):
    s = sessions.get(message.from_user.id)
    if not s or not s["files"]:
        await message.reply("📭 Send at least one video file first.")
        return
    await message.reply(
        "🎧 **Select Audio Type**\n\n"
        "Choose **Single Audio** if every source has one audio track.\n"
        "Choose **Multi Audio** if your sources contain multiple audio tracks.\n\n"
        "Subtitle streams will be preserved/converted when MP4 supports them.",
        reply_markup=kb()
    )


@bot.on_callback_query(filters.regex(r"^mode:"))
async def choose_mode(_, query):
    uid = query.from_user.id
    mode = query.data.split(":", 1)[1]
    if mode == "cancel":
        sessions.pop(uid, None)
        await query.message.edit("❌ Cancelled.")
        await query.answer()
        return

    s = sessions.get(uid)
    if not s or not s["files"]:
        await query.answer("No queued files.", show_alert=True)
        return

    await query.message.edit("⏳ Processing started. Please wait...")
    await query.answer()

    async with job_semaphore:
        files = [Path(x["path"]) for x in s["files"]]
        original = Path(s["files"][0]["name"]).stem + "_merged.mp4"
        try:
            output = await merge_files(uid, mode, files, original)
            token = output.name.split("_", 1)[0]
            link = f"{BASE_URL}/download/{token}"
            size_gb = output.stat().st_size / 1024**3
            await query.message.edit(
                f"✅ **Merge completed!**\n\n"
                f"📁 `{original}`\n"
                f"📦 Size: `{size_gb:.2f} GB`\n"
                f"🎧 Mode: `{mode.title()} Audio`\n\n"
                f"⬇️ **Direct Chrome Download:**\n{link}\n\n"
                f"⚠️ The file remains until the configured retention cleanup removes it."
            )
        except Exception as e:
            log.exception("Merge failed")
            await query.message.edit(f"❌ Merge failed:\n`{str(e)[:2000]}`")
        finally:
            for x in s["files"]:
                Path(x["path"]).unlink(missing_ok=True)
            sessions.pop(uid, None)


async def cleanup_loop():
    while True:
        try:
            await cleanup_old_outputs()
        except Exception:
            log.exception("Cleanup loop error")
        await asyncio.sleep(3600)


async def start_web():
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    await bot.start()
    log.info("Bot started")
    await asyncio.gather(
        start_web(),
        cleanup_loop(),
        asyncio.Event().wait(),
    )


if __name__ == "__main__":
    asyncio.run(main())
