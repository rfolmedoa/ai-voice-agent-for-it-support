# === Imports ===
import asyncio
import base64
import json
import sys
import websockets
import ssl
from pydub import AudioSegment
from dotenv import load_dotenv
import os
from openai import AsyncOpenAI  # Added for ChatGPT integration

# === Load environment variables from .env file ===
load_dotenv()
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Added for ChatGPT

# === Initialize OpenAI client ===
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Dictionary to hold subscribers and conversation history for each call session
subscribers = {}
conversation_history = {}  # Store conversation history per callSid

# === Connect to Deepgram WebSocket API ===
def deepgram_connect():
    extra_headers = {
        'Authorization': "Token {}".format(DEEPGRAM_API_KEY)
    }
    return websockets.connect('wss://api.deepgram.com/v1/listen?encoding=mulaw&sample_rate=8000&channels=2&multichannel=true', extra_headers=extra_headers)

# === Handle Twilio WebSocket audio stream ===
async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    callsid_queue = asyncio.Queue()

    async with deepgram_connect() as deepgram_ws:

        # === Send audio chunks to Deepgram ===
        async def deepgram_sender(deepgram_ws):
            print('deepgram_sender started')
            while True:
                chunk = await audio_queue.get()
                await deepgram_ws.send(chunk)

        # === Receive transcription from Deepgram and integrate ChatGPT ===
        async def deepgram_receiver(deepgram_ws):
            print('deepgram_receiver started')
            callsid = await callsid_queue.get()
            subscribers[callsid] = []
            conversation_history[callsid] = []  # Initialize conversation history
            
            async for message in deepgram_ws:
                try:
                    response = json.loads(message)
                    transcript = response.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                    if transcript.strip():
                        print(f"[{callsid}] Transcript: {transcript}")
                        
                        # Add user transcript to conversation history
                        conversation_history[callsid].append({"role": "user", "content": transcript})
                        
                        # Send transcript to ChatGPT
                        chatgpt_response = await openai_client.chat.completions.create(
                            model="gpt-4o",  # Use desired model (e.g., gpt-4o or gpt-3.5-turbo)
                            messages=[
                                {"role": "system", "content": "You are a helpful assistant responding to a phone call transcript. Provide concise, natural responses suitable for a voice conversation."},
                                *conversation_history[callsid][-5:]  # Send last 5 messages for context
                            ],
                            max_tokens=100
                        )
                        chatgpt_text = chatgpt_response.choices[0].message.content
                        print(f"[{callsid}] ChatGPT: {chatgpt_text}")
                        
                        # Add ChatGPT response to conversation history
                        conversation_history[callsid].append({"role": "assistant", "content": chatgpt_text})
                        
                        # Send ChatGPT response back to Twilio as text (for TTS playback)
                        twilio_message = {
                            "event": "media",
                            "streamSid": response.get("streamSid", ""),
                            "media": {
                                "payload": base64.b64encode(chatgpt_text.encode()).decode()  # Encode text for Twilio
                            }
                        }
                        await twilio_ws.send(json.dumps(twilio_message))
                        
                        # Broadcast transcription to clients
                        for client in subscribers[callsid]:
                            client.put_nowait(message)
                except Exception as e:
                    print(f"Error processing message: {e}")
                
            # Notify all clients when stream ends
            for client in subscribers[callsid]:
                client.put_nowait('close')

            # Clean up
            del subscribers[callsid]
            del conversation_history[callsid]

        # === Receive and buffer audio from Twilio WebSocket ===
        async def twilio_receiver(twilio_ws):
            print('twilio_receiver started')
            BUFFER_SIZE = 20 * 160  # 20 ms of audio at 8000 Hz * 2 channels
            inbuffer = bytearray()
            outbuffer = bytearray()
            inbound_chunks_started = False
            outbound_chunks_started = False
            latest_inbound_timestamp = 0
            latest_outbound_timestamp = 0

            async for message in twilio_ws:
                try:
                    data = json.loads(message)
                    if data['event'] == 'start':
                        start = data['start']
                        callsid = start['callSid']
                        callsid_queue.put_nowait(callsid)

                    elif data['event'] == 'connected':
                        continue

                    elif data['event'] == 'media':
                        media = data['media']
                        chunk = base64.b64decode(media['payload'])

                        # Handle inbound (caller → system) audio
                        if media['track'] == 'inbound':
                            if inbound_chunks_started:
                                if latest_inbound_timestamp + 20 < int(media['timestamp']):
                                    bytes_to_fill = 8 * (int(media['timestamp']) - (latest_inbound_timestamp + 20))
                                    inbuffer.extend(b'\xff' * bytes_to_fill)
                            else:
                                inbound_chunks_started = True
                                latest_inbound_timestamp = int(media['timestamp'])
                                latest_outbound_timestamp = int(media['timestamp']) - 20

                            latest_inbound_timestamp = int(media['timestamp'])
                            inbuffer.extend(chunk)

                        # Handle outbound (system → caller) audio
                        elif media['track'] == 'outbound':
                            outbound_chunks_started = True
                            if latest_outbound_timestamp + 20 < int(media['timestamp']):
                                bytes_to_fill = 8 * (int(media['timestamp']) - (latest_outbound_timestamp + 20))
                                outbuffer.extend(b'\xff' * bytes_to_fill)
                            latest_outbound_timestamp = int(media['timestamp'])
                            outbuffer.extend(chunk)

                    elif data['event'] == 'stop':
                        break

                    # When buffers are full enough, mix audio and send to Deepgram
                    while len(inbuffer) >= BUFFER_SIZE and len(outbuffer) >= BUFFER_SIZE:
                        asinbound = AudioSegment(inbuffer[:BUFFER_SIZE], sample_width=1, frame_rate=8000, channels=1)
                        asoutbound = AudioSegment(outbuffer[:BUFFER_SIZE], sample_width=1, frame_rate=8000, channels=1)
                        mixed = AudioSegment.from_mono_audiosegments(asinbound, asoutbound)
                        audio_queue.put_nowait(mixed.raw_data)

                        # Shift buffer forward
                        inbuffer = inbuffer[BUFFER_SIZE:]
                        outbuffer = outbuffer[BUFFER_SIZE:]
                except:
                    break

            # End signal
            audio_queue.put_nowait(b'')

        # === Run all Twilio handling coroutines concurrently ===
        await asyncio.wait([
            asyncio.ensure_future(deepgram_sender(deepgram_ws)),
            asyncio.ensure_future(deepgram_receiver(deepgram_ws)),
            asyncio.ensure_future(twilio_receiver(twilio_ws))
        ])

        await twilio_ws.close()

# === Handle WebSocket connections from listening clients ===
async def client_handler(client_ws):
    client_queue = asyncio.Queue()

    # Send active callSIDs to client
    await client_ws.send(json.dumps(list(subscribers.keys())))
    try:
        callsid = await client_ws.recv()
        callsid = callsid.strip()

        if callsid in subscribers:
            subscribers[callsid].append(client_queue)
        else:
            await client_ws.close()
    except:
        await client_ws.close()

    # Stream transcription data to client
    async def client_sender(client_ws):
        while True:
            message = await client_queue.get()
            if message == 'close':
                break
            try:
                await client_ws.send(message)
            except:
                subscribers[callsid].remove(client_queue)
                break

    await asyncio.wait([
        asyncio.ensure_future(client_sender(client_ws)),
    ])

    await client_ws.close()

# === Router: choose endpoint based on path ===
async def router(websocket, path):
    if path == '/client':
        print('client connection incoming')
        await client_handler(websocket)
    elif path == '/twilio':
        print('twilio connection incoming')
        await twilio_handler(websocket)

# === Start WebSocket server ===
def main():
    server = websockets.serve(router, 'localhost', 8765)
    asyncio.get_event_loop().run_until_complete(server)
    asyncio.get_event_loop().run_forever()

# === Entry Point ===
if __name__ == '__main__':
    sys.exit(main() or 0)