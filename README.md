# Scottbott

A Discord bot with a configurable personality, persistent memory, image generation, and music generation. Chat powered by DeepSeek V4 Pro via NVIDIA NIM; image and music generation still use Google's Gemini API.

Lightweight by design: no voice chat, no audio decoding, no native libs. Runs comfortably on a 1 GB / 1 OCPU VM.

## Features

- **Text chat** with DeepSeek V4 Pro via NVIDIA NIM, persistent per-channel context backed by SQLite. Mention/reply flows use a focused 10-message slice to reduce cross-user contamination; the full 50-message window is still stored for continuity.
- **Per-user memory** — `!scott remember [text]` / `!scott forget [text]` / `!scott facts [@user]`
- **Image generation** — `!scott image [prompt]`
- **Music generation** (Lyria 3) — `!scott song [prompt]` for a 30-second clip, `!scott song long [prompt]` for a full song with vocals
  - Note: Lyria 3 requires billing enabled on your Google Cloud project. Other features work on the free tier.

## Project layout

```
scottbott/
├── scottbott.py        Main bot entry point (thin wiring layer)
├── config.py           Centralised environment & constant configuration
├── memory.py           SQLite-backed memory and conversation persistence
├── conversation.py     In-memory LRU cache + SQLite conversation storage
├── security.py         File upload scanning, rate limits, replay protection
├── prompts.py          System prompt builder & self-awareness context
├── ui.py               Discord modals & views (notes, personality editor)
├── commands/
│   └── scott.py        !scott subcommand handlers
├── services/
│   ├── ai.py           Gemini retry & NVIDIA NIM chat backend
│   ├── images.py       Replicate image generation + Grok prompt expansion
│   ├── music.py        Lyria song generation
│   └── search.py       DuckDuckGo web search
├── scripts/
│   ├── deploy.sh       Git-pull deploy with DB backup + service restart
│   ├── logrotate-scottbott   Logrotate config
│   └── install-logrotate.sh  Installs logrotate config
├── requirements.txt    Python dependencies
├── run.bat             Windows launcher (auto-installs Python on first run)
├── .env.local.example  Template for .env (fill in your secrets)
└── README.md
```

## Prerequisites

You need three things before the bot can run:

1. **A Discord bot token.** Create an application at https://discord.com/developers/applications, add a Bot, copy the token, enable the *Server Members* and *Message Content* intents.
2. **NVIDIA API key.** Used for text chat via NVIDIA NIM. Get one at https://build.nvidia.com/
3. **Gemini API key.** Used for image generation and music generation (Lyria). Get one free at https://aistudio.google.com/

You only need a Gemini key if you plan to use `!scott image` or `!scott song`. Text chat works with just the NVIDIA key.

You only need a Google Cloud project with billing if you want to use `!scott song`.

## Local setup (Windows)

1. Clone the repo: `git clone https://github.com/<you>/scottbott.git`
2. Copy `.env.local.example` to `.env` and fill in your tokens / API keys.
3. Double-click `run.bat`.

The launcher auto-installs a portable Python 3.11 to `./python/` on first run, then starts the bot. No system-wide installs.

## Local setup (Linux / macOS)

```bash
git clone https://github.com/<you>/scottbott.git
cd scottbott

# System dependencies (just Python 3.11+)
sudo apt update && sudo apt install -y python3.11 python3.11-venv

# Python deps
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.local.example .env
# edit .env with your tokens

# Run
python scottbott.py
```

## Deploying to Oracle Cloud free tier

Oracle's Always Free tier gives you a perpetual VM at no cost. Two shapes work:

- **`VM.Standard.E2.1.Micro`** — 1 OCPU, 1 GB RAM (AMD x86). Sufficient for this bot.
- **`VM.Standard.A1.Flex`** — up to 4 OCPUs, 24 GB RAM (Ampere ARM). Way more than needed; pick this if available.

Memory profile of the bot at idle: ~415 MB. Active use: ~440-500 MB peaks during image/song generation. Comfortably fits the 1 GB shape with ~500 MB headroom.

### 1. Provision the VM

Sign up at https://cloud.oracle.com/, then:

1. Compute → Instances → **Create Instance**
2. Image: **Canonical Ubuntu 22.04** or **24.04**
3. Shape: pick `A1.Flex` if available, otherwise `E2.1.Micro`
4. Networking: leave defaults; ensure SSH is allowed
5. Add your SSH public key
6. Create

Note the public IP when it's ready.

### 2. Provision the bot

SSH in:

```bash
ssh ubuntu@<your-vm-ip>
```

Install Python and clone:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git

git clone https://github.com/<you>/scottbott.git
cd scottbott

python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Upload your `.env` (from your local machine):

```bash
scp .env ubuntu@<your-vm-ip>:/home/ubuntu/scottbott/
```

(Or `nano .env` on the VM and paste the contents.)

### 3. Add a swap file (highly recommended on the 1 GB shape)

Skip this on A1.Flex; only relevant for the 1 GB micro shape. Prevents the kernel OOM-killer from terminating the bot during peak memory usage.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 4. Run as a systemd service

Create `/etc/systemd/system/scottbott.service`:

```ini
[Unit]
Description=Scottbott Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/scottbott
EnvironmentFile=/home/ubuntu/scottbott/.env
Environment=MALLOC_ARENA_MAX=2
ExecStart=/home/ubuntu/scottbott/.venv/bin/python /home/ubuntu/scottbott/scottbott.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/scottbott.log
StandardError=append:/var/log/scottbott.log

[Install]
WantedBy=multi-user.target
```

The `MALLOC_ARENA_MAX=2` line trims about 50-100 MB of glibc allocator overhead. Useful on the 1 GB shape.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable scottbott
sudo systemctl start scottbott
sudo systemctl status scottbott
```

### 4b. Add log rotation

The service writes to `/var/log/scottbott.log`. Without rotation that file will grow forever. Install the bundled logrotate config:

```bash
cd scottbott
sudo bash scripts/install-logrotate.sh
```

This keeps 14 days of compressed logs. You can verify it works with:

```bash
sudo logrotate -d /etc/logrotate.d/scottbott
```

Check logs:

```bash
sudo tail -f /var/log/scottbott.log
```

### 5. Updating the bot later

**Push from your local machine:**

```bash
git add .
git commit -m "your change"
git push
```

**Deploy on the server** (backs up `scott_memory.db`, pulls code, restarts, verifies):

```bash
ssh ubuntu@<your-vm-ip>
cd scottbott
bash scripts/deploy.sh
```

The script:
1. Backs up `scott_memory.db` to `backups/scott_memory.db.<timestamp>`
2. Runs `git pull`
3. Restarts the systemd service
4. Waits and checks the service is actually active

If the deploy fails, your DB backup is in `~/scottbott/backups/`.

## Architecture notes

**Speaker isolation.** The bot uses a structured `--- SPEAKER CONTEXT ---` block injected right before each user turn: `ACTIVE_USER_ID`, `ACTIVE_USERNAME`, `REPLY_ONLY_TO`, `IGNORE_OTHER_NAMES`, and `REPLIED_TO_USER_ID/REPLIED_TO_USERNAME` when replying to another user. A one-pass validator reruns the model once if the response accidentally names a wrong user from the recent history.

**Memory TTL.** Facts are tagged at write time (`personal`, `contextual`, or `session`). Only `personal` facts are injected by default; `contextual` facts expire after 14 days and `session` facts after 2 days. The memory context block is hard-limited to ~200 tokens, dropping oldest items first so only the most recent facts survive.

## Troubleshooting

- **`!scott song` returns "no audio and no text"** — Lyria 3 requires billing enabled on your Google Cloud project. The other features work without billing.
- **Bot says "I cannot generate explicit or inappropriate images"** — keyword filter in the image command. Edit the `_NSFW_TAGS` set in `commands/scott.py` if you want to adjust.
- **Bot doesn't respond when @-mentioned** — make sure you enabled the *Message Content* intent in the Discord developer portal AND the bot has permission to read the channel.

## License

MIT.
