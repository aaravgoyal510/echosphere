import os
import time
import math
import wave
import struct
import logging
from faster_whisper import WhisperModel
from piper.voice import PiperVoice
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
MODEL_DIR = os.path.join(os.getcwd(), ".piper")
WAV_PATH = "dummy_test.wav"
ONNX_PATH = os.path.join(MODEL_DIR, "en_US-amy-low.onnx")
JSON_PATH = os.path.join(MODEL_DIR, "en_US-amy-low.onnx.json")

def generate_dummy_wav(filepath: str, duration_sec: float = 1.0, freq: float = 440.0):
    """Generate a simple mono WAV file (16kHz, 16-bit PCM) for STT testing."""
    sample_rate = 16000
    num_samples = int(duration_sec * sample_rate)
    
    with wave.open(filepath, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        for i in range(num_samples):
            val = int(32767 * math.sin(2.0 * math.pi * freq * i / sample_rate))
            data = struct.pack("<h", val)
            wav_file.writeframesraw(data)

def query_anthropic_extraction(client: Anthropic, text: str) -> float:
    """Sends a fast extraction query to Anthropic Claude 3.5 Haiku and returns elapsed time."""
    system_prompt = (
        "You are a sales qualification extractor. Analyze the input and output JSON. "
        "Fields: 'intent' (pricing, competitor, calendar, other), 'team_size' (integer or null). "
        "Strictly output only raw valid JSON without markdown wrapping or comments."
    )
    user_prompt = f"Extract from: '{text}'"
    
    start_time = time.perf_counter()
    try:
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=64,
            temperature=0.0,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )
        content_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                content_text += block.text
        logger.info(f"Claude JSON Output: {content_text.strip()}")
        latency = time.perf_counter() - start_time
        return latency
    except Exception as e:
        logger.error(f"Claude API failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 0.0

def run_latency_spike():
    logger.info("Starting end-to-end Latency Spike (Whisper + Claude Haiku + Piper)...")
    generate_dummy_wav(WAV_PATH)
    
    # Init Anthropic client
    client = Anthropic()
    
    # 1. Load Whisper
    logger.info("Loading Whisper 'base.en'...")
    stt_model = WhisperModel("base.en", device="cpu", compute_type="int8")
    
    # Warmup STT
    list(stt_model.transcribe(WAV_PATH, beam_size=1)[0])
    
    # 2. Load Piper
    logger.info("Loading Piper TTS...")
    tts_model = PiperVoice.load(ONNX_PATH, config_path=JSON_PATH)
    
    # Warmup TTS
    next(tts_model.synthesize("Warmup"))
    
    # 3. Warmup/Test Anthropic
    logger.info("Warming up/Testing Anthropic Claude Haiku...")
    query_anthropic_extraction(client, "hello")
    
    # 4. Profile STT (Whisper)
    logger.info("Profiling STT (Whisper)...")
    start_stt = time.perf_counter()
    segments, info = stt_model.transcribe(WAV_PATH, beam_size=1)
    transcribed_text = " ".join([seg.text for seg in segments])
    stt_latency = time.perf_counter() - start_stt
    logger.info(f"STT Output: '{transcribed_text}' (Inference: {stt_latency*1000:.1f}ms)")
    
    # 5. Profile Claude Extraction
    logger.info("Profiling Claude Extraction...")
    test_phrase = "Can you tell me how much it costs for about fifty people?"
    claude_latency = query_anthropic_extraction(client, test_phrase)
    logger.info(f"Claude Extraction completed in {claude_latency*1000:.1f}ms.")
    
    # 6. Profile TTS (Piper)
    logger.info("Profiling TTS (Piper)...")
    agent_response = "Pricing starts at fifty dollars per seat for the enterprise tier."
    start_tts = time.perf_counter()
    audio_stream = tts_model.synthesize(agent_response)
    next(audio_stream)
    tts_first_chunk = time.perf_counter() - start_tts
    
    for _ in audio_stream:
        pass
    tts_total = time.perf_counter() - start_tts
    logger.info(f"TTS First Chunk: {tts_first_chunk*1000:.1f}ms, Total: {tts_total*1000:.1f}ms")
    
    # 7. Summary
    estimated_roundtrip = stt_latency + claude_latency + tts_first_chunk
    logger.info("=========================================")
    logger.info("LATENCY SPIKE PROFILE SUMMARY WITH CLAUDE")
    logger.info("=========================================")
    logger.info(f"STT Inference Time  : {stt_latency*1000:.1f}ms")
    logger.info(f"Claude Extraction   : {claude_latency*1000:.1f}ms")
    logger.info(f"TTS First Chunk     : {tts_first_chunk*1000:.1f}ms")
    logger.info(f"TTS Total Synthesis : {tts_total*1000:.1f}ms")
    logger.info(f"Estimated End-to-End: {estimated_roundtrip*1000:.1f}ms (excluding dialogue LLM)")
    logger.info("=========================================")
    
    # Clean WAV
    if os.path.exists(WAV_PATH):
        os.remove(WAV_PATH)

if __name__ == "__main__":
    run_latency_spike()
