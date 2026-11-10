"""把 Minecraft 服务器状态画成 660x84 的 MOTD 图片（Pillow 版）。"""

from __future__ import annotations

import os
import random
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from nonebot import logger
from PIL import Image, ImageColor, ImageDraw, ImageFont

from .ping import MC_COLORS, MC_NAMED_COLORS, Chat, ServerStatus, parse_legacy

CANVAS_SIZE = (660, 84)
FAVICON_SIZE = 64
FAVICON_POS = (10, 10)
TEXT_X = 80.0
MOTD_Y = 10.0
STATUS_Y = 55.0
LINE_HEIGHT = 20.0
MAX_TEXT_X = 580.0
PING_X = 600.0

_RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS

_WINDOWS_FONT_FILES = {
    "microsoft yahei": ("msyh.ttc", "msyhbd.ttc"),
    "微软雅黑": ("msyh.ttc", "msyhbd.ttc"),
    "microsoft yahei ui": ("msyh.ttc", "msyhbd.ttc"),
    "simhei": ("simhei.ttf", "simhei.ttf"),
    "simsun": ("simsun.ttc", "simsun.ttc"),
    "dengxian": ("Deng.ttf", "Dengb.ttf"),
    "segoe ui": ("segoeui.ttf", "segoeuib.ttf"),
    "segoe ui emoji": ("seguiemj.ttf", "seguiemj.ttf"),
    "consolas": ("consola.ttf", "consolab.ttf"),
}

_LINUX_FALLBACKS = [
    (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ),
    (
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    ),
    (
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ),
    (
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ),
]


@dataclass
class RenderStyle:
    font_normal: str = "Microsoft YaHei"
    font_bold: str = "Microsoft YaHei"
    font_italic: str = "Microsoft YaHei"
    font_emoji: str = "Segoe UI Emoji"
    bg_color: str = "#1C1C1C"
    good_ping_color: str = "#7CFC00"
    bad_ping_color: str = "#FF4500"
    player_color: str = "#A020F0"
    version_color: str = "#8B795E"
    font_size: int = 17


def _windows_fonts_dir() -> Path:
    return Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"


@lru_cache(maxsize=None)
def _find_font_file(name: str, bold: bool, italic: bool) -> Optional[str]:
    """按字体名找到实际字体文件；找不到返回 None。"""

    name = (name or "").strip()
    if not name:
        return None

    direct = Path(name).expanduser()
    if direct.is_file():
        return str(direct)

    key = name.lower()
    if key.endswith(" bold"):
        key = key[: -len(" bold")].strip()
        bold = True
    if key.endswith(" italic"):
        key = key[: -len(" italic")].strip()
        italic = True

    file_pair = _WINDOWS_FONT_FILES.get(key)
    if file_pair:
        filename = file_pair[1] if bold else file_pair[0]
        candidate = _windows_fonts_dir() / filename
        if candidate.is_file():
            return str(candidate)
        # Linux 上用户可能也写了 Windows 字体名
        for folder in (Path("/usr/share/fonts"), Path.home() / ".fonts"):
            candidate = folder / filename
            if candidate.is_file():
                return str(candidate)

    fonts_dir = _windows_fonts_dir()
    if fonts_dir.is_dir():
        normalized = re.sub(r"[^a-z0-9]", "", key)
        try:
            for path in fonts_dir.iterdir():
                if path.suffix.lower() not in {".ttf", ".ttc", ".otf"}:
                    continue
                if normalized and normalized in re.sub(
                    r"[^a-z0-9]", "", path.name.lower()
                ):
                    return str(path)
        except OSError:
            pass

    for regular, bold_file in _LINUX_FALLBACKS:
        candidate = Path(bold_file if bold else regular)
        if candidate.is_file():
            return str(candidate)
    return None


@lru_cache(maxsize=None)
def _load_font(path: Optional[str], size: int) -> Any:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception as exc:
            logger.warning(f"mcping: 加载字体 {path} 失败，使用默认字体: {exc!r}")
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


_EMOJI_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x1F000, 0x1FAFF),
    (0x1F1E6, 0x1F1FF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x2300, 0x23FF),
    (0x2B50, 0x2B55),
)

_EMOJI_JOINERS = {0xFE0E, 0xFE0F, 0x200D}


def _is_emoji_char(char: str) -> bool:
    codepoint = ord(char)
    if codepoint in _EMOJI_JOINERS:
        return True
    return any(start <= codepoint <= end for start, end in _EMOJI_RANGES)


def _iter_runs(text: str) -> Iterable[Tuple[str, bool]]:
    """把文本切成「普通 / emoji」连续片段。"""

    current: List[str] = []
    current_emoji: Optional[bool] = None
    for char in text:
        emoji = _is_emoji_char(char)
        # 变体选择符/零宽连接符跟随前一个片段，避免拆散 emoji 序列
        if ord(char) in _EMOJI_JOINERS and current:
            current.append(char)
            continue
        if current and current_emoji != emoji:
            yield "".join(current), bool(current_emoji)
            current = []
        current.append(char)
        current_emoji = emoji
    if current:
        yield "".join(current), bool(current_emoji)


def _parse_color(value: Optional[str], default: Tuple[int, int, int] = (255, 255, 255)) -> Any:
    if not value:
        return default
    text = str(value).strip()
    key = text.lower()
    text = MC_NAMED_COLORS.get(key, MC_COLORS.get(key, text))
    try:
        return ImageColor.getrgb(text)
    except (ValueError, AttributeError):
        return default


class MotdRenderer:
    def __init__(self, style: RenderStyle, output_dir: Path) -> None:
        self.style = style
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        size = max(8, int(style.font_size))
        self.font_normal = _load_font(
            _find_font_file(style.font_normal, bold=False, italic=False), size
        )
        self.font_bold = _load_font(
            _find_font_file(style.font_bold, bold=True, italic=False), size
        )
        self.font_italic = _load_font(
            _find_font_file(style.font_italic, bold=False, italic=True), size
        )
        self.font_emoji = _load_font(
            _find_font_file(style.font_emoji, bold=False, italic=False), size
        )

        self.bg_color = _parse_color(style.bg_color, (28, 28, 28))
        self.good_ping_color = _parse_color(style.good_ping_color, (124, 252, 0))
        self.bad_ping_color = _parse_color(style.bad_ping_color, (255, 69, 0))
        self.player_color = _parse_color(style.player_color, (160, 32, 240))
        self.version_color = _parse_color(style.version_color, (139, 121, 94))
        self.font_size = size

    # -------------------------------------------------------------- 绘制
    def _draw_text_runs(
        self,
        draw: ImageDraw.ImageDraw,
        x: float,
        y: float,
        text: str,
        font: Any,
        color: Any,
    ) -> float:
        for run, emoji in _iter_runs(text):
            run_font = self.font_emoji if emoji else font
            try:
                draw.text(
                    (x, y),
                    run,
                    font=run_font,
                    fill=color,
                    anchor="la",
                    embedded_color=emoji,
                )
            except (TypeError, ValueError):
                draw.text((x, y), run, font=run_font, fill=color, anchor="la")
            x += run_font.getlength(run)
        return x

    def _font_for(self, chat: Chat) -> Any:
        if chat.italic:
            return self.font_italic
        if chat.bold:
            return self.font_bold
        return self.font_normal

    def _draw_chat(
        self, draw: ImageDraw.ImageDraw, chat: Chat, x: float, y: float
    ) -> Tuple[float, float]:
        if x > MAX_TEXT_X:
            return x, y

        if chat.text == "\n":
            return TEXT_X, y + LINE_HEIGHT

        if chat.text:
            font = self._font_for(chat)
            color = _parse_color(chat.color)
            text = chat.text
            if chat.obfuscated:
                text = chr(random.randint(33, 126))

            for index, line in enumerate(text.split("\n")):
                if index:
                    x = TEXT_X
                    y += LINE_HEIGHT
                if not line:
                    continue
                line_start = x
                x = self._draw_text_runs(draw, x, y, line, font, color)
                if chat.underlined:
                    underline_y = y + self.font_size + 4
                    draw.line((line_start, underline_y, x, underline_y), fill=color, width=1)
                if chat.strikethrough:
                    strike_y = y + self.font_size * 0.7
                    draw.line((line_start, strike_y, x, strike_y), fill=color, width=1)

        for child in chat.extra:
            x, y = self._draw_chat(draw, child, x, y)

        return x, y

    def _paste_favicon(self, image: Image.Image, data: bytes) -> None:
        try:
            with Image.open(BytesIO(data)) as source:
                favicon = source.convert("RGBA")
        except Exception:
            return

        if favicon.width > FAVICON_SIZE or favicon.height > FAVICON_SIZE:
            favicon.thumbnail((FAVICON_SIZE, FAVICON_SIZE), _RESAMPLE)
        try:
            image.alpha_composite(favicon, FAVICON_POS)
        except ValueError:
            pass

    def _output_path(self, status: ServerStatus) -> Path:
        safe_host = re.sub(r"[^0-9A-Za-z._-]", "_", status.host)[:64] or "server"
        return self.output_dir / f"{safe_host}_{status.port}.png"

    def render(self, status: ServerStatus) -> Path:
        with self._lock:
            image = Image.new("RGBA", CANVAS_SIZE, self.bg_color)
            if status.favicon:
                self._paste_favicon(image, status.favicon)

            draw = ImageDraw.Draw(image)
            self._draw_chat(draw, status.description, TEXT_X, MOTD_Y)

            x = TEXT_X
            y = STATUS_Y
            players = f"在线人数:{status.online}/{status.max_players}"
            draw.text((x, y), players, font=self.font_normal, fill=self.player_color, anchor="la")
            x += self.font_normal.getlength(players) + 10.0

            version_label = "服务器版本:"
            draw.text(
                (x, y),
                version_label,
                font=self.font_normal,
                fill=self.version_color,
                anchor="la",
            )
            x += self.font_normal.getlength(version_label) + 10.0

            self._draw_chat(draw, parse_legacy(status.version), x, y)

            ping_color = (
                self.bad_ping_color if status.ping > 100 else self.good_ping_color
            )
            draw.text((PING_X, 10), "Ping", font=self.font_normal, fill=self.good_ping_color, anchor="la")
            draw.text((PING_X, 30), str(status.ping), font=self.font_normal, fill=ping_color, anchor="la")

            path = self._output_path(status)
            temp_path = path.with_name(
                f".{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp.png"
            )
            try:
                image.save(temp_path, "PNG")
                os.replace(temp_path, path)
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

            logger.debug(f"mcping: 已生成 {path}")
            return path
