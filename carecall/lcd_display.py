"""
CareCall LCD Display — MHS 3.5" ILI9486, 480x320, RGB565 framebuffer on /dev/fb0.

Runs as a background thread. Call start() after the public URL is known.
Also runnable standalone: python -m carecall.lcd_display
"""
import os
import socket
import struct
import threading
import time
import logging

logger = logging.getLogger(__name__)

FB_DEVICE = '/dev/fb0'
WIDTH, HEIGHT = 480, 320
UPDATE_INTERVAL = 30  # seconds

BG_COLOR      = (0,   0,  32)
TITLE_COLOR   = (255, 200,  0)
LABEL_COLOR   = (100, 200, 255)
VALUE_COLOR   = (255, 255, 255)
URL_COLOR     = (100, 255, 100)
DIM_COLOR     = (100, 100, 100)

_public_url: str = 'Starting...'
_from_number: str = ''
_lock = threading.Lock()


def set_public_url(url: str):
    global _public_url
    with _lock:
        _public_url = url


def set_from_number(number: str):
    global _from_number
    with _lock:
        _from_number = number


def _get_public_url() -> str:
    with _lock:
        return _public_url


def _get_from_number() -> str:
    with _lock:
        return _from_number


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return 'No network'


def _load_font(size: int):
    from PIL import ImageFont
    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/freefont/FreeMono.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap_url(url: str, draw, font, max_width: int) -> list[str]:
    """Split URL into lines that fit within max_width pixels."""
    if draw.textlength(url, font=font) <= max_width:
        return [url]
    # Try to break at a natural point (after ://, before path segments)
    for sep in ('/', '-', '.'):
        mid = len(url) // 2
        idx = url.rfind(sep, 0, mid + 10)
        if idx > 0:
            part1, part2 = url[:idx + 1], url[idx + 1:]
            if (draw.textlength(part1, font=font) <= max_width and
                    draw.textlength(part2, font=font) <= max_width):
                return [part1, part2]
    # Hard split at midpoint
    mid = len(url) // 2
    return [url[:mid], url[mid:]]


def _render(ip: str, url: str, from_number: str):
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (WIDTH, HEIGHT), color=BG_COLOR)
    d = ImageDraw.Draw(img)

    font_title  = _load_font(34)
    font_label  = _load_font(22)
    font_value  = _load_font(30)
    font_url    = _load_font(22)
    font_small  = _load_font(17)

    # ── Title ─────────────────────────────────────────────────────────────
    d.text((WIDTH // 2, 16), 'CareCall', font=font_title,
           fill=TITLE_COLOR, anchor='mt')
    d.line([(16, 60), (WIDTH - 16, 60)], fill=DIM_COLOR, width=1)

    # ── Local IP ──────────────────────────────────────────────────────────
    d.text((20, 70),  'Local IP',  font=font_label, fill=LABEL_COLOR)
    d.text((20, 96),  ip,          font=font_value, fill=VALUE_COLOR)
    d.line([(16, 136), (WIDTH - 16, 136)], fill=DIM_COLOR, width=1)

    # ── Public URL ────────────────────────────────────────────────────────
    d.text((20, 144), 'Public URL', font=font_label, fill=LABEL_COLOR)
    lines = _wrap_url(url, d, font_url, WIDTH - 40)
    y = 168
    for line in lines:
        d.text((20, y), line, font=font_url, fill=URL_COLOR)
        y += 26
    d.line([(16, y + 4), (WIDTH - 16, y + 4)], fill=DIM_COLOR, width=1)

    # ── Call From ─────────────────────────────────────────────────────────
    y += 12
    d.text((20, y), 'Call From', font=font_label, fill=LABEL_COLOR)
    y += 24
    display_num = from_number if from_number else 'Not configured'
    d.text((20, y), display_num, font=font_value, fill=VALUE_COLOR)

    # ── Timestamp ─────────────────────────────────────────────────────────
    ts = time.strftime('%Y-%m-%d  %H:%M:%S')
    d.text((WIDTH // 2, HEIGHT - 8), ts, font=font_small,
           fill=DIM_COLOR, anchor='mb')

    return img


def _to_rgb565(img) -> bytes:
    data = bytearray(WIDTH * HEIGHT * 2)
    idx = 0
    for r, g, b in img.get_flattened_data() if hasattr(img, 'get_flattened_data') else img.getdata():
        pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        struct.pack_into('<H', data, idx, pixel)
        idx += 2
    return bytes(data)


def _write_fb(data: bytes):
    with open(FB_DEVICE, 'wb') as f:
        f.write(data)


def _update_once():
    ip   = _get_local_ip()
    url  = _get_public_url()
    num  = _get_from_number()
    img  = _render(ip, url, num)
    _write_fb(_to_rgb565(img))


def _loop():
    while True:
        try:
            _update_once()
        except Exception as e:
            logger.warning('LCD update failed: %s', e)
        time.sleep(UPDATE_INTERVAL)


def start(public_url: str | None = None, from_number: str | None = None):
    """Start the background LCD refresh thread."""
    if not os.path.exists(FB_DEVICE):
        logger.warning('LCD: %s not found — display thread not started', FB_DEVICE)
        return
    if public_url:
        set_public_url(public_url)
    if from_number:
        set_from_number(from_number)
    t = threading.Thread(target=_loop, name='lcd-display', daemon=True)
    t.start()
    logger.info('LCD display thread started')


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        set_public_url(sys.argv[1])
    _loop()
