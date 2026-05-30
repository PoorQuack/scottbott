import os
from dotenv import load_dotenv
from collections import defaultdict, deque

load_dotenv(override=True)

# Discord / Identity
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
_creator_raw = os.getenv("CREATOR_DISCORD_USER_ID")
try:
    CREATOR_ID = int(_creator_raw) if _creator_raw else 0
except ValueError:
    print(f"Warning: CREATOR_DISCORD_USER_ID is not a valid integer: {_creator_raw!r}")
    CREATOR_ID = 0
BOT_ID = 1428715040999084133
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Replicate
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
REPLICATE_IMAGE_MODEL = (
    "aisha-ai-official/wai-nsfw-illustrious-v12:"
    "0fc0fa9885b284901a6f9c0b4d67701fd7647d157b88371427d63f8089ce140e"
)

# xAI
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_IMAGE_MODEL = os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image-quality")
XAI_IMAGE_MODERATION = os.getenv("XAI_IMAGE_MODERATION", "none")

# NVIDIA NIM
NIM_API_KEY = os.getenv("NIM_API_KEY")
NIM_BASE_URL = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL = os.getenv("NIM_MODEL", "deepseek-ai/deepseek-v4-pro")
try:
    NIM_TIMEOUT = float(os.getenv("NIM_TIMEOUT", "1200"))
except ValueError:
    NIM_TIMEOUT = 1200.0
NIM_REASONING_EFFORT = os.getenv("NIM_REASONING_EFFORT", "high")
try:
    NIM_MAX_TOKENS = int(os.getenv("NIM_MAX_TOKENS", "16384"))
except ValueError:
    NIM_MAX_TOKENS = 16384
try:
    NIM_TOP_P = float(os.getenv("NIM_TOP_P", "1.0"))
except ValueError:
    NIM_TOP_P = 1.0

# File processing
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_TEXT_DISPLAY_CHARS = 8000
CREATOR_MAX_TEXT_DISPLAY_CHARS = 100000

# Upload security
FILE_UPLOAD_RATE_LIMIT = 3
FILE_UPLOAD_RATE_WINDOW = 300
SAFE_TEXT_EXTENSIONS = {'.txt', '.md', '.csv'}
SUSPICIOUS_PATTERNS = [
    r'ignore\s+(previous\s+)?instructions',
    r'system\s+prompt',
    r'ignore\s+above',
    r'jailbreak',
    r'DAN\s+mode',
    r'developer\s+mode',
    r'you\s+are\s+now\s+.*?(ignore|disregard)',
    r'your\s+new\s+(instructions?|role)',
    r'act\s+as\s+if\s+you\s+are',
    r'pretend\s+to\s+be',
    r'from\s+now\s+on\s+you\s+are',
    r'disable\s+(safety|filter|restriction)',
    r'api[_\s]?key',
    r'private[_\s]?key',
    r'password',
    r'token',
    r'secret',
    r'-----BEGIN',
    r'-----END',
]

# Song models
SONG_MODEL_CLIP = os.getenv("SONG_GENERATION_MODEL_CLIP", "lyria-3-clip-preview")
SONG_MODEL_PRO = os.getenv("SONG_GENERATION_MODEL_PRO", "lyria-3-pro-preview")

# Chat params
try:
    SCOTT_TEMP = float(os.getenv("SCOTT_TEMPERATURE", "0.7"))
except ValueError:
    SCOTT_TEMP = 0.7
try:
    CONTEXT_LIMIT = int(os.getenv("CHANNEL_CONTEXT_MESSAGES", "50"))
except ValueError:
    CONTEXT_LIMIT = 50

# Retry config
GEMINI_MAX_RETRIES = 5
GEMINI_BASE_DELAY = 1.0
GEMINI_MAX_DELAY = 60.0

# Self-awareness
SELF_AWARENESS_MAX_CHARS = 0
SELF_AWARENESS_EXCLUDE = {
    '.env', '.env.example', '__pycache__', '.venv', '.git',
    'ssh-key', 'gen-lang', 'client-', 'serviceaccount',
    'credentials', 'private_key',
}
SELF_AWARENESS_EXTENSIONS = {'.py', '.txt', '.md', '.json', '.yml', '.yaml', '.toml'}

# Set Replicate token globally
if REPLICATE_API_TOKEN:
    os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN
    print("Replicate client configured for image generation")
else:
    print("Warning: REPLICATE_API_TOKEN not set")
