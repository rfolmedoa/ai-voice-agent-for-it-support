# AI Voice Agent for IT Support (WIP)

1. Clone the repository:

   ```bash
   git clone https://github.com/rfolmedoa/ai-voice-agent-for-it-support.git
   ```

2. Create a virtual environment: 

   ```bash
   conda env create -f environment.yml
   ```

3. Create a free trial account for:

   - Twilio ($15 in free credits)
   - Deepgram ($200 in free credits)
   - Jotform (5 forms limit)

   (Optional) If you pay $20 on Twilio, you will have access to those credits + Twilio Dev Phone to run an application on your local system and make calls directly from your browser instead of using your mobile phone. This is very useful when testing the application. To use this feature, use the following documentation to install Twilio CLI and run Twilio Dev Phone: 

   [Twilio Dev Phone](https://www.twilio.com/docs/labs/dev-phone)

3. Create a .env file with the following API keys:

   - DEEPGRAM_API_KEY
   - OPENAI_API_KEY
   - JOTFORM_API_KEY

5. Install ngrok on the machine where the server will run to expose it to the Internet:

   [Download ngrok for macOS](https://ngrok.com/downloads/mac-os)

6. Run the main.py file (WebSocket server on port 5000):

   ```bash
   python main.py
   ```

7. Run ngrok:

   ```bash
   ngrok http 5000
   ```