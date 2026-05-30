import io
import time
import re
import hashlib
import aiohttp
import PIL.Image
from collections import defaultdict, deque
from config import (
    CREATOR_ID,
    MAX_FILE_SIZE_BYTES,
    MAX_TEXT_DISPLAY_CHARS,
    CREATOR_MAX_TEXT_DISPLAY_CHARS,
    FILE_UPLOAD_RATE_LIMIT,
    FILE_UPLOAD_RATE_WINDOW,
    SAFE_TEXT_EXTENSIONS,
    SUSPICIOUS_PATTERNS,
)

file_upload_tracker = defaultdict(lambda: deque(maxlen=FILE_UPLOAD_RATE_LIMIT))
processed_file_hashes = set()


def _check_rate_limit(user_id: int) -> tuple[bool, str]:
    now = time.time()
    user_history = file_upload_tracker[user_id]
    while user_history and now - user_history[0] > FILE_UPLOAD_RATE_WINDOW:
        user_history.popleft()
    if len(user_history) >= FILE_UPLOAD_RATE_LIMIT:
        wait_time = int(FILE_UPLOAD_RATE_WINDOW - (now - user_history[0]))
        return False, f"Rate limit exceeded. You can upload {FILE_UPLOAD_RATE_LIMIT} files per 5 minutes. Please wait {wait_time} seconds."
    return True, ""


def _scan_content(text_content: str) -> tuple[bool, str]:
    content_lower = text_content.lower()
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, content_lower, re.IGNORECASE):
            return False, f"Content contains potentially malicious pattern: '{pattern[:50]}...'"
    return True, ""


def _get_content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:32]


def _check_explicit_consent(message_content: str) -> bool:
    if not message_content:
        return False
    consent_patterns = [
        r'!scott\s+read',
        r'!scott\s+analyze',
        r'!scott\s+process',
        r'read\s+this\s+file',
        r'analyze\s+(the\s+)?(attached?\s+)?file',
        r'look\s+at\s+(the\s+)?(attached?\s+)?file',
    ]
    content_lower = message_content.lower()
    for pattern in consent_patterns:
        if re.search(pattern, content_lower):
            return True
    return False


async def _process_text_file(attachment, session, parts, user_id, is_creator, skip_security_scan=False):
    try:
        async with session.get(attachment.url) as resp:
            if resp.status == 200:
                content_length = resp.headers.get('Content-Length')
                if content_length and int(content_length) > MAX_FILE_SIZE_BYTES:
                    parts.append(f"\n[File: {attachment.filename} - skipped (too large, {int(content_length):,} bytes)]")
                    return False
                chunks = []
                total_size = 0
                async for chunk in resp.content.iter_chunked(8192):
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE_BYTES:
                        parts.append(f"\n[File: {attachment.filename} - skipped (exceeds size limit during download)]")
                        break
                    chunks.append(chunk)
                else:
                    try:
                        file_bytes = b''.join(chunks)
                        file_hash = _get_content_hash(file_bytes)
                        if file_hash in processed_file_hashes:
                            parts.append(f"\n[File: {attachment.filename} - duplicate detected and skipped]")
                            return False
                        processed_file_hashes.add(file_hash)
                        text_content = file_bytes.decode('utf-8', errors='replace')
                        if not skip_security_scan:
                            is_safe, scan_reason = _scan_content(text_content)
                            if not is_safe:
                                parts.append(f"\n[File: {attachment.filename} - BLOCKED: {scan_reason}]")
                                print(f"[SECURITY] Blocked file from user {user_id}: {scan_reason}")
                                return False
                        if not is_creator:
                            file_upload_tracker[user_id].append(time.time())
                        max_chars = CREATOR_MAX_TEXT_DISPLAY_CHARS if is_creator else MAX_TEXT_DISPLAY_CHARS
                        file_info = f"\n\n[File: {attachment.filename}]\n```\n{text_content[:max_chars]}\n```"
                        if len(text_content) > max_chars:
                            file_info += f"\n...(truncated, total size: {len(text_content):,} chars)"
                        parts.append(file_info)
                        if skip_security_scan:
                            print(f"[CREATOR] Processed file: {attachment.filename}")
                        return True
                    except Exception as e:
                        print(f"Error processing text file {attachment.filename}: {e}")
    except aiohttp.ClientError as e:
        print(f"Network error downloading text file {attachment.filename}: {e}")
    return False


async def get_multimodal_content(message):
    """Processes message text and attachments with security controls."""
    parts = []
    if message.content:
        parts.append(message.content)
    if not message.attachments:
        return parts if parts else ["[No content]"]

    user_id = message.author.id
    is_creator = (user_id == CREATOR_ID)

    if not is_creator:
        allowed, limit_msg = _check_rate_limit(user_id)
        if not allowed:
            parts.append(f"\n[File upload blocked: {limit_msg}]")
            return parts

    has_explicit_consent = is_creator or _check_explicit_consent(message.content)

    async with aiohttp.ClientSession() as session:
        for attachment in message.attachments:
            filename = attachment.filename.lower()
            if any(filename.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']):
                try:
                    async with session.get(attachment.url) as resp:
                        if resp.status == 200:
                            img_data = await resp.read()
                            img_hash = _get_content_hash(img_data)
                            if img_hash in processed_file_hashes:
                                parts.append(f"\n[Image: {attachment.filename} - duplicate detected and skipped]")
                                continue
                            processed_file_hashes.add(img_hash)
                            if not is_creator:
                                file_upload_tracker[user_id].append(time.time())
                            try:
                                img = PIL.Image.open(io.BytesIO(img_data))
                                parts.append(img)
                            except PIL.UnidentifiedImageError as e:
                                print(f"Error: Corrupted image format for {attachment.filename}: {e}")
                            except PIL.Image.DecompressionBombError as e:
                                print(f"Error: Image too large for {attachment.filename}: {e}")
                except aiohttp.ClientError as e:
                    print(f"Network error downloading image {attachment.filename}: {e}")
            elif any(filename.endswith(ext) for ext in SAFE_TEXT_EXTENSIONS):
                if not has_explicit_consent:
                    parts.append(f"\n[File: {attachment.filename} - skipped (use '!scott read' to analyze text files)]")
                    continue
                await _process_text_file(attachment, session, parts, user_id, is_creator, skip_security_scan=False)
            elif any(filename.endswith(ext) for ext in ['.py', '.sh', '.ps1', '.json', '.xml', '.html', '.yaml', '.yml', '.log', '.ini', '.cfg', '.bat', '.js', '.ts', '.php', '.sql']):
                if not is_creator:
                    parts.append(f"\n[File: {attachment.filename} - rejected (unsafe file type)]")
                    print(f"[SECURITY] Rejected unsafe extension from {message.author}: {attachment.filename}")
                    continue
                await _process_text_file(attachment, session, parts, user_id, is_creator, skip_security_scan=True)
            elif filename.endswith('.pdf'):
                parts.append(f"\n[Attached PDF file: {attachment.filename} - PDF content cannot be read directly]")
            else:
                parts.append(f"\n[Attached file: {attachment.filename}]")
    return parts if parts else ["[File uploaded with no text content]"]
