import asyncio
import base64
import logging
import os
from builtins import anext
from typing import Any

from agora.rtc.rtc_connection import RTCConnection, RTCConnInfo
from attr import dataclass

from agora_realtime_ai_api.rtc import Channel, ChatMessage, RtcEngine, RtcOptions

from .logger import setup_logger
from .realtime.struct import InputAudioBufferCommitted, InputAudioBufferSpeechStarted, InputAudioBufferSpeechStopped, InputAudioTranscription, ItemCreated, ItemInputAudioTranscriptionCompleted, RateLimitsUpdated, ResponseAudioDelta, ResponseAudioDone, ResponseAudioTranscriptDelta, ResponseAudioTranscriptDone, ResponseContentPartAdded, ResponseContentPartDone, ResponseCreated, ResponseDone, ResponseOutputItemAdded, ResponseOutputItemDone, ServerVADUpdateParams, SessionUpdate, SessionUpdateParams, SessionUpdated, Voices, to_json
from .realtime.connection import RealtimeApiConnection
from .tools import ClientToolCallResponse, ToolContext
from .utils import PCMWriter

# Set up the logger with color and timestamp support
logger = setup_logger(name=__name__, log_level=logging.INFO)

def _monitor_queue_size(queue: asyncio.Queue, queue_name: str, threshold: int = 5) -> None:
    queue_size = queue.qsize()
    if queue_size > threshold:
        logger.warning(f"Queue {queue_name} size exceeded {threshold}: current size {queue_size}")


async def wait_for_remote_user(channel: Channel, target_user_id: int) -> int:
    logger.info(f"Waiting for remote user to join, target user: {target_user_id}")
    remote_users = list(channel.remote_users.keys())
    if str(target_user_id) in remote_users:
        logger.info(f"Target user {target_user_id} already joined")
        return str(target_user_id)

    future = asyncio.Future[int]()

    channel.once("user_joined", lambda conn, user_id: future.set_result(user_id) if user_id == target_user_id else None)

    try:
        # Wait for the remote user with a timeout of 30 seconds
        remote_user = await asyncio.wait_for(future, timeout=30.0)
        return remote_user
    except KeyboardInterrupt:
        future.cancel()
        
    except Exception as e:
        logger.error(f"Error waiting for remote user: {e}")
        raise


@dataclass(frozen=True, kw_only=True)
class InferenceConfig:
    system_message: str | None = None
    turn_detection: ServerVADUpdateParams | None = None  # MARK: CHECK!
    voice: Voices | None = None


class RealtimeKitAgent:
    engine: RtcEngine
    channel: Channel
    connection: RealtimeApiConnection
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
    input_audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
    is_agent_busy: bool = False

    message_queue: asyncio.Queue[ResponseAudioTranscriptDelta] = (
        asyncio.Queue()
    )
    message_done_queue: asyncio.Queue[ResponseAudioTranscriptDone] = (
        asyncio.Queue()
    )
    tools: ToolContext | None = None

    _client_tool_futures: dict[str, asyncio.Future[ClientToolCallResponse]]

    @classmethod
    async def setup_and_run_agent(
        cls,
        *,
        engine: RtcEngine,
        options: RtcOptions,
        inference_config: InferenceConfig,
        tools: ToolContext | None,
        target_user_id: int,
    ) -> None:
        channel = engine.create_channel(options)
        await channel.connect()

        try:
            async with RealtimeApiConnection(
                base_uri=os.getenv("REALTIME_API_BASE_URI", "wss://api.openai.com"),
                api_key=os.getenv("OPENAI_API_KEY"),
                verbose=False,
            ) as connection:
                await connection.send_request(
                    SessionUpdate(
                        session=SessionUpdateParams(
                            # MARK: check this
                            turn_detection=inference_config.turn_detection,
                            tools=tools.model_description() if tools else [],
                            tool_choice="auto",
                            input_audio_format="pcm16",
                            output_audio_format="pcm16",
                            instructions=inference_config.system_message,
                            voice= 'echo',
                            model=os.environ.get("OPENAI_MODEL", "gpt-4o-realtime-preview"),
                            modalities=["text", "audio"],
                            temperature=0.8,
                            max_response_output_tokens="inf",
                            input_audio_transcription=InputAudioTranscription(model="whisper-1")
                        )
                    )
                )

                start_session_message = await anext(connection.listen())
                # assert isinstance(start_session_message, messages.StartSession)
                logger.info(
                    f"Session started: {start_session_message.session.id} model: {start_session_message.session.model}"
                )

                agent = cls(
                    connection=connection,
                    tools=tools,
                    channel=channel,
                    target_user_id=target_user_id,
                )
                await agent.run()

        finally:
            await channel.disconnect()
            await connection.close()

    def __init__(
        self,
        *,
        connection: RealtimeApiConnection,
        tools: ToolContext | None,
        channel: Channel,
        target_user_id: int,
    ) -> None:
        self.connection = connection
        self.tools = tools
        self._client_tool_futures = {}
        self.channel = channel
        self.subscribe_user = None
        self.write_pcm = os.environ.get("WRITE_AGENT_PCM", "false") == "true"
        self.target_user_id = target_user_id
        logger.info(f"Write PCM: {self.write_pcm}")

    async def run(self) -> None:
        try:

            def log_exception(t: asyncio.Task[Any]) -> None:
                if not t.cancelled() and t.exception():
                    logger.error(
                        "unhandled exception",
                        exc_info=t.exception(),
                    )

            self.subscribe_user = await wait_for_remote_user(self.channel,self.target_user_id)
            logger.info(f"Subscribing to user {self.subscribe_user}")
            await self.channel.subscribe_audio(self.subscribe_user)

            async def on_user_left(
                agora_rtc_conn: RTCConnection, user_id: int, reason: int
            ):
                logger.info(f"User left: {user_id}")
                if self.subscribe_user == user_id:
                    self.subscribe_user = None
                    logger.info("Subscribed user left, disconnecting")
                    await self.channel.disconnect()

            self.channel.on("user_left", on_user_left)

            disconnected_future = asyncio.Future[None]()

            def callback(agora_rtc_conn: RTCConnection, conn_info: RTCConnInfo, reason):
                logger.info(f"Connection state changed: {conn_info.state}")
                if conn_info.state == 1:
                    if not disconnected_future.done():
                        disconnected_future.set_result(None)

            self.channel.on("connection_state_changed", callback)

            asyncio.create_task(self.rtc_to_model()).add_done_callback(log_exception)
            asyncio.create_task(self.model_to_rtc()).add_done_callback(log_exception)

            asyncio.create_task(self._process_model_messages()).add_done_callback(
                log_exception
            )

            await disconnected_future
            logger.info("Agent finished running")
        except asyncio.CancelledError:
            logger.info("Agent cancelled")
        except Exception as e:
            logger.error(f"Error running agent: {e}")
            raise

    async def rtc_to_model(self) -> None:
        while self.subscribe_user is None or self.channel.get_audio_frames(self.subscribe_user) is None:
            logger.info("Waiting for audio frames")
            await asyncio.sleep(0.1)

        audio_frames = self.channel.get_audio_frames(self.subscribe_user)

        # Initialize PCMWriter for receiving audio
        pcm_writer = PCMWriter(prefix="rtc_to_model", write_pcm=self.write_pcm)

        try:
            async for audio_frame in audio_frames:
                # logger.info(f"Received audio frame")
                # Process received audio (send to model)
                if(not self.is_agent_busy):
                    _monitor_queue_size(self.audio_queue, "audio_queue")
                    while self.input_audio_queue.qsize() > 0: # MARK: AUD OVRLP ISSUE CHK
                        await self.connection.send_audio_data(self.input_audio_queue.get_nowait())
                    await self.connection.send_audio_data(audio_frame.data)
                else:
                    self.input_audio_queue.put_nowait((audio_frame.data)) # MARK: AUD OVRLP ISSUE CHK

                # Write PCM data if enabled
                # await pcm_writer.write(audio_frame.data)

                await asyncio.sleep(0)  # Yield control to allow other tasks to run

        except asyncio.CancelledError:
            # Write any remaining PCM data before exiting
            await pcm_writer.flush()
            raise  # Re-raise the exception to propagate cancellation

    async def model_to_rtc(self) -> None:
        # Initialize PCMWriter for sending audio
        pcm_writer = PCMWriter(prefix="model_to_rtc", write_pcm=self.write_pcm)

        try:
            while True:
                # Get audio frame from the model output
                frame = await self.audio_queue.get()

                # Process sending audio (to RTC)
                await self.channel.push_audio_frame(frame)

                # Write PCM data if enabled
                await pcm_writer.write(frame)

        except asyncio.CancelledError:
            # Write any remaining PCM data before exiting
            await pcm_writer.flush()
            raise  # Re-raise the cancelled exception to properly exit the task

    async def _process_model_messages(self) -> None:
        async for message in self.connection.listen():
            # logger.info(f"Received message {message=}")
            match message:
                case ResponseAudioDelta():
                    # logger.info("Received audio message")
                    self.audio_queue.put_nowait(base64.b64decode(message.delta))
                    # loop.call_soon_threadsafe(self.audio_queue.put_nowait, base64.b64decode(message.delta))
                    logger.info(f"TMS:ResponseAudioDelta: response_id:{message.response_id},item_id: {message.item_id}")
                case ResponseAudioTranscriptDelta():
                    logger.info(f"TMS:ResponseTranscriptDelta {message.delta}")
                    # asyncio.create_task(self.channel.chat.send_message(
                    #     ChatMessage(
                    #         message=to_json(message), msg_id=message.item_id
                    #     )
                    # ))

                case ResponseAudioTranscriptDone():
                    # so only when the transcription is done, we should send the current Input audio buffer
                    logger.info(f"Text message done: {message=}")
                    asyncio.create_task(self.channel.chat.send_message(
                        ChatMessage(
                            message=to_json(message), msg_id=message.item_id
                        )
                    ))
                case InputAudioBufferSpeechStarted():
                    logger.info(f"TMS:InputAudioBufferSpeechStarted: item_id: {message.item_id}")
                case InputAudioBufferSpeechStopped():
                    self.is_agent_busy = True
                    logger.info(f"TMS:InputAudioBufferSpeechStopped: item_id: {message.item_id}")
                    pass
                case ItemInputAudioTranscriptionCompleted():
                    logger.info(f"ItemInputAudioTranscriptionCompleted: {message=}")
                    asyncio.create_task(self.channel.chat.send_message(
                        ChatMessage(
                            message=to_json(message), msg_id=message.item_id
                        )
                    ))
                #  InputAudioBufferCommitted
                case InputAudioBufferCommitted():
                    pass
                case ItemCreated():
                    pass
                # ResponseCreated
                case ResponseCreated():
                    pass
                # ResponseDone
                case ResponseDone():
                    self.is_agent_busy = False
                    pass

                # ResponseOutputItemAdded
                case ResponseOutputItemAdded():
                    pass

                # ResponseContenPartAdded
                case ResponseContentPartAdded():
                    pass
                # ResponseAudioDone
                case ResponseAudioDone():
                    pass
                # ResponseContentPartDone
                case ResponseContentPartDone():
                    pass
                # ResponseOutputItemDone
                case ResponseOutputItemDone():
                    pass
                case SessionUpdated():
                    pass
                case RateLimitsUpdated():
                    pass
                case _:
                    logger.warning(f"Unhandled message {message=}")
