from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import queue
import threading
import asyncio
import requests

from dotenv import load_dotenv
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from openai import OpenAI
from autogen_core import SingleThreadedAgentRuntime, AgentId
from autogen_agentchat.messages import TextMessage
from autogen_core.models import ModelFamily
from form_agent import FormAgent
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from functools import partial
import asyncio
from fastapi import HTTPException
import httpx  
import anyio


load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# @app.get("/")
# async def root() -> FileResponse:          # GET /
#     return FileResponse("public/index.html")

# app.mount(
#     "/static",                             # everything in public/ is now /static/…
#     StaticFiles(directory="public"),
#     name="static",
# )

# Global clients and runtime
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

dg_client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)
runtime = SingleThreadedAgentRuntime()

@app.on_event("startup")
async def startup_event():
    # Register the FormAgent on startup
    await FormAgent.register(
        runtime,
        type="assistant",
        factory=lambda: FormAgent(
            api_key=os.getenv("JOTFORM_API_KEY"),
            form_id="251556541366157",
            # model_client_kwargs={
            #     "model": "Qwen/Qwen2.5-7B-Instruct",
            #     "base_url": "http://localhost:8888/v1/",
            #     "api_key": os.getenv("LOCAL_OPENAI_KEY"),
            #     "parallel_tool_calls": True,
            #     "model_info": {
            #         "vision": False,
            #         "function_calling": True,
            #         "json_output": True,
            #         "family": ModelFamily.ANY,
            #         "structured_output": True
            #     },
            # },
            model_client_kwargs={
                "model": "gpt-4o",
                "api_key": os.getenv("OPENAI_KEY"),
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

@app.post("/api/get-agent-response")
async def get_agent_response(request: Request):
    data = await request.json()
    text = data.get("text", "")
    message = await runtime.send_message(TextMessage(content=text, source="user"), AgentId("assistant", "default"))
    if not message:
        return JSONResponse({"error": "No response from agent"}, status_code=500)

    return JSONResponse({"agent_message": message.content})

@app.post("/api/tts")
def tts(request: Request):
    # data = request.json()
    data = anyio.from_thread.run(     # run the async call in the thread‑pool
        request.json
    )
    text = data.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "No text provided"}, status_code=400)

    print(f"TTS Text: {text}")

    url = "https://api.deepgram.com/v1/speak?model=aura-asteria-en"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Token {DEEPGRAM_API_KEY}"
    }
    payload = json.dumps({"text": text})

    r = requests.post(url, headers=headers, data=payload, stream=True)
    if r.status_code != 200:
        return JSONResponse({"error": "Deepgram TTS failed", "details": r.text}, status_code=500)

    def generate_audio():
        for chunk in r.iter_content(chunk_size=4096):
            if chunk:
                yield chunk

    return StreamingResponse(generate_audio(), media_type="audio/mpeg")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_running_loop()
    # Setup conversation and Deepgram connection
    conversation_history = [{
        "role": "system",
        "content": (
            "You are the digital twin assistant of San Antonio. Your capabilities include navigating the map,"
            " showing heat maps, changing weather, and setting time of day. If a command is unclear, ask for clarity;"
            " otherwise, respond concisely and professionally."
        )
    }]
    audio_queue = queue.Queue()
    stop_event = threading.Event()

    def send_to_client(payload: dict):
        """Schedule websocket.send_json on the main loop, thread‑safe."""
        future = asyncio.run_coroutine_threadsafe(
            websocket.send_json(payload),
            loop
        )
        # optional: handle exceptions raised by the coroutine
        future.add_done_callback(
            lambda fut: fut.exception() and print(f"WS send error: {fut.exception()}")
        )
        
    def audio_sender():
        while not stop_event.is_set():
            try:
                chunk = audio_queue.get(timeout=0.5)
                if chunk is None:
                    break
                dg_ws.send(chunk)
            except queue.Empty:
                continue

    # Deepgram WebSocket
    dg_ws = dg_client.listen.websocket.v('1')
   

    dg_ws.on(LiveTranscriptionEvents.Open, lambda *args: print("Deepgram connected"))
    dg_ws.on(
        LiveTranscriptionEvents.Transcript,
        lambda _client, result: send_to_client(
            {"type": "transcript", "data": result.to_dict()}
        ),
    )

    dg_ws.on(
        LiveTranscriptionEvents.Error,
        lambda _client, err: send_to_client(
            {"type": "error", "message": str(err)}
        ),
    )

    dg_ws.on(LiveTranscriptionEvents.Close, lambda *args: print("Deepgram disconnected"))
    dg_ws.start(
        LiveOptions(
            language='en',
            punctuate=True,
            smart_format=True,
            model='nova-3',
            interim_results=True,
            endpointing=1000
        )
    )

    audio_thread = threading.Thread(target=audio_sender)
    audio_thread.start()

    try:
        while True:
            event = await websocket.receive()

            # Stop cleanly when the browser disconnects
            if event["type"] == "websocket.disconnect":
                break

            # Binary audio chunk coming from the browser
            if event.get("bytes") is not None:
                audio_queue.put(event["bytes"])
                continue

    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        audio_queue.put(None)
        audio_thread.join()
        try:
            dg_ws.finish()
        except:
            pass

# Run with: uvicorn server:app --host 0.0.0.0 --port 3035
