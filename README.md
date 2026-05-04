# Scottbott

A Discord bot with a configurable personality, persistent memory, image generation, and music generation — all powered by Google's Gemini API.

Lightweight by design: no voice chat, no audio decoding, no native libs. Runs comfortably on a 1 GB / 1 OCPU VM.

## Features

- **Text chat** with Gemini, persistent per-channel context (50-message rolling window backed by SQLite)
- **Per-user memory** — `!scott remember [text]` / `!scott forget [text]` / `!scott facts [@user]`
- **Image generation** — `!scott image [prompt]`
- **Music generation** (Lyria 3) — `!scott song [prompt]` for a 30-second clip, `!scott song long [prompt]` for a full song with vocals
  - Note: Lyria 3 requires billing enabled on your Google Cloud project. Other features work on the free tier.

## Project layout

```
scottbott/
├── scottbott.py        Main bot entry point and command handlers
├── memory.py           SQLite-backed memory and conversation persistence
├── requirements.txt    Python dependencies (5 packages)
├── run.bat             Windows launcher (auto-installs Python on first run)
├── install_python.bat  Bootstraps a portable Python 3.11 in ./python/
├── .env.example        Template for .env (fill in your secrets)
└── README.md
```

## Prerequisites

You need two things before the bot can run:

1. **A Discord bot token.** Create an application at https://discord.com/developers/applications, add a Bot, copy the token, enable the *Server Members* and *Message Content* intents.
2. **A Gemini API key.** Get one for free at https://aistudio.google.com/. Used for text chat, image generation, and music generation.

That's it — no service account, no Cloud project setup needed for the basic bot. (You only need a Cloud project with billing if you want to use `!scott song`.)

## Local setup (Windows)

1. Clone the repo: `git clone https://github.com/<you>/scottbott.git`
2. Copy `.env.example` to `.env` and fill in your tokens / API keys.
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
cp .env.example .env
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

Check logs:

```bash
tail -f /var/log/scottbott.log
```

### 5. Updating the bot later

On your local machine:

```bash
git add .
git commit -m "your change"
git push
```

On the Oracle VM:

```bash
ssh ubuntu@<your-vm-ip>
cd scottbott
git pull
sudo systemctl restart scottbott
```

## Troubleshooting

- **`!scott song` returns "no audio and no text"** — Lyria 3 requires billing enabled on your Google Cloud project. The other features work without billing.
- **Bot says "I cannot generate explicit or inappropriate images"** — keyword filter in the image command. Edit the `explicit_keywords` list in `scottbott.py` if you want to adjust.
- **Bot doesn't respond when @-mentioned** — make sure you enabled the *Message Content* intent in the Discord developer portal AND the bot has permission to read the channel.

## License

MIT.
