
---

# Interpreter Generator  

## **Python Server for Translation Agent Generation**  

### Overview  

This Python server leverages the **Agora OpenAI Python SDK** to generate AI agents capable of real-time audio translation. These agents listen to specific users in an Agora RTC channel, translate their speech directly to a target language with ultra-low latency, and stream the translated audio back into the channel.  

---

### Instructions  

#### **Setup and Installation**  

1. **Prepare the Environment**:  
   - Create a `.env` file for the backend by copying the example file:  
     ```bash
     cp .env.example .env
     ```  
   - Populate the `.env` file with the necessary values.  

2. **Obtain Tokens and IDs**:  
   - **Agora Account**:  
     - [Log in to Agora](https://console.agora.io/en/).  
     - Create a [New Project](https://console.agora.io/projects) with `Secured mode: APP ID + Token` to get the **App ID** and **App Certificate**.  
   - **OpenAI Account**:  
     - [Log in to OpenAI](https://platform.openai.com/signup).  
     - Go to the dashboard to [obtain your API key](https://platform.openai.com/api-keys).  

3. **Set Up a Virtual Environment**:  
   ```bash
   python3 -m venv venv && source venv/bin/activate
   ```  

4. **Install Dependencies**:  
   ```bash
   pip install -r requirements.txt
   ```  

5. **Run the Server**:  
   ```bash
   python -m realtime_agent.main server
   ```  

---

### Repository Organization  

- **`realtimeAgent/realtime`**: Contains the core Python implementation for interacting with the Realtime API.  
- **`realtimeAgent/agent.py`**: Includes a demo agent built using the `realtime` module and the [Agora-Realtime-AI-API](https://pypi.org/project/agora-realtime-ai-api/).  
- **`realtimeAgent/main.py`**: Provides the main web server for starting and stopping AI-driven agents.  

This repository is built on top of the **OpenAI Realtime Python SDK**.  

---

### Endpoints  

#### **1. `/start_agent`**  
**Body Parameters**:  
- `channel_name` (string): The Agora RTC channel name.  
- `uid` (int): The 8-digit UID of the agent.  
- `target_user_id` (int): The 4-digit UID of the user the agent will listen to.  
- `system_instruction` (string): Instructions to configure the agent as an interpreter.  

**Functionality**:  
- Creates an **OpenAI agent session** with the provided instructions.  
- Connects the agent to the specified Agora RTC channel using the given channel name and UID.  
- Subscribes the agent to the specified target user (using `target_user_id`).  
- The agent listens to the target user’s audio in real-time, translates it directly to the target language via the OpenAI Realtime WSS connection, and streams the translated audio back into the channel using Agora RTC with ultra-low latency.  

**Notes**:  
- Adjust the agent’s **Voice Activity Detection (VAD)** settings in `main.py` to modify silence duration thresholds.  

---

### Key Features  

- **Low Latency Translation**: The agent translates audio directly to the target language, bypassing intermediate audio-to-text conversion for minimal delay.  
- **Real-Time Streaming**: Translated audio is streamed back to the Agora RTC channel with ultra-low latency.  
- **Configurable VAD**: Silence duration thresholds can be adjusted for better control of speech detection.  

---

### Related Repositories  

- **Node.js Service**: Manages token generation and AI agent coordination. [Repository Link](https://github.com/nitin4real/agora-backend)
- **Frontend**: Provides the user interface for the meeting scenario. [Repository Link](https://github.com/nitin4real/react-video-call-project)  

---
