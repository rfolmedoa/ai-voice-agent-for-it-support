import openai
import requests
from pydub import AudioSegment
import sounddevice as sd
import numpy as np
import io
import os
from dotenv import load_dotenv

load_dotenv()

# Set your API keys (use os.getenv for production!)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


openai.api_key = OPENAI_API_KEY

def ask_chatgpt(messages):
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",  # or "gpt-4o"
        messages=messages,
        temperature=0.7,
    )
    return response.choices[0].message.content

def speak_deepgram(text):
    url = "https://api.deepgram.com/v1/speak"
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "application/json"
    }
    params = {
        "model": "aura-2-thalia-en",
        "encoding": "mp3"
    }
    payload = {"text": text}
    response = requests.post(url, headers=headers, params=params, json=payload)
    if response.status_code != 200:
        print("Deepgram TTS Error:", response.status_code, response.text)
        return b''

    # Convert mp3 to AudioSegment
    mp3_bytes = io.BytesIO(response.content)
    audio = AudioSegment.from_file(mp3_bytes, format="mp3")

    # Convert to 8kHz mono 8-bit mu-law
    audio = audio.set_channels(1)
    audio = audio.set_frame_rate(8000)
    audio = audio.set_sample_width(1)  # 8-bit = 1 byte
    mulaw_audio = audio.set_sample_width(1).raw_data

    # Optional: play it locally
    samples = np.array(audio.get_array_of_samples()).astype(np.float32) / (2 ** 7)  # 8-bit scale
    sd.play(samples, samplerate=8000)
    sd.wait()

    return mulaw_audio 
    

if __name__ == "__main__":
    print("Type 'exit' or 'quit' to stop.")
    # Keep the full conversation context in the messages list!
    messages = []
    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ("exit", "quit"):
            print("Goodbye!")
            break
        messages.append({"role": "user", "content": user_input})
        response_text = ask_chatgpt(messages)
        print("ChatGPT:", response_text)
        messages.append({"role": "assistant", "content": response_text})
        speak_deepgram(response_text)