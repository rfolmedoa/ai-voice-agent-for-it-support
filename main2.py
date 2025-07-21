import asyncio
import base64
import json
import os
import websockets
import requests
from dotenv import load_dotenv

# === LOAD ENV VARIABLES ===
load_dotenv()
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# === SETUP OPENAI CLIENT ===
import openai
openai_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = "You are a helpful assistant. Keep your responses short and clear."

def deepgram_connect():
    headers = {
        'Authorization': f"Token {DEEPGRAM_API_KEY}"
    }
    # Only single-channel needed for inbound audio
    return websockets.connect(
        'wss://api.deepgram.com/v1/listen?encoding=mulaw&sample_rate=8000',
        extra_headers=headers
    )

async def chatgpt_response(prompt):
    response = await openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        max_tokens=100
    )
    return response.choices[0].message.content.strip()

def deepgram_tts(text, api_key):
    url = "https://api.deepgram.com/v1/speak?model=aura-2-thalia-en&encoding=mulaw&sample_rate=8000"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json"
    }
    payload = {"text": text}
    response = requests.post(url, headers=headers, json=payload, stream=True)
    if response.status_code != 200:
        raise Exception(f"Deepgram TTS error: {response.text}")
    return b''.join(response.iter_content(chunk_size=1024))

async def send_audio_to_twilio(ws, audio_bytes, stream_sid):
    chunk_size = 320  # 20ms of audio at 8kHz
    for i in range(0, len(audio_bytes), chunk_size):
        chunk = audio_bytes[i:i+chunk_size]
        payload = base64.b64encode(chunk).decode()
        message = {
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": payload}
        }
        await ws.send(json.dumps(message))
        await asyncio.sleep(0.02)  # Simulate real-time (20ms)

async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    stream_sid = None

    async with deepgram_connect() as deepgram_ws:
        # Start sender: Twilio -> Deepgram (audio stream)
        async def deepgram_sender():
            print('Deepgram sender started')
            while True:
                chunk = await audio_queue.get()
                if chunk == b'':
                    break
                await deepgram_ws.send(chunk)

        # Start receiver: Deepgram -> ChatGPT -> Deepgram TTS -> Twilio (audio stream)
        async def deepgram_receiver():
            nonlocal stream_sid
            print('Deepgram receiver started')
            async for message in deepgram_ws:
                try:
                    resp = json.loads(message)
                    transcript = resp.get('channel', {}).get('alternatives', [{}])[0].get('transcript', "")
                    if transcript:
                        print(f"User said: {transcript}")

                        # Get ChatGPT response
                        chatgpt_reply = await chatgpt_response(transcript)
                        print(f"Assistant: {chatgpt_reply}")

                        # Synthesize reply audio with Deepgram TTS
                        audio_bytes = deepgram_tts(chatgpt_reply, DEEPGRAM_API_KEY)

                        # Send audio to Twilio
                        if stream_sid:
                            await send_audio_to_twilio(twilio_ws, audio_bytes, stream_sid)
                except Exception as e:
                    print(f"Error decoding Deepgram message: {e}")

        # Receive from Twilio
        async def twilio_receiver():
            nonlocal stream_sid
            async for message in twilio_ws:
                try:
                    msg = json.loads(message)
                    if msg.get("event") == "start":
                        stream_sid = msg["streamSid"]
                        print(f"Stream started: {stream_sid}")
                    elif msg.get("event") == "media":
                        payload = msg["media"]["payload"]
                        chunk = base64.b64decode(payload)
                        await audio_queue.put(chunk)
                    elif msg.get("event") == "stop":
                        print("Stream stopped")
                        await audio_queue.put(b'')
                        break
                except Exception as e:
                    print(f"Error decoding Twilio message: {e}")

        # Run all three tasks together
        await asyncio.gather(
            deepgram_sender(),
            deepgram_receiver(),
            twilio_receiver()
        )

# === MAIN WEBSOCKET SERVER ===
async def main():
    print("WebSocket server running on port 8765")

    async def handler(websocket, path):
        print("Twilio client connected")
        await twilio_handler(websocket)

    server = await websockets.serve(handler, '0.0.0.0', 8765)
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
