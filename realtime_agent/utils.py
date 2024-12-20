import asyncio
import functools
from datetime import datetime
import logging

import aiohttp

from .logger import setup_logger

logger = setup_logger(name=__name__, log_level=logging.INFO)


def write_pcm_to_file(buffer: bytearray, file_name: str) -> None:
    """Helper function to write PCM data to a file."""
    with open(file_name, "ab") as f:  # append to file
        f.write(buffer)


def generate_file_name(prefix: str) -> str:
    # Create a timestamp for the file name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}.pcm"


class PCMWriter:
    def __init__(self, prefix: str, write_pcm: bool, buffer_size: int = 1024 * 64):
        self.write_pcm = write_pcm
        self.buffer = bytearray()
        self.buffer_size = buffer_size
        self.file_name = generate_file_name(prefix) if write_pcm else None
        self.loop = asyncio.get_event_loop()

    async def write(self, data: bytes) -> None:
        """Accumulate data into the buffer and write to file when necessary."""
        if not self.write_pcm:
            return

        self.buffer.extend(data)

        # Write to file if buffer is full
        if len(self.buffer) >= self.buffer_size:
            await self._flush()

    async def flush(self) -> None:
        """Write any remaining data in the buffer to the file."""
        if self.write_pcm and self.buffer:
            await self._flush()

    async def _flush(self) -> None:
        """Helper method to write the buffer to the file."""
        if self.file_name:
            await self.loop.run_in_executor(
                None,
                functools.partial(write_pcm_to_file, self.buffer[:], self.file_name),
            )
        self.buffer.clear()

async def notify_user_left_channel(user_id: str, channel_name: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post("http://localhost:3013/user_left", json={"user_id": user_id, "channel_name": channel_name}) as response:
            if response.status == 200:
                logger.info(f"Successfully notified server about user {user_id} from channel {channel_name} leaving")
            else:
                logger.error(f"Failed to notify server about user {user_id} leaving: {response.status}")

async def clear_all_remote_user() -> None:
    async with aiohttp.ClientSession() as session:
        async with session.get("http://localhost:3013/clearAll") as response:
            if response.status == 200:
                logger.info("Successfully cleared all data on the server")
            else:
                logger.error(f"Failed to clear all data on the server: {response.status}")