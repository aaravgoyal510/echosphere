import os
import asyncio
import logging
from typing import AsyncIterator, Callable, Optional
from piper.voice import PiperVoice
from pipeline.base import TTSAdapter

logger = logging.getLogger(__name__)

class PiperTTSAdapter(TTSAdapter):
    """
    Real TTS Adapter utilizing local Piper Voice ONNX model.
    Synthesizes raw PCM audio bytes (16kHz, 16-bit mono) from text streams.
    Supports instant task cancellation to achieve sub-250ms barge-in stop latency.
    """
    def __init__(self, on_audio_chunk: Optional[Callable[[bytes], None]] = None):
        self.on_audio_chunk = on_audio_chunk
        self.active_speak_task: Optional[asyncio.Task] = None
        self.speaking = False
        self.interrupted = False
        self.spoken_text = []
        
        self.voice: Optional[PiperVoice] = None
        
        # Paths to local ONNX model
        self.model_dir = os.path.join(os.getcwd(), ".piper")
        self.onnx_path = os.path.join(self.model_dir, "en_US-amy-low.onnx")
        self.json_path = os.path.join(self.model_dir, "en_US-amy-low.onnx.json")

    async def initialize(self) -> None:
        """Loads the Piper voice model asynchronously."""
        if not self.voice:
            if not os.path.exists(self.onnx_path) or not os.path.exists(self.json_path):
                raise FileNotFoundError(
                    f"Piper voice model not found at {self.onnx_path}. "
                    "Please run tests/latency_spike.py first to download the voice assets."
                )
            
            logger.info("Loading local Piper voice model...")
            self.voice = await asyncio.to_thread(
                PiperVoice.load, self.onnx_path, config_path=self.json_path
            )
            logger.info("Piper voice model loaded successfully.")

    async def speak(self, text_stream: AsyncIterator[str]) -> None:
        """
        Consumes text stream chunks, synthesizes audio segments,
        and streams PCM bytes to output. Can be cancelled mid-sentence.
        """
        await self.initialize()
        
        self.speaking = True
        self.interrupted = False
        self.spoken_text = []
        
        self.active_speak_task = asyncio.create_task(self._consume_stream(text_stream))
        try:
            await self.active_speak_task
        except asyncio.CancelledError:
            self.interrupted = True
            logger.info("[Piper TTS] Synthesized speech stream cancelled due to barge-in.")
            # Trigger downstream interruption signal
            if self.on_audio_chunk:
                self.on_audio_chunk(b"[TTS_STOPPED_MID_SENTENCE]")
        finally:
            self.speaking = False
            self.active_speak_task = None

    async def _consume_stream(self, text_stream: AsyncIterator[str]) -> None:
        async for chunk in text_stream:
            # Track spoken tokens for history truncation calculations
            self.spoken_text.append(chunk)
            
            # Synthesize text chunk to raw PCM bytes in background thread
            # Piper returns a generator yielding raw PCM bytes
            audio_generator = await asyncio.to_thread(self.voice.synthesize, chunk)
            
            # Consume the audio generator chunks
            for audio_bytes in audio_generator:
                if self.on_audio_chunk:
                    self.on_audio_chunk(audio_bytes)
                
                # Check for cancellation between chunks to yield to event loop
                await asyncio.sleep(0.001)

    async def stop_speaking(self) -> None:
        """Instantly cancels the active Piper speech synthesis task."""
        if self.active_speak_task and not self.active_speak_task.done():
            self.active_speak_task.cancel()
            try:
                await self.active_speak_task
            except asyncio.CancelledError:
                pass
