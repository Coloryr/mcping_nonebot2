# mcping_nonebot2

[mcping](https://github.com/Coloryr/McPing) 与 NoneBot2 的通信插件。

从 2.0.0 开始，原 C# 版 McPing 后端的 ping、MOTD 绘图和 `/mc` 指令处理已经
合并进本插件：正常部署只需要运行 NoneBot，不再需要单独启动 `McPing.exe`。

> QQ 群“全量消息模式”（不 @ 机器人也能收到消息）需要
> `nonebot-adapter-qq>=1.7.0`，并在 QQ 开放平台开启群消息全量模式。

## 功能

- QQ / OneBot V11 中直接使用 `/mc` 查询 Minecraft 服务器
- Java 版 TCP status 协议 + Bedrock 版 UDP unconnected ping
- Java 版支持 `_minecraft._tcp` SRV 记录
- 生成 660x84 的 MOTD 图片：favicon、颜色、粗体/斜体/下划线、人数、版本、Ping
- 同一用户查询冷却、默认服务器、可配置字体/配色
- 不再监听任何 TCP 端口，无需单独的后端进程

## 安装

```bash
pip install -e .
```

或者把仓库放到 NoneBot 项目的 `plugins/` 目录下，并确保已安装：

```bash
pip install "pillow>=10.0" "dnspython>=2.0"
```

## 指令

| 指令 | 说明 |
| --- | --- |
| `/mc` | 使用 `MCPING_DEFAULT_IP`；未配置时返回用法 |
| `/mc <IP>` | 默认 Java 25565，失败后尝试 Bedrock 19132 |
| `/mc <IP> <端口>` | 指定端口；Java 失败后用同一端口尝试 Bedrock |
| `/mc <端口>` | 查询本机 `127.0.0.1:<端口>` |

全角冒号（`：`）和全角数字会自动转换为半角。
| `/mcping ...` | `/mc` 的别名 |

查询成功会发一张 MOTD 图片，失败发送 `获取[...]错误`。

## 配置

配置写进 NoneBot 的 `.env` / `.env.prod`（NoneBot 的自定义配置从 env 文件读取）。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MCPING_BUILTIN_ENABLE` | `true` | 是否由插件直接处理 `/mc` |
| `MCPING_DELAY` | `10` | 同一用户查询冷却秒数，0 关闭 |
| `MCPING_DEFAULT_IP` | 空 | 只发送 `/mc` 时使用的服务器，可带端口 |
| `MCPING_REQUEST_TIMEOUT` | `5` | 单次网络操作超时秒数 |
| `MCPING_OUTPUT_DIR` | 空 | 图片目录；留空使用 localstore 数据目录 |
| `MCPING_FONT_NORMAL` | `Microsoft YaHei` | 普通字体 |
| `MCPING_FONT_BOLD` | `Microsoft YaHei` | 粗体字体 |
| `MCPING_FONT_ITALIC` | `Microsoft YaHei` | 斜体字体 |
| `MCPING_FONT_EMOJI` | `Segoe UI Emoji` | emoji 字体 |
| `MCPING_BG_COLOR` | `#1C1C1C` | 背景色 |
| `MCPING_GOOD_PING_COLOR` | `#7CFC00` | Ping<=100 的颜色 |
| `MCPING_BAD_PING_COLOR` | `#FF4500` | Ping>100 的颜色 |
| `MCPING_PLAYER_COLOR` | `#A020F0` | 在线人数颜色 |
| `MCPING_VERSION_COLOR` | `#8B795E` | “服务器版本:”颜色 |

示例（值里有空格或 `#` 时请加引号）：

```dotenv
MCPING_DEFAULT_IP=mc.example.com
MCPING_DELAY=5
MCPING_REQUEST_TIMEOUT=4
MCPING_BG_COLOR="#1C1C1C"
MCPING_FONT_NORMAL="Microsoft YaHei"
```

## 从旧 C# 后端迁移

1. 停止正在运行的 `McPing.exe` / `dotnet McPing.dll`。
2. 重启 NoneBot。插件不再监听 8888 或其他 TCP 端口。
3. 在 QQ 里发送 `/mc mc.example.com` 验证。

## 字体

Windows 默认使用 `C:\Windows\Fonts\msyh.ttc`（微软雅黑）和
`seguiemj.ttf`（Segoe UI Emoji）。Linux 会自动尝试 Noto Sans CJK /
文泉驿 / DejaVu；如果中文显示为方框，请把字体配置改成系统实际安装的字体，
或直接写字体文件绝对路径。

## 图片目录

默认是 localstore 的数据目录，例如 Windows：
`%LOCALAPPDATA%\nonebot2\nonebot_plugin_mcping`。
启动日志会打印实际路径，也可以用 `MCPING_OUTPUT_DIR` 指定。

## 项目结构

```text
nonebot_plugin_mcping/
  __init__.py   # 插件入口、/mc 指令、QQ/OneBot 发送
  config.py     # 插件配置
  ping.py       # Java/Bedrock 查询、SRV、MOTD 文本解析
  render.py     # Pillow MOTD 绘图
```
