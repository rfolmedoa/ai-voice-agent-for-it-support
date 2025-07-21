import asyncio
import base64
import json
import websockets
from llm_tts import speak_deepgram  # make sure this returns 8kHz mono µ-law audio
from dotenv import load_dotenv
import os
import openai

load_dotenv()

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# OpenAI client
client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

# Prompt for ChatGPT
SYSTEM_PROMPT = "You are a helpful AI. Provide short and clear responses."

# Create a connection to Deepgram’s real-time transcription WebSocket
def deepgram_connect():
    return websockets.connect(
        "wss://api.deepgram.com/v1/listen?encoding=mulaw&sample_rate=8000&channels=1",
        extra_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    )

# Send prompt to ChatGPT and return response text
async def chatgpt_response(prompt: str) -> str:
    response = await client.chat.completions.create(
        model="gpt-3.5-turbo",  # or "gpt-4o"
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()

# Send LLM-generated audio to Twilio over WebSocket in µ-law format
async def send_audio_to_twilio(twilio_ws, audio_bytes: bytes):
    CHUNK_SIZE = 320  # 20ms at 8000 Hz
    for i in range(0, len(audio_bytes), CHUNK_SIZE):
        chunk = audio_bytes[i:i+CHUNK_SIZE]
        await twilio_ws.send(json.dumps({
            "event": "media",
            "media": {
                "payload": base64.b64encode(chunk).decode("utf-8")
            }
        }))
        await asyncio.sleep(0.02)  # simulate real-time pacing

# Main Twilio handler
async def twilio_handler(twilio_ws):
    print("Twilio client connected")
    audio_queue = asyncio.Queue()

    async with deepgram_connect() as deepgram_ws:
        print("Connected to Deepgram")

        # Deepgram sender: send Twilio audio to Deepgram
        async def deepgram_sender():
            while True:
                chunk = await audio_queue.get()
                if chunk == b"":
                    break
                await deepgram_ws.send(chunk)

        # Deepgram receiver: get transcript and respond via TTS
        async def deepgram_receiver():
            async for message in deepgram_ws:
                try:
                    data = json.loads(message)
                    transcript = data["channel"]["alternatives"][0]["transcript"]
                    is_final = data["is_final"]
                    if transcript:
                        print("User said:", transcript)
                        if is_final:
                            # Send to ChatGPT
                            llm_text = await chatgpt_response(transcript)
                            print("LLM:", llm_text)
                            # Generate TTS audio (8kHz mono µ-law)
                            audio_bytes = await asyncio.to_thread(speak_deepgram, llm_text)
                            # Send audio to Twilio
                            await send_audio_to_twilio(twilio_ws, audio_bytes)
                except Exception as e:
                    print("Deepgram decode error:", e)

        # Twilio WebSocket receiver
        async def twilio_receiver():
            async for msg in twilio_ws:
                data = json.loads(msg)
                event = data.get("event")

                if event == "start":
                    call_sid = data["start"]["callSid"]
                    print("Call started:", call_sid)

                elif event == "media":
                    audio = base64.b64decode(data["media"]["payload"])
                    await audio_queue.put(audio)

                elif event == "stop":
                    print("Call ended.")
                    await audio_queue.put(b"")  # stop Deepgram sender
                    break

        await asyncio.gather(
            deepgram_sender(),
            deepgram_receiver(),
            twilio_receiver()
        )

async def main():
    print("Starting Twilio AI voice server...")
    server = await websockets.serve(twilio_handler, "0.0.0.0", 8765)
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
