import asyncio
import struct
import json

from typing import Optional, Dict
from nonebot.plugin import PluginMetadata
from nonebot import get_driver, logger
from nonebot import on_message, get_bot
from nonebot.adapters.qq import GroupAtMessageCreateEvent, Bot, MessageSegment, C2CMessageCreateEvent

__plugin_meta__ = PluginMetadata(
    name="mcping-nonebot2",
    description="",
    usage=""
)

msg_im: int = 1

class NettyProtocolServer:
    def __init__(self, host: str = '0.0.0.0', port: int = 8888):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.clients: Dict[asyncio.StreamWriter, asyncio.Task] = {}
        self.running = False
    
        driver = get_driver()
        self.config = driver.config
        
        if hasattr(self.config, "netty_host"):
            self.host = self.config.netty_host
        if hasattr(self.config, "netty_port"):
            self.port = self.config.netty_port

    async def start(self):
        if self.running:
            logger.warning("服务已在运行中，无需重复启动")
            return
            
        try:
            self.server = await asyncio.start_server(
                self.handle_client,
                self.host,
                self.port
            )
            self.running = True
            logger.info(f"Netty服务启动在 {self.host}:{self.port}")
        except OSError as e:
            logger.error(f"启动服务失败: {e}")
            self.running = False
            return

    async def stop(self):
        if not self.running or not self.server:
            logger.warning("服务未运行，无需停止")
            return
            
        logger.info("正在关闭Netty服务...")
        self.running = False
        
        for writer in list(self.clients.keys()):  
            await self._remove_client(writer) 
        
        self.clients.clear()
        
        self.server.close()
        await self.server.wait_closed()
        self.server = None
        logger.info("Netty服务已停止")

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if not self.running:
            writer.close()
            await writer.wait_closed()
            return
            
        client_addr = writer.get_extra_info('peername')
        logger.info(f"新的客户端连接: {client_addr}")
        
        task = asyncio.create_task(self.client_loop(reader, writer, client_addr))
        self.clients[writer] = task
        
        try:
            await task
        except asyncio.CancelledError:
            logger.info(f"客户端 {client_addr} 任务被取消")
        except Exception as e:
            logger.error(f"客户端 {client_addr} 处理异常: {e}")
        finally:
            await self._remove_client(writer)
            logger.info(f"客户端 {client_addr} 连接已关闭")

    async def client_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client_addr: tuple):
        try:
            while self.running:
                header = await reader.readexactly(4)
                if not header:
                    break
                
                body_length = struct.unpack(">I", header)[0]
                logger.debug(f"来自 {client_addr} 的消息长度: {body_length} 字节")
                
                body = await reader.readexactly(body_length)
                response = await self.process_message(body, client_addr)
                
                if response:
                    await self.send_response(writer, response)
        
        except asyncio.IncompleteReadError:
            logger.info(f"客户端 {client_addr} 断开连接")
        except ConnectionResetError:
            logger.info(f"客户端 {client_addr} 强制断开连接")
        except asyncio.CancelledError:
            logger.info(f"客户端 {client_addr} 任务被取消")
        except Exception as e:
            logger.error(f"处理客户端 {client_addr} 时出错: {e}")
        finally:
            await self._remove_client(writer)

    async def process_message(self, data: bytes, client_addr: tuple) -> Optional[bytes]:
        try:
            message = json.loads(data.decode('utf-8'))
            logger.debug(f"收到来自 {client_addr} 的JSON消息: {message}")

            if "ping" in message:
                return b"{\"pong\":null}"

            bot = get_bot()
            if isinstance(bot, Bot):
                msg: MessageSegment
                if "image" in message:
                    image = message['image']
                    file = open(image, 'rb')
                    byte_data = file.read()
                    file.close()
                    msg = MessageSegment.file_image(byte_data)
                else:
                    msg = MessageSegment.text(message['text'])
                global msg_im
                if "user_id" in message:
                    await bot.send_to_c2c(message['user_id'], msg, message['msg_id'], msg_im, message['event_id'])
                else:
                    await bot.send_to_group(message['group_id'], msg, message['msg_id'], msg_im, message['event_id'])
                msg_im+=1
        
        except json.JSONDecodeError:
            text_message = data.decode('utf-8')
            logger.info(f"收到来自 {client_addr} 的文本消息: {text_message}")
            return f"已收到: {text_message}".encode('utf-8')
        except UnicodeDecodeError:
            logger.info(f"收到来自 {client_addr} 的二进制消息: {data[:16]}...")
            return b"Received binary data"

    async def send_response(self, writer: asyncio.StreamWriter, response: bytes):
        header = struct.pack(">I", len(response))
        writer.write(header + response)
        await writer.drain()
        logger.debug(f"已发送 {len(response)} 字节响应")

    async def broadcast_message(self, message: str):
        """向所有客户端广播消息"""
        if not self.running or not self.clients:
            logger.warning("服务未运行或无客户端连接，无法广播")
            return

        data = message.encode()
        tasks = []
        writers = list(self.clients.keys())
        for writer in writers:
            if writer.is_closing():
                continue

            async def send_single(writer: asyncio.StreamWriter):
                try:
                    writer.write(len(data).to_bytes(4))
                    writer.write(data)
                    await writer.drain() 
                    logger.debug(f"消息广播至客户端 {writer.get_extra_info('peername')}")
                except (ConnectionResetError, asyncio.CancelledError):
                    logger.warning(f"客户端 {writer.get_extra_info('peername')} 连接已断开，移除")
                    await self._remove_client(writer)
                except Exception as e:
                    logger.error(f"广播消息失败: {e}")

            tasks.append(send_single(writer))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _remove_client(self, writer: asyncio.StreamWriter):
        """安全移除客户端并关闭连接"""
        if writer in self.clients:
            task = self.clients[writer]
            task.cancel()
            del self.clients[writer]
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.warning(f"关闭客户端时出错: {e}")

netty_server = NettyProtocolServer()

qq_matcher = on_message(priority=0, block=False)

driver = get_driver()

@driver.on_startup
async def start_netty_server():
    logger.info("正在启动Netty兼容服务...")
    await netty_server.start()

@driver.on_shutdown
async def stop_netty_server():
    logger.info("正在关闭Netty兼容服务...")
    await netty_server.stop()

@qq_matcher.handle()
async def handle_qq(bot: Bot, ev: GroupAtMessageCreateEvent):
    group_id = ev.group_openid
    user_id = ev.get_user_id()
    messages = ev.get_message()
    event_id = ev.event_id
    msg_id = ev.id

    assert isinstance(bot, Bot), '仅适用于 QQ 机器人'

    data = json.dumps({
        'group_id': group_id, 'user_id': user_id, 'messages': messages.extract_plain_text(), 'event_id': event_id, 'msg_id': msg_id
    }, default=str)

    await netty_server.broadcast_message(data)

@qq_matcher.handle()
async def handle_qq(bot: Bot, ev: C2CMessageCreateEvent):
    user_id = ev.get_user_id()
    messages = ev.get_message()
    event_id = ev.event_id
    msg_id = ev.id

    assert isinstance(bot, Bot), '仅适用于 QQ 机器人'

    data = json.dumps({
        'is_user': True, 'user_id': user_id, 'messages': messages.extract_plain_text(), 'event_id': event_id, 'msg_id': msg_id
    }, default=str)

    await netty_server.broadcast_message(data)
