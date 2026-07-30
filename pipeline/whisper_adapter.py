import asyncio
import logging
import numpy as np
from typing import Callable, Awaitable, Optional
from faster_whisper import WhisperModel
from pipeline.base import STTAdapter

logger = logging.getLogger(__name__)

class WhisperSTTAdapter(STTAdapter):
    """
    Real STT Adapter utilizing faster-whisper on CPU.
    Accepts raw 16kHz 16-bit mono PCM bytes, accumulates them into segments, 
    and transcribes them using the local base.en Whisper model in a background thread.
    """
    def __init__(self, model_size: str = "base.en", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        
        self.on_interim: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_final: Optional[Callable[[str], Awaitable[None]]] = None
        self.listening = False
        
        self.audio_buffer = bytearray()
        self.model: Optional[WhisperModel] = None
        self.lock = asyncio.Lock()

    async def start_listening(
        self, 
        on_interim: Callable[[str], Awaitable[None]], 
        on_final: Callable[[str], Awaitable[None]]
    ) -> None:
        """Initializes the Whisper model (first load) and registers callbacks."""
        self.on_interim = on_interim
        self.on_final = on_final
        self.listening = True
        
        if not self.model:
            logger.info(f"Loading local Whisper model '{self.model_size}' on {self.device}...")
            # Load in executor to prevent block during call setup
            self.model = await asyncio.to_thread(
                WhisperModel, self.model_size, device=self.device, compute_type=self.compute_type
            )
            logger.info("Whisper model loaded successfully.")

    async def stop_listening(self) -> None:
        self.listening = False
        self.audio_buffer.clear()

    async def receive_audio_chunk(self, chunk: bytes) -> None:
        """
        Receives raw PCM audio chunks (16kHz, 16-bit mono).
        Accumulates chunks and runs transcription when a complete segment is reached.
        """
        if not self.listening or not self.model:
            return

        async with self.lock:
            self.audio_buffer.extend(chunk)
            
            # Simple threshold VAD segmentation: if buffer exceeds ~1.5s of audio (48,000 bytes)
            # we run a transcription turn. 16000 samples/sec * 2 bytes/sample = 32000 bytes/sec.
            if len(self.audio_buffer) >= 48000:
                segment_bytes = bytes(self.audio_buffer)
                self.audio_buffer.clear()
                
                # Run transcription in a background thread
                asyncio.create_task(self._process_transcription(segment_bytes))

    async def _process_transcription(self, raw_audio: bytes) -> None:
        """Converts raw PCM to float32 normalized array and runs inference."""
        try:
            # Convert 16-bit PCM bytes to float32 numpy array
            audio_np = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0
            
            # Run transcription in a background thread to keep event loop responsive
            segments, info = await asyncio.to_thread(
                self.model.transcribe,
                audio_np,
                beam_size=3,
                language="en",
                vad_filter=True  # Use Whisper's internal VAD filter to drop noise
            )
            
            # Extract text from segments
            transcribed_text = " ".join([seg.text for seg in segments]).strip()
            
            if transcribed_text:
                logger.info(f"[Whisper STT] Final Transcript: '{transcribed_text}'")
                
                # Trigger callbacks
                if self.on_interim:
                    res = self.on_interim(transcribed_text)
                    if asyncio.iscoroutine(res):
                        await res
                        
                if self.on_final:
                    res = self.on_final(transcribed_text)
                    if asyncio.iscoroutine(res):
                        await res
                        
        except Exception as e:
            logger.error(f"Error during Whisper transcription: {e}")
