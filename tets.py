async def deepgram_stt(streamsid, twilio_ws, audio_queue):
    ai_speaking = False
    pending_response_task = None

    uri = (
        "wss://api.deepgram.com/v1/listen"
        "?encoding=linear16"
        "&sample_rate=8000"
        "&language=en"
        "&punctuate=true"
        "&smart_format=true"
        "&model=nova-3"
        "&interim_results=true"
        "&endpointing=2000"
        "&speech_final=false"
    )

    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    async with websockets.connect(uri, extra_headers=headers) as dg_ws:

        async def send_audio():
            while True:
                mulaw_audio = await audio_queue.get()
                linear_audio = audioop.ulaw2lin(mulaw_audio, 2)
                await dg_ws.send(linear_audio)

        async def delayed_response(text):
            global ai_speaking
            try:
                await asyncio.sleep(1.0)  # debounce wait
                ai_speaking = True
                print("ChatGPT: (thinking on)", text)
                response_text = await get_chatgpt_response(text)
                print("ChatGPT:", response_text)
                await send_tts_to_twilio(response_text, streamsid, twilio_ws)
            finally:
                ai_speaking = False

        async def receive_transcript():
            nonlocal pending_response_task
            current_user_transcript = ""

            async for message in dg_ws:
                msg = json.loads(message)
                transcript = msg.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                is_final = msg.get("is_final", False)

                if not transcript.strip():
                    continue

                print("is_final:", is_final, "Transcript:", transcript.strip())

                if ai_speaking:
                    print("Ignoring input during AI speech.")
                    continue

                if is_final:
                    current_user_transcript = transcript.strip()

                    if "goodbye" in current_user_transcript or "exit" in current_user_transcript:
                        farewell = "Goodbye! Ending the call now."
                        print("ChatGPT:", farewell)
                        await send_tts_to_twilio(farewell, streamsid, twilio_ws)
                        await asyncio.sleep(2)
                        await twilio_ws.close()
                        break

                    # Cancel any previous delayed response
                    if pending_response_task:
                        pending_response_task.cancel()

                    # Schedule new delayed response
                    pending_response_task = asyncio.create_task(delayed_response(current_user_transcript))

                else:
                    # Interim transcript detected → cancel pending response
                    if pending_response_task:
                        print("Cancelling pending AI response due to continued speech.")
                        pending_response_task.cancel()
                        pending_response_task = None

        await asyncio.gather(send_audio(), receive_transcript())


















mport asyncio
import audioop
import json
import websockets



async def deepgram_stt(streamsid, twilio_ws, audio_queue):
    uri = (
        "wss://api.deepgram.com/v1/listen"
        "?encoding=linear16"
        "&sample_rate=8000"
        "&language=en"
        "&punctuate=true"
        "&smart_format=true"
        "&model=nova-3"
        "&interim_results=true"
        "&endpointing=2000"
        "&speech_final=true"
    )

    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    pending_task = None
    current_transcript = ""
    ai_speaking = False

    async with websockets.connect(uri, extra_headers=headers) as dg_ws:

        async def send_audio():
            while True:
                mulaw_audio = await audio_queue.get()
                linear_audio = audioop.ulaw2lin(mulaw_audio, 2)
                await dg_ws.send(linear_audio)

        async def respond_debounced(transcript):
            global ai_speaking
            try:
                await asyncio.sleep(1.0)  # debounce window
                ai_speaking = True
                print("ChatGPT: Thinking on", transcript)
                
                if "goodbye" in transcript.lower() or "exit" in transcript.lower():
                    farewell = "Goodbye! Ending the call now."
                    print("ChatGPT:", farewell)
                    await send_tts_to_twilio(farewell, streamsid, twilio_ws)
                    await asyncio.sleep(2)
                    await twilio_ws.close()
                    return

                response = await get_chatgpt_response(transcript)
                print("ChatGPT:", response)
                await send_tts_to_twilio(response, streamsid, twilio_ws)
            finally:
                ai_speaking = False

        async def receive_transcript():
            nonlocal pending_task, current_transcript
            async for message in dg_ws:
                msg = json.loads(message)
                transcript = msg.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                is_final = msg.get("is_final", False)

                if not transcript.strip():
                    continue

                print("is_final:", is_final, "| Transcript:", transcript.strip())

                if ai_speaking:
                    print("Ignoring speech while AI is talking...")
                    continue

                if is_final:
                    current_transcript = transcript.strip()

                    if pending_task:
                        pending_task.cancel()

                    pending_task = asyncio.create_task(respond_debounced(current_transcript))

                else:
                    # Interim result after an is_final means user kept talking
                    if pending_task:
                        print("Canceled pending response: user continued speaking.")
                        pending_task.cancel()
                        pending_task = None

        await asyncio.gather(send_audio(), receive_transcript())