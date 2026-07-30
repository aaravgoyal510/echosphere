import asyncio
import time
from typing import AsyncIterator, Callable, Awaitable, List, Optional
from pipeline.base import STTAdapter, TTSAdapter

class SimulatedSTTAdapter(STTAdapter):
    def __init__(self):
        self.on_interim: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_final: Optional[Callable[[str], Awaitable[None]]] = None
        self.listening = False

    async def start_listening(
        self, 
        on_interim: Callable[[str], Awaitable[None]], 
        on_final: Callable[[str], Awaitable[None]]
    ) -> None:
        self.on_interim = on_interim
        self.on_final = on_final
        self.listening = True

    async def stop_listening(self) -> None:
        self.listening = False

    async def simulate_customer_speech(self, text: str, word_delay: float = 0.05) -> None:
        """Simulate customer speaking word-by-word to test live VAD and interim transcription."""
        if not self.listening or not self.on_interim or not self.on_final:
            return

        words = text.split()
        accumulated = []
        for i, word in enumerate(words):
            accumulated.append(word)
            interim_text = " ".join(accumulated)
            
            # Fire interim callback
            res_interim = self.on_interim(interim_text)
            if asyncio.iscoroutine(res_interim):
                await res_interim
            await asyncio.sleep(word_delay)
            
        # Fire final callback once completed
        res_final = self.on_final(text)
        if asyncio.iscoroutine(res_final):
            await res_final


class SimulatedTTSAdapter(TTSAdapter):
    def __init__(self, on_audio_chunk: Optional[Callable[[str], None]] = None):
        self.on_audio_chunk = on_audio_chunk
        self.active_speak_task: Optional[asyncio.Task] = None
        self.speaking = False
        self.interrupted = False
        self.spoken_text = []

    async def speak(self, text_stream: AsyncIterator[str]) -> None:
        self.speaking = True
        self.interrupted = False
        self.spoken_text = []
        
        # Wrap the stream consumer in an asyncio task so we can cancel it instantly on barge-in
        self.active_speak_task = asyncio.create_task(self._consume_stream(text_stream))
        try:
            await self.active_speak_task
        except asyncio.CancelledError:
            self.interrupted = True
            # Simulate immediate stop (< 200ms)
            if self.on_audio_chunk:
                self.on_audio_chunk("[TTS_STOPPED_MID_SENTENCE]")
        finally:
            self.speaking = False
            self.active_speak_task = None

    async def _consume_stream(self, text_stream: AsyncIterator[str]) -> None:
        async for chunk in text_stream:
            self.spoken_text.append(chunk)
            if self.on_audio_chunk:
                self.on_audio_chunk(chunk)
            # Simulate natural speech latency (e.g. 30ms per character or small sleep per chunk)
            await asyncio.sleep(0.02 * len(chunk))

    async def stop_speaking(self) -> None:
        if self.active_speak_task and not self.active_speak_task.done():
            self.active_speak_task.cancel()
            # Wait for cancellation to complete
            try:
                await self.active_speak_task
            except asyncio.CancelledError:
                pass


class TurnTakingManager:
    def __init__(
        self, 
        stt: SimulatedSTTAdapter, 
        tts: SimulatedTTSAdapter,
        backchannel_whitelist: List[str] = None
    ):
        self.stt = stt
        self.tts = tts
        self.backchannel_whitelist = backchannel_whitelist or ["mm-hmm", "yeah", "okay", "right", "got it"]
        self.on_interruption_handler: Optional[Callable[[], Awaitable[None]]] = None

    def register_interruption_handler(self, handler: Callable[[], Awaitable[None]]) -> None:
        self.on_interruption_handler = handler

    async def handle_customer_interim_transcript(self, text: str) -> None:
        """
        Processes incoming customer speech. Checks if customer is interrupting the agent,
        applies backchannel filtering, and handles turn-taking signals.
        """
        # If the agent is not currently speaking, there is no barge-in to trigger.
        if not self.tts.speaking:
            return

        # Check if the interim text is a backchannel (short phrase in whitelist)
        cleaned_text = text.strip().lower().rstrip(".,?!")
        if cleaned_text in self.backchannel_whitelist:
            # Backchannel detected: do not interrupt the agent.
            return

        # Check if length is greater than ~2 words (real interruption)
        words = text.split()
        if len(words) >= 1:
            # Genuine interruption detected.
            # 1. Stop the agent speaking immediately.
            start_stop_time = time.perf_counter()
            await self.tts.stop_speaking()
            stop_latency_ms = (time.perf_counter() - start_stop_time) * 1000
            
            # 2. Trigger interruption handler (which cancels in-flight LLM generation)
            if self.on_interruption_handler:
                await self.on_interruption_handler()
                
            print(f"[TurnTakingManager] Interrupted agent speech. Stop latency: {stop_latency_ms:.2f}ms")
