"""Minecraft Java / Bedrock 服务器状态查询。

Java 版走 TCP status 协议（握手 -> status request -> JSON），
Bedrock 版走 UDP unconnected ping。查询失败时返回 None。
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import random
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from nonebot import logger

PROTOCOL_VERSION = 754
DEFAULT_JAVA_PORT = 25565
DEFAULT_BEDROCK_PORT = 19132
MAX_PACKET_SIZE = 2 * 1024 * 1024

BEDROCK_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")

#: 旧版 MOTD 的 § 颜色代码
MC_COLORS = {
    "0": "#000000",
    "1": "#0000AA",
    "2": "#00AA00",
    "3": "#00AAAA",
    "4": "#AA0000",
    "5": "#AA00AA",
    "6": "#FFAA00",
    "7": "#AAAAAA",
    "8": "#555555",
    "9": "#5555FF",
    "a": "#55FF55",
    "b": "#55FFFF",
    "c": "#FF5555",
    "d": "#FF55FF",
    "e": "#FFFF55",
    "f": "#FFFFFF",
}

#: JSON 组件里的颜色名（与 Minecraft 一致，不是 CSS 的 red）
MC_NAMED_COLORS = {
    "black": "#000000",
    "dark_blue": "#0000AA",
    "dark_green": "#00AA00",
    "dark_aqua": "#00AAAA",
    "dark_red": "#AA0000",
    "dark_purple": "#AA00AA",
    "gold": "#FFAA00",
    "gray": "#AAAAAA",
    "dark_gray": "#555555",
    "blue": "#5555FF",
    "green": "#55FF55",
    "aqua": "#55FFFF",
    "red": "#FF5555",
    "light_purple": "#FF55FF",
    "yellow": "#FFFF55",
    "white": "#FFFFFF",
}


@dataclass
class Chat:
    """Minecraft 的文本组件（只保留渲染需要的字段）。"""

    text: str = ""
    extra: List["Chat"] = field(default_factory=list)
    color: Optional[str] = None
    bold: bool = False
    italic: bool = False
    underlined: bool = False
    strikethrough: bool = False
    obfuscated: bool = False

    def copy_style(self) -> "Chat":
        return Chat(
            color=self.color,
            bold=self.bold,
            italic=self.italic,
            underlined=self.underlined,
            strikethrough=self.strikethrough,
            obfuscated=self.obfuscated,
        )

    def plain_text(self) -> str:
        return self.text + "".join(child.plain_text() for child in self.extra)


@dataclass
class ServerStatus:
    host: str
    port: int
    edition: str
    version: str
    online: int
    max_players: int
    description: Chat
    ping: int
    favicon: Optional[bytes] = None
    protocol: Optional[int] = None


# ------------------------------------------------------------------ 文本解析


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_legacy(text: str) -> Chat:
    """把 §a红色§r文本 解析成 Chat 组件树。"""

    if not text:
        return Chat()

    lines = text.split("\n")
    root = Chat(extra=[])
    for line in lines:
        current = Chat()
        index = 0
        while index < len(line):
            char = line[index]
            if char == "§" and index + 1 < len(line):
                if current.text:
                    root.extra.append(current)
                current = current.copy_style()
                code = line[index + 1].lower()
                if code in MC_COLORS:
                    current.color = MC_COLORS[code]
                elif code == "r":
                    current.bold = False
                    current.italic = False
                    current.underlined = False
                    current.strikethrough = False
                    current.obfuscated = False
                    current.color = "#FFFFFF"
                elif code == "k":
                    current.obfuscated = True
                elif code == "l":
                    current.bold = True
                elif code == "m":
                    current.strikethrough = True
                elif code == "n":
                    current.underlined = True
                elif code == "o":
                    current.italic = True
                index += 2
            else:
                current.text += char
                index += 1
        root.extra.append(current)
        if len(lines) != 1:
            root.extra.append(Chat(text="\n"))
    return root


def parse_chat(value: Any) -> Chat:
    """解析 Java 版 status JSON 里的 description / version 文本。"""

    if value is None:
        return Chat()
    if isinstance(value, str):
        return parse_legacy(value)
    if isinstance(value, (int, float, bool)):
        return Chat(text=str(value))
    if isinstance(value, list):
        return Chat(extra=[parse_chat(item) for item in value])
    if isinstance(value, dict):
        text = value.get("text")
        text = "" if text is None else str(text)
        color = value.get("color")
        chat = Chat(
            text=text,
            color=color if isinstance(color, str) else None,
            bold=_to_bool(value.get("bold")),
            italic=_to_bool(value.get("italic")),
            underlined=_to_bool(value.get("underlined")),
            strikethrough=_to_bool(value.get("strikethrough")),
            obfuscated=_to_bool(value.get("obfuscated")),
        )
        extra = value.get("extra")
        if isinstance(extra, list):
            chat.extra = [parse_chat(item) for item in extra]
        elif isinstance(extra, dict):
            chat.extra = [parse_chat(extra)]
        # 部分服务端把 § 代码直接塞进 text；有 extra 时把两者拼起来
        if "§" in text:
            parsed_text = parse_legacy(text)
            if chat.extra:
                chat.extra = parsed_text.extra + chat.extra
                chat.text = ""
            else:
                return parsed_text
        return chat
    return Chat(text=str(value))


# ------------------------------------------------------------------ 地址解析


def split_address(address: str) -> Tuple[str, Optional[int]]:
    """拆分 host / host:port / [::1]:port。"""

    address = address.strip()
    if not address:
        return "", None
    if address.startswith("["):
        end = address.find("]")
        if end == -1:
            return address, None
        host = address[1:end]
        rest = address[end + 1 :]
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, None
    if address.count(":") == 1:
        host, _, port_text = address.rpartition(":")
        if port_text.isdigit():
            return host, int(port_text)
    return address, None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


# ------------------------------------------------------------------ 协议工具


def _encode_varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _decode_varint(data: bytes, offset: int) -> Tuple[int, int]:
    result = 0
    for index in range(5):
        if offset >= len(data):
            raise ValueError("VarInt 数据不完整")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return result, offset
    raise ValueError("VarInt 太长")


async def _read_varint(reader: asyncio.StreamReader) -> int:
    result = 0
    for index in range(5):
        raw = await reader.readexactly(1)
        byte = raw[0]
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return result
    raise ValueError("VarInt 太长")


# ------------------------------------------------------------------ Java


async def _query_java(
    handshake_host: str,
    connect_host: str,
    port: int,
    timeout: float,
) -> ServerStatus:
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(connect_host, port), timeout=timeout
        )

        host_bytes = handshake_host.encode("utf-8")
        handshake = (
            b"\x00"
            + _encode_varint(PROTOCOL_VERSION)
            + _encode_varint(len(host_bytes))
            + host_bytes
            + struct.pack(">H", port)
            + b"\x01"
        )
        writer.write(_encode_varint(len(handshake)) + handshake)
        writer.write(b"\x01\x00")  # status request
        await asyncio.wait_for(writer.drain(), timeout=timeout)

        started = time.perf_counter()
        length = await asyncio.wait_for(_read_varint(reader), timeout=timeout)
        if length <= 0 or length > MAX_PACKET_SIZE:
            raise ValueError(f"非法包长度: {length}")
        body = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
        ping = max(1, int((time.perf_counter() - started) * 1000))

        packet_id, offset = _decode_varint(body, 0)
        if packet_id != 0:
            raise ValueError(f"非法包 ID: {packet_id}")
        string_length, offset = _decode_varint(body, offset)
        if string_length < 0 or offset + string_length > len(body):
            raise ValueError("状态字符串长度越界")
        raw = body[offset : offset + string_length].decode("utf-8", "replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("status JSON 不是对象")

        version = data.get("version")
        version = version if isinstance(version, dict) else {}
        players = data.get("players")
        players = players if isinstance(players, dict) else {}

        favicon: Optional[bytes] = None
        favicon_raw = data.get("favicon")
        if isinstance(favicon_raw, str) and favicon_raw:
            try:
                encoded = favicon_raw.split(",", 1)[1] if "," in favicon_raw else favicon_raw
                favicon = base64.b64decode(encoded, validate=False)[: 2 * 1024 * 1024]
            except Exception:
                favicon = None

        protocol = _to_int(version.get("protocol"), -1)
        return ServerStatus(
            host=handshake_host,
            port=port,
            edition="java",
            version=str(version.get("name") or "未知"),
            online=_to_int(players.get("online")),
            max_players=_to_int(players.get("max")),
            description=parse_chat(data.get("description")),
            ping=ping,
            favicon=favicon,
            protocol=None if protocol < 0 else protocol,
        )
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except Exception:
                pass


# ------------------------------------------------------------------ Bedrock


class _BedrockProtocol(asyncio.DatagramProtocol):
    def __init__(self, payload: bytes, future: "asyncio.Future[bytes]") -> None:
        self.payload = payload
        self.future = future
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        transport.sendto(self.payload)  # type: ignore[attr-defined]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if not self.future.done():
            self.future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(exc)


def _parse_bedrock(
    data: bytes, host: str, port: int, ping: int
) -> ServerStatus:
    if len(data) < 35:
        raise ValueError("Bedrock 响应太短")
    if data[0] != 0x1C:
        raise ValueError("Bedrock 响应包 ID 不正确")
    if data[17:33] != BEDROCK_MAGIC:
        raise ValueError("Bedrock 响应 magic 不正确")

    text_length = struct.unpack(">H", data[33:35])[0]
    payload = data[35 : 35 + text_length]
    if len(payload) < text_length:
        raise ValueError("Bedrock 响应字符串不完整")

    fields = payload.decode("utf-8", "replace").split(";")
    if len(fields) < 6:
        raise ValueError("Bedrock 响应字段不足")

    motd = fields[1]
    if len(fields) > 7 and fields[7]:
        motd = f"{motd}\n{fields[7]}"

    version = fields[3] if len(fields) > 3 else ""
    game_type = fields[8] if len(fields) > 8 else ""
    version_name = f"{version} {game_type}".strip() or "未知"

    return ServerStatus(
        host=host,
        port=port,
        edition="bedrock",
        version=version_name,
        online=_to_int(fields[4]),
        max_players=_to_int(fields[5]),
        description=parse_legacy(motd),
        ping=ping,
    )


async def _query_bedrock(host: str, port: int, timeout: float) -> ServerStatus:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM), timeout=timeout
    )
    if not infos:
        raise OSError(f"无法解析 {host}")
    # 优先 IPv4，大部分 Bedrock 服务端只监听 IPv4
    infos.sort(key=lambda item: 0 if item[0] == socket.AF_INET else 1)
    sockaddr = infos[0][4]

    timestamp = int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF
    payload = (
        b"\x01"
        + struct.pack(">Q", timestamp)
        + BEDROCK_MAGIC
        + struct.pack(">Q", random.getrandbits(64))
    )

    future: "asyncio.Future[bytes]" = loop.create_future()
    started = time.perf_counter()
    transport, _ = await asyncio.wait_for(
        loop.create_datagram_endpoint(
            lambda: _BedrockProtocol(payload, future), remote_addr=sockaddr
        ),
        timeout=timeout,
    )
    try:
        data = await asyncio.wait_for(future, timeout=timeout)
        ping = max(1, int((time.perf_counter() - started) * 1000))
    finally:
        transport.close()

    return _parse_bedrock(data, host, port, ping)


# ------------------------------------------------------------------ SRV


async def _resolve_srv_doh(name: str, timeout: float) -> Optional[Tuple[str, int]]:
    """没有 dnspython 时用 DNS-over-HTTPS 查询 SRV。"""

    try:
        import httpx
    except ImportError:
        return None

    endpoints = [
        ("https://dns.google/resolve", {"name": name, "type": "SRV"}, {}),
        (
            "https://cloudflare-dns.com/dns-query",
            {"name": name, "type": "SRV"},
            {"accept": "application/dns-json"},
        ),
    ]
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for url, params, headers in endpoints:
                try:
                    response = await client.get(url, params=params, headers=headers)
                    response.raise_for_status()
                    payload = response.json()
                except Exception:
                    continue
                for answer in payload.get("Answer") or []:
                    if answer.get("type") != 33:
                        continue
                    parts = str(answer.get("data", "")).split()
                    if len(parts) < 4:
                        continue
                    target = parts[3].rstrip(".")
                    try:
                        srv_port = int(parts[2])
                    except ValueError:
                        continue
                    if target and 0 < srv_port <= 65535:
                        return target, srv_port
    except Exception:
        return None
    return None


async def resolve_srv(host: str, timeout: float) -> Optional[Tuple[str, int]]:
    name = f"_minecraft._tcp.{host}"
    try:
        import dns.asyncresolver
    except ImportError:
        return await _resolve_srv_doh(name, timeout)

    try:
        resolver = dns.asyncresolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answer = await resolver.resolve(name, "SRV")
        for record in answer:
            target = str(record.target).rstrip(".")
            if target:
                return target, int(record.port)
    except Exception:
        return await _resolve_srv_doh(name, timeout)
    return None


# ------------------------------------------------------------------ 对外入口


async def query(
    address: str, port: Optional[int] = None, timeout: float = 5.0
) -> Optional[ServerStatus]:
    """查询服务器；失败返回 None。

    address 可以带端口（host:port），也可以由 port 单独指定。
    Java 查询失败后会尝试 Bedrock（默认 19132，显式端口则用该端口）。
    """

    host, embedded_port = split_address(address)
    host = host.strip()
    if not host:
        return None
    if port is None:
        port = embedded_port
    if port is not None and not (1 <= port <= 65535):
        return None

    java_port = port if port is not None else DEFAULT_JAVA_PORT
    try:
        return await _query_java(host, host, java_port, timeout)
    except Exception as exc:
        logger.debug(f"mcping: Java 查询 {host}:{java_port} 失败: {exc!r}")

    # 只有没显式端口时才查 SRV
    if port is None and not _is_ip(host):
        try:
            srv = await resolve_srv(host, timeout)
        except Exception:
            srv = None
        if srv:
            target, srv_port = srv
            logger.debug(f"mcping: SRV {host} -> {target}:{srv_port}")
            try:
                return await _query_java(host, target, srv_port, timeout)
            except Exception as exc:
                logger.debug(
                    f"mcping: Java(SRV) 查询 {target}:{srv_port} 失败: {exc!r}"
                )

    if port is None or port == DEFAULT_JAVA_PORT:
        bedrock_port = DEFAULT_BEDROCK_PORT
    else:
        bedrock_port = port
    try:
        return await _query_bedrock(host, bedrock_port, timeout)
    except Exception as exc:
        logger.debug(f"mcping: Bedrock 查询 {host}:{bedrock_port} 失败: {exc!r}")

    return None
