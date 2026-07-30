import os
import time
import math
import wave
import struct
import urllib.request
import logging
import httpx
from faster_whisper import WhisperModel
# pyrefly: ignore [missing-import]
from piper.voice import PiperVoice
from dotenv import load_dotenv

# Load environment variables (e.g. OLLAMA_API_KEY)
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
MODEL_DIR = os.path.join(os.getcwd(), ".piper")
WAV_PATH = "dummy_test.wav"
ONNX_PATH = os.path.join(MODEL_DIR, "en_US-amy-low.onnx")
JSON_PATH = os.path.join(MODEL_DIR, "en_US-amy-low.onnx.json")
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma4:31b-cloud"


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

def query_ollama_extraction(text: str) -> float:
    """Sends a fast extraction query to the local Ollama instance and returns elapsed time."""
    system_prompt = (
        "You are a sales qualification extractor. Analyze the input and output JSON. "
        "Fields: 'intent' (pricing, competitor, calendar, other), 'team_size' (integer or null)."
    )
    user_prompt = f"Extract from: '{text}'"
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "format": "json",
        "stream": False,
        "options": {
            "num_predict": 256,  # Keep output very short for fast extraction
            "temperature": 0.0,  # Deterministic
            "num_ctx": 2048
        }
    }
    
    start_time = time.perf_counter()
    try:
        response = httpx.post(OLLAMA_URL, json=payload, timeout=10.0)
        latency = time.perf_counter() - start_time
        if response.status_code == 200:
            res_data = response.json()
            logger.info(f"Ollama Full Response: {res_data}")
            logger.info(f"Ollama JSON Output: {res_data.get('message', {}).get('content', '').strip()}")
        else:
            logger.warning(f"Ollama failed with status: {response.status_code} body: {response.text}. Gracefully skipping extraction.")
            latency = 0.0
    except Exception as e:
        logger.warning(f"Ollama extraction call failed or timed out: {e}. Gracefully skipping extraction.")
        latency = 0.0
        
    return latency


def run_latency_spike():
    logger.info("Starting end-to-end Latency Spike (Whisper + Ollama + Piper)...")
    generate_dummy_wav(WAV_PATH)
    
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
    
    # 3. Warmup Ollama
    logger.info(f"Warming up Ollama model '{OLLAMA_MODEL}'...")
    query_ollama_extraction("hello")
    
    # 4. Profile STT (Whisper)
    logger.info("Profiling STT (Whisper)...")
    start_stt = time.perf_counter()
    segments, info = stt_model.transcribe(WAV_PATH, beam_size=1)
    transcribed_text = " ".join([seg.text for seg in segments])
    stt_latency = time.perf_counter() - start_stt
    logger.info(f"STT Output: '{transcribed_text}' (Inference: {stt_latency*1000:.1f}ms)")
    
    # 5. Profile Ollama Extraction
    logger.info("Profiling local Ollama Extraction...")
    # Simulate a realistic customer qualification statement
    test_phrase = "Can you tell me how much it costs for about fifty people?"
    ollama_latency = query_ollama_extraction(test_phrase)
    logger.info(f"Ollama Extraction completed in {ollama_latency*1000:.1f}ms.")
    
    # 6. Profile TTS (Piper)
    logger.info("Profiling TTS (Piper)...")
    agent_response = "Pricing starts at fifty dollars per seat for the enterprise tier."
    start_tts = time.perf_counter()
    audio_stream = tts_model.synthesize(agent_response)
    # Time to first chunk
    next(audio_stream)
    tts_first_chunk = time.perf_counter() - start_tts
    
    # Consume rest
    for _ in audio_stream:
        pass
    tts_total = time.perf_counter() - start_tts
    logger.info(f"TTS First Chunk: {tts_first_chunk*1000:.1f}ms, Total: {tts_total*1000:.1f}ms")
    
    # 7. Summary
    estimated_roundtrip = stt_latency + ollama_latency + tts_first_chunk
    logger.info("=========================================")
    logger.info("LATENCY SPIKE PROFILE SUMMARY WITH OLLAMA")
    logger.info("=========================================")
    logger.info(f"STT Inference Time  : {stt_latency*1000:.1f}ms")
    logger.info(f"Ollama Extraction   : {ollama_latency*1000:.1f}ms")
    logger.info(f"TTS First Chunk     : {tts_first_chunk*1000:.1f}ms")
    logger.info(f"TTS Total Synthesis : {tts_total*1000:.1f}ms")
    logger.info(f"Estimated End-to-End: {estimated_roundtrip*1000:.1f}ms (excluding dialogue LLM)")
    logger.info("=========================================")
    
    # Clean WAV
    if os.path.exists(WAV_PATH):
        os.remove(WAV_PATH)

if __name__ == "__main__":
    run_latency_spike()
