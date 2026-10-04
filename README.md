# Video Merger Bot

A Telegram bot that accepts video files one-by-one, then merges them into one final MP4.

## Features

- Send source files one-by-one.
- Keep the exact sending order.
- `/done` starts processing.
- Choose **Single Audio** or **Multi Audio** mode.
- Automatically converts/normalizes the final output to MP4.
- Multi-audio mode attempts to preserve all compatible audio streams.
- Attempts to preserve subtitle streams when they are MP4-compatible; incompatible subtitle formats are converted when possible.
- Uses FFmpeg.
- Generates a direct HTTPS download endpoint from the bot's web service.
- Automatic cleanup is configurable with `FILE_RETENTION_DAYS`; set it high for long-lived links.
- No secrets are stored in the repository.

## Important hosting limitation

This repository is designed to be self-hosted. Render Free is suitable for testing, but it is **not reliable permanent storage for large 10–20 GB files**. Its filesystem/runtime limits can cause files to disappear after restarts or redeploys.

For a truly permanent link, use persistent storage. If you still use Render Free, understand that the generated link is only as permanent as the underlying filesystem.

## Environment variables

Copy `.env.example` to `.env` for local use.

Required:
- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `BASE_URL`

Optional:
- `PORT` (default `8080`)
- `FILE_RETENTION_DAYS` (default `3650`)
- `MAX_FILE_SIZE_GB` (default `25`)
- `WORK_DIR` (default `/tmp/video_merger`)
- `ADMIN_IDS` (comma-separated Telegram user IDs)
- `MAX_CONCURRENT_JOBS` (default `1`)

## Local run

1. Install Python 3.11+.
2. Install FFmpeg.
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in the values.
5. `python bot.py`

The HTTP service listens on `PORT`.

## Render

A `render.yaml` is included.

1. Create a new GitHub repository.
2. Upload this project.
3. Create the Render service from the repository.
4. Add all environment variables in Render.
5. Set `BASE_URL` to the public HTTPS URL of your Render service.
6. Make sure FFmpeg is installed by the included Dockerfile.

## Usage

1. `/start`
2. Send files one-by-one.
3. `/list` to see the current queue.
4. `/done`
5. Choose:
   - **🎵 Single Audio**
   - **🎶 Multi Audio**
6. Wait for processing.
7. Open the generated direct download link in Chrome.

Commands:
- `/start` — start/reset a queue
- `/list` — show queued files
- `/done` — choose mode and start merge
- `/cancel` — cancel the current queue/job
- `/status` — show current status

## Media compatibility

MP4 is a container, not a universal codec. To produce a broadly compatible MP4, the bot transcodes video/audio when necessary. This can be CPU-intensive and may reduce quality compared with stream-copying.

For **Multi Audio**, all compatible audio tracks are mapped. Subtitle tracks are mapped when possible. Some subtitle formats cannot be embedded into MP4 and may need conversion or omission; the processing log records this.

## Security

Do not commit `.env`, bot tokens, API hashes, or generated session files.

The download endpoint uses a random token. It does not expose the original Telegram filename as the URL token.

## License

MIT
