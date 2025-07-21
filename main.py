import asyncio
import base64
import json
import sys
import websockets
from pydub import AudioSegment
from dotenv import load_dotenv
import os
import openai
from llm_tts import speak_deepgram 

load_dotenv()

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

subscribers = {}
SYSTEM_PROMPT = "Provide short responses"

def deepgram_connect():
    headers = {'Authorization': f"Token {DEEPGRAM_API_KEY}"}
    return websockets.connect(
        'wss://api.deepgram.com/v1/listen?encoding=mulaw&sample_rate=8000&channels=1',
        extra_headers=headers
    )

async def chatgpt_response(prompt):
    response = await client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
    )
    return response.choices[0].message.content

async def twilio_handler(twilio_ws):
    import base64

    audio_queue = asyncio.Queue()
    callsid_queue = asyncio.Queue()

    async def send_audio_to_twilio(twilio_ws, audio_bytes):
        b64_audio = base64.b64encode(audio_bytes).decode('utf-8')
        message = json.dumps({
            "event": "media",
            "media": {
                "payload": b64_audio
            }
        })
        await twilio_ws.send(message)

    async with deepgram_connect() as deepgram_ws:

        async def deepgram_sender():
            while True:
                chunk = await audio_queue.get()
                if chunk == b'':
                    break
                await deepgram_ws.send(chunk)

        async def deepgram_receiver():
            callsid = await callsid_queue.get()
            subscribers[callsid] = []
            async for message in deepgram_ws:
                try:
                    data = json.loads(message)
                    transcript = data.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                    is_final = data.get("is_final", False)

                    if transcript.strip() and is_final:
                        print(f"[{callsid}] {transcript}")
                        response = await chatgpt_response(transcript)
                        print(f"[ChatGPT] {response}")

                        if any(kw in transcript.lower() for kw in ["goodbye", "bye", "see you"]):
                            print("Detected goodbye. Exiting...")
                            goodbye_audio = await asyncio.to_thread(speak_deepgram, "Goodbye!")
                            await send_audio_to_twilio(twilio_ws, goodbye_audio)
                            await asyncio.sleep(2)
                            sys.exit(0)

                        # Generate audio from LLM response and send it to Twilio
                        response_audio = await asyncio.to_thread(speak_deepgram, response)
                        await send_audio_to_twilio(twilio_ws, response_audio)

                except Exception as e:
                    print(f"[Deepgram] Error: {e}")

        async def twilio_receiver():
            BUFFER_SIZE = 160 * 20  # 20ms of mulaw at 8kHz, 1 byte/sample
            buffer = bytearray()
            callsid = None

            async for message in twilio_ws:
                try:
                    data = json.loads(message)

                    if data['event'] == 'start':
                        callsid = data['start']['callSid']
                        callsid_queue.put_nowait(callsid)

                    elif data['event'] == 'media' and data['media']['track'] == 'inbound':
                        chunk = base64.b64decode(data['media']['payload'])
                        buffer.extend(chunk)

                        while len(buffer) >= BUFFER_SIZE:
                            audio_chunk = buffer[:BUFFER_SIZE]
                            audio_queue.put_nowait(audio_chunk)
                            buffer = buffer[BUFFER_SIZE:]

                    elif data['event'] == 'stop':
                        break

                except Exception as e:
                    print(f"[Twilio] Error: {e}")
                    break

            audio_queue.put_nowait(b'')

        await asyncio.gather(
            deepgram_sender(),
            deepgram_receiver(),
            twilio_receiver()
        )

        await twilio_ws.close()


async def client_handler(client_ws):
    client_queue = asyncio.Queue()
    await client_ws.send(json.dumps(list(subscribers.keys())))

    try:
        callsid = (await client_ws.recv()).strip()
        if callsid in subscribers:
            subscribers[callsid].append(client_queue)
        else:
            await client_ws.close()
            return
    except:
        await client_ws.close()
        return

    async def client_sender():
        while True:
            message = await client_queue.get()
            if message == 'close':
                break
            try:
                await client_ws.send(message)
            except:
                break

    await client_sender()
    await client_ws.close()

async def router(websocket, path):
    if path == '/client':
        print("[+] Client connected")
        await client_handler(websocket)
    elif path == '/twilio':
        print("[+] Twilio connected")
        await twilio_handler(websocket)

def main():
    print("Starting WebSocket server at ws://localhost:8765...")
    server = websockets.serve(router, "0.0.0.0", 8765)
    asyncio.get_event_loop().run_until_complete(server)
    asyncio.get_event_loop().run_forever()

if __name__ == '__main__':
    sys.exit(main() or 0)
