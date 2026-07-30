from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Awaitable

class STTAdapter(ABC):
    @abstractmethod
    async def start_listening(
        self, 
        on_interim: Callable[[str], Awaitable[None]], 
        on_final: Callable[[str], Awaitable[None]]
    ) -> None:
        """Starts streaming audio from customer and triggers callbacks with transcripts."""
        pass

    @abstractmethod
    async def stop_listening(self) -> None:
        """Stops listening and closes connection."""
        pass


class TTSAdapter(ABC):
    @abstractmethod
    async def speak(self, text_stream: AsyncIterator[str]) -> None:
        """Streams text chunks to TTS and plays the generated audio chunks."""
        pass

    @abstractmethod
    async def stop_speaking(self) -> None:
        """Cancels any active audio output immediately (for barge-in)."""
        pass
