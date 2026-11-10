"""mcping_nonebot2 插件配置。"""

from __future__ import annotations

from pydantic import BaseModel


class PluginConfig(BaseModel):
    """所有配置都可以通过同名大写环境变量覆盖。

    例如 MCPING_DELAY=5、MCPING_DEFAULT_IP=mc.example.com。
    """

    # ---------------------------------------------------------- 内置 /mc
    #: 是否在插件内直接处理 /mc（不再需要单独运行 C# McPing）
    mcping_builtin_enable: bool = True
    #: 同一用户两次查询之间的冷却秒数，0 表示不限制
    mcping_delay: int = 10
    #: 只发送 /mc 时使用的默认服务器，可带端口
    mcping_default_ip: str = ""
    #: 单次网络操作超时秒数
    mcping_request_timeout: float = 5.0
    #: 图片输出目录，留空则使用 nonebot-plugin-localstore 的数据目录
    mcping_output_dir: str = ""

    # ---------------------------------------------------------- MOTD 绘图
    mcping_font_normal: str = "Microsoft YaHei"
    mcping_font_bold: str = "Microsoft YaHei"
    mcping_font_italic: str = "Microsoft YaHei"
    mcping_font_emoji: str = "Segoe UI Emoji"
    mcping_bg_color: str = "#1C1C1C"
    mcping_good_ping_color: str = "#7CFC00"
    mcping_bad_ping_color: str = "#FF4500"
    mcping_player_color: str = "#A020F0"
    mcping_version_color: str = "#8B795E"


def get_config() -> PluginConfig:
    """从 NoneBot 全局配置中取出本插件配置。"""

    from nonebot import get_plugin_config

    return get_plugin_config(PluginConfig)
