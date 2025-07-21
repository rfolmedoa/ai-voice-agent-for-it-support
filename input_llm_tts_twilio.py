import asyncio
import base64
import json
import sys
import websockets
import ssl
import requests
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Load API keys
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai = OpenAI(api_key=OPENAI_API_KEY)

async def get_chatgpt_response(prompt: str) -> str:
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant. You provide only short responses."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("OpenAI error:", e)
        return "I'm sorry, I encountered an error."

async def send_tts_to_twilio(text: str, streamsid: str, twilio_ws):
    url = "https://api.deepgram.com/v1/speak?model=aura-asteria-en&encoding=mulaw&sample_rate=8000&container=none"
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"text": text}
    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        raw_mulaw = response.content
        media_message = {
            "event": "media",
            "streamSid": streamsid,
            "media": {"payload": base64.b64encode(raw_mulaw).decode("ascii")},
        }
        await twilio_ws.send(json.dumps(media_message))
    else:
        print("Deepgram TTS failed:", response.text)

async def twilio_handler(twilio_ws):
    streamsid_queue = asyncio.Queue()

    async def twilio_receiver():
        async for message in twilio_ws:
            try:
                data = json.loads(message)
                if data["event"] == "start":
                    streamsid_queue.put_nowait(data["start"]["streamSid"])
            except Exception as e:
                print("Receiver error:", e)
                break

    async def twilio_sender():
        streamsid = await streamsid_queue.get()

        while True:
            user_input = input("You: ")
            if user_input.lower() in {"exit", "quit"}:
                break

            llm_response = await get_chatgpt_response(user_input)
            print("ChatGPT:", llm_response)
            await send_tts_to_twilio(llm_response, streamsid, twilio_ws)

        await twilio_ws.close()

    await asyncio.gather(twilio_receiver(), twilio_sender())

async def router(websocket, path):
    if path == "/twilio":
        print("Twilio connection received")
        await twilio_handler(websocket)

def main():
    server = websockets.serve(router, "localhost", 5000)
    asyncio.get_event_loop().run_until_complete(server)
    asyncio.get_event_loop().run_forever()

if __name__ == "__main__":
    sys.exit(main() or 0)
