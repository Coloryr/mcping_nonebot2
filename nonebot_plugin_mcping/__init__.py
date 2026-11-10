"""mcping-nonebot2 —— 在 QQ 中查询 Minecraft 服务器并生成 MOTD 图片。

从 2.0.0 起，C# 版 McPing 后端的 ping、MOTD 绘图和 /mc 指令处理已经合并进
本插件，正常部署不再需要单独运行 McPing.exe。

用法：
    /mc                 使用 MCPING_DEFAULT_IP（若已配置）
    /mc <IP>            默认端口（Java 25565，失败后尝试 Bedrock 19132）
    /mc <IP> <端口>     指定端口，Java 失败后会用同一端口尝试 Bedrock
    /mcping ...         等价别名

可选配置（.env）：
    MCPING_BUILTIN_ENABLE=true     是否由插件直接处理 /mc
    MCPING_DELAY=10                同一用户查询冷却秒数，0 为关闭
    MCPING_DEFAULT_IP=              只发 /mc 时使用的默认服务器
    MCPING_REQUEST_TIMEOUT=5       单次网络操作超时秒数
    MCPING_OUTPUT_DIR=             图片输出目录，留空用 localstore 数据目录
    MCPING_FONT_NORMAL=Microsoft YaHei
    MCPING_FONT_BOLD=Microsoft YaHei
    MCPING_FONT_ITALIC=Microsoft YaHei
    MCPING_FONT_EMOJI=Segoe UI Emoji
    MCPING_BG_COLOR=#1C1C1C
    MCPING_GOOD_PING_COLOR=#7CFC00
    MCPING_BAD_PING_COLOR=#FF4500
    MCPING_PLAYER_COLOR=#A020F0
    MCPING_VERSION_COLOR=#8B795E

插件不再监听任何 TCP 端口，所有查询都在 NoneBot 进程内完成。
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from pathlib import Path
from typing import Dict, Optional

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.qq import Bot as QQBot, MessageSegment as QQMessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .config import PluginConfig, get_config
from .ping import query
from .render import MotdRenderer, RenderStyle

__plugin_meta__ = PluginMetadata(
    name="nonebot_plugin_mcping",
    description="在 QQ 中查询 Minecraft 服务器并生成 MOTD 状态图",
    usage="/mc [IP] [端口] —— 查询 Java/BE 服务器并生成 MOTD 图片",
    type="application",
    config=PluginConfig,
    supported_adapters={"~qq", "~onebot.v11"},
    homepage="https://github.com/Coloryr/mcping_nonebot2",
)

try:  # OneBot V11 是可选适配器
    from nonebot.adapters.onebot.v11 import (
        Bot as OneBot11Bot,
        MessageSegment as OneBot11MessageSegment,
    )
except ImportError:  # pragma: no cover - 没装 onebot 适配器时
    OneBot11Bot = None  # type: ignore[assignment]
    OneBot11MessageSegment = None  # type: ignore[assignment]

USAGE = "输入/mc [IP] [端口] 来生成服务器Motd图片；只填端口时查询本机。支持JAVA版和BE版"

config = get_config()
driver = get_driver()


def _output_dir() -> Path:
    configured = config.mcping_output_dir.strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        try:
            from nonebot_plugin_localstore import get_plugin_data_dir

            path = Path(get_plugin_data_dir())
        except Exception:
            path = Path(__file__).parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


renderer = MotdRenderer(
    RenderStyle(
        font_normal=config.mcping_font_normal,
        font_bold=config.mcping_font_bold,
        font_italic=config.mcping_font_italic,
        font_emoji=config.mcping_font_emoji,
        bg_color=config.mcping_bg_color,
        good_ping_color=config.mcping_good_ping_color,
        bad_ping_color=config.mcping_bad_ping_color,
        player_color=config.mcping_player_color,
        version_color=config.mcping_version_color,
    ),
    _output_dir(),
)


class _Cooldown:
    def __init__(self, seconds: int) -> None:
        self.seconds = max(0, int(seconds))
        self._last: Dict[str, float] = {}

    def remaining(self, key: str) -> float:
        if self.seconds <= 0:
            return 0.0
        last = self._last.get(key)
        if last is None:
            return 0.0
        return max(0.0, self.seconds - (time.monotonic() - last))

    def hit(self, key: str) -> None:
        if self.seconds <= 0:
            return
        now = time.monotonic()
        self._last[key] = now
        if len(self._last) > 2048:
            cutoff = now - self.seconds
            self._last = {
                item_key: item_time
                for item_key, item_time in self._last.items()
                if item_time >= cutoff
            }


cooldown = _Cooldown(config.mcping_delay)


async def _send_message(bot: Bot, event: Event, message) -> bool:
    try:
        await bot.send(event=event, message=message)
        return True
    except Exception as exc:
        logger.warning(f"mcping: 发送消息失败: {exc!r}")
        return False


def _image_message(bot: Bot, path: Path):
    if isinstance(bot, QQBot):
        return QQMessageSegment.file_image(path)
    if OneBot11Bot is not None and isinstance(bot, OneBot11Bot):
        return OneBot11MessageSegment.image(path)
    raise RuntimeError(f"不支持的适配器: {type(bot).__name__}")


async def _handle_mc(bot: Bot, event: Event, args: Message) -> None:
    parts = unicodedata.normalize("NFKC", args.extract_plain_text()).strip().split()
    logger.info(f"mcping: 收到查询: {parts!r}")

    if len(parts) > 2:
        await _send_message(bot, event, USAGE)
        return

    port: Optional[int] = None
    if not parts:
        default_ip = config.mcping_default_ip.strip()
        if not default_ip:
            await _send_message(bot, event, USAGE)
            return
        address = default_ip
        display = default_ip
    elif len(parts) == 1:
        token = parts[0]
        if token.isdigit():
            port = int(token)
            address = "127.0.0.1"
            display = f"127.0.0.1:{token}"
            if not 1 <= port <= 65535:
                await _send_message(bot, event, f"获取[{display}]错误")
                return
        else:
            address = token
            display = token
    else:
        address = parts[0]
        raw_port = parts[1]
        display = f"{address}:{raw_port}"
        try:
            port = int(raw_port)
        except ValueError:
            await _send_message(bot, event, f"获取[{display}]错误")
            return
        if not 1 <= port <= 65535:
            await _send_message(bot, event, f"获取[{display}]错误")
            return

    user_key = event.get_user_id() or event.get_session_id()
    if cooldown.remaining(user_key) > 0:
        await _send_message(bot, event, "查询过于频繁")
        return
    await _send_message(bot, event, f"正在获取[{display}]")

    status = await query(
        address,
        port,
        timeout=max(0.5, float(config.mcping_request_timeout)),
    )
    if status is None:
        logger.warning(f"mcping: 查询 {display} 失败（Java/Bedrock 均不可达）")
        await _send_message(bot, event, f"获取[{display}]错误")
        return

    cooldown.hit(user_key)

    try:
        image_path = await asyncio.to_thread(renderer.render, status)
    except Exception as exc:
        logger.exception(f"mcping: 生成 MOTD 图片失败: {exc!r}")
        await _send_message(bot, event, f"获取[{display}]错误")
        return

    try:
        image_message = _image_message(bot, image_path)
    except Exception as exc:
        logger.error(f"mcping: 构造图片消息失败: {exc!r}")
        await _send_message(bot, event, f"获取[{display}]错误")
        return

    await _send_message(bot, event, image_message)
    logger.info(
        f"mcping: {display} -> {status.edition} {status.version} "
        f"{status.online}/{status.max_players} {status.ping}ms"
    )


if config.mcping_builtin_enable:
    mc_matcher = on_command("mc", aliases={"mcping"}, priority=10, block=True)

    @mc_matcher.handle()
    async def _mc_handler(
        bot: Bot, event: Event, args: Message = CommandArg()
    ) -> None:
        await _handle_mc(bot, event, args)


@driver.on_startup
async def _startup_log() -> None:
    logger.info(
        "mcping: 内置 /mc {}".format(
            "已启用" if config.mcping_builtin_enable else "已关闭"
        )
    )
