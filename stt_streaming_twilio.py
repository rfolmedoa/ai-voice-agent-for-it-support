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

# === Load environment variables from .env file ===
load_dotenv()
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

# Dictionary to hold subscribers for each call session
subscribers = {}

# === Connect to Deepgram WebSocket API ===
def deepgram_connect():
    extra_headers = {
        'Authorization': "Token {}".format(DEEPGRAM_API_KEY)
    }
    # Open Deepgram WebSocket connection with multi-channel audio
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

        # === Receive transcription from Deepgram ===
        async def deepgram_receiver(deepgram_ws):
            print('deepgram_receiver started')
            callsid = await callsid_queue.get()
            subscribers[callsid] = []
            
            # Stream transcription results from Deepgram
            async for message in deepgram_ws:
                try:
                    response = json.loads(message)
                    transcript = response.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                    if transcript.strip():
                        print(f"[{callsid}] {transcript}")
                except Exception as e:
                    print(f"Error decoding Deepgram message: {e}")
                
                # Broadcast message to any listening clients
                for client in subscribers[callsid]:
                    client.put_nowait(message)

            # Notify all clients when stream ends
            for client in subscribers[callsid]:
                client.put_nowait('close')

            del subscribers[callsid]

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
                                # Fill in silence for dropped packets
                                if latest_inbound_timestamp + 20 < int(media['timestamp']):
                                    bytes_to_fill = 8 * (int(media['timestamp']) - (latest_inbound_timestamp + 20))
                                    inbuffer.extend(b'\xff' * bytes_to_fill)
                            else:
                                # First inbound packet
                                inbound_chunks_started = True
                                latest_inbound_timestamp = int(media['timestamp'])
                                latest_outbound_timestamp = int(media['timestamp']) - 20

                            latest_inbound_timestamp = int(media['timestamp'])
                            inbuffer.extend(chunk)

                        # Handle outbound (system → caller) audio
                        elif media['track'] == 'outbound':
                            outbound_chunked_started = True
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
                # Client disconnected
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
