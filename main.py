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
import audioop
import uvicorn

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request


load_dotenv()

app = FastAPI()

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
JOTFORM_API_KEY = os.getenv("JOTFORM_API_KEY")
openai = OpenAI(api_key=OPENAI_API_KEY)

# ----- AutoGen ----



from autogen_core import SingleThreadedAgentRuntime, AgentId
from autogen_agentchat.messages import TextMessage
from autogen_core.models import ModelFamily
from form_agent import FormAgent

runtime = SingleThreadedAgentRuntime()

@app.on_event("startup")
async def startup_event():
    # Register the FormAgent on startup
    await FormAgent.register(
        runtime,
        type="assistant",
        factory=lambda: FormAgent(
            api_key=os.getenv("JOTFORM_API_KEY"),
            form_id="251997111120854",
            model_client_kwargs={
                "model": "gpt-4o",
                "api_key": os.getenv("OPENAI_API_KEY"),
            },
            reflect_on_tool_use=True,
            model_client_stream=False,
        ),
    )
    # Launch the runtime.start() in a background thread to avoid blocking
    runtime.start()

@app.on_event("shutdown")
async def shutdown_event():
    await runtime.stop_when_idle()



# ----- OpenAI -----

async def get_chatgpt_response(prompt: str) -> str:
    try:

        message = await runtime.send_message(TextMessage(content=prompt, source="user"), AgentId("assistant", "default"))

        return message.content
    except Exception as e:
        print("OpenAI error:", e)
        return "Sorry, I had a problem generating a response."

# ----- Deepgram TTS -----
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
        await twilio_ws.send_text(json.dumps(media_message))
    else:
        print("TTS Error:", response.text)

# ----- Deepgram STT Handler -----
async def deepgram_stt(streamsid, twilio_ws, audio_queue):
    uri = "wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate=8000&model=nova-3"
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    async with websockets.connect(uri, extra_headers=headers) as dg_ws:
        async def send_audio():
            while True:
                mulaw_audio = await audio_queue.get()
                # Convert μ-law to 16-bit PCM
                linear_audio = audioop.ulaw2lin(mulaw_audio, 2)  # 2 bytes (16-bit)
                await dg_ws.send(linear_audio)

        async def receive_transcript():
            buffer = ""
            async for message in dg_ws:
                msg = json.loads(message)
                transcript = msg.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                is_final = msg.get("is_final", False)

                if transcript:
                    print("You:", transcript)
                
                if is_final and transcript.strip():
                    user_input = transcript.strip().lower()
                    if "goodbye" in user_input or "exit" in user_input:
                        farewell = "Goodbye! Ending the call now."
                        print("ChatGPT:", farewell)
                        await send_tts_to_twilio(farewell, streamsid, twilio_ws)
                        await asyncio.sleep(2)  # allow time for playback
                        await twilio_ws.close()
                        break

                if is_final and transcript.strip():
                    response_text = await get_chatgpt_response(transcript.strip())
                    print("ChatGPT:", response_text)
                    await send_tts_to_twilio(response_text, streamsid, twilio_ws)

        await asyncio.gather(send_audio(), receive_transcript())

# ----- Twilio Handler -----
@app.websocket("/twilio")
async def twilio_handler(twilio_ws: WebSocket):
    await twilio_ws.accept()
    audio_queue = asyncio.Queue()
    streamsid = None

    async def twilio_receiver():
        nonlocal streamsid
        try:
            async for raw_msg in twilio_ws.iter_text():
                data = json.loads(raw_msg)
                if data["event"] == "start":
                    streamsid = data["start"]["streamSid"]
                    print(f"🔗 Twilio Stream Started: {streamsid}")
                elif data["event"] == "media":
                    payload = data["media"]["payload"]
                    raw_audio = base64.b64decode(payload)
                    await audio_queue.put(raw_audio)
                elif data["event"] == "stop":
                    print("🛑 Twilio Stream Ended")
                    break
        except WebSocketDisconnect:
            print("🛑 FastAPI WebSocket disconnected in receiver")

    async def stt_task():
        try:
            while streamsid is None:
                await asyncio.sleep(0.05)
            await deepgram_stt(streamsid, twilio_ws, audio_queue)
        except WebSocketDisconnect:
            print("🛑 WebSocket disconnected in STT")

    await asyncio.gather(twilio_receiver(), stt_task())

# ----- WebSocket Server Router -----
async def router(websocket, path):
    if path == "/twilio":
        print("🌐 Incoming Twilio connection...")
        await twilio_handler(websocket)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=True)
