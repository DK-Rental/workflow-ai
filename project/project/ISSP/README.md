# Workflow AI: SOP Uploader & Joe Teams Bot

This repository is organized around a **Flask-based backend**, a **lightweight video uploader frontend**, background processing threads for audio transcription, and a **Microsoft Bot Framework integration** powered by Azure OpenAI.

The application is deployed as **two separate Microsoft Teams applications** (a frontend Tab and a Chat Bot) backed by a unified server. 

To fill in the values in the `.env` file, refer to your Azure Portal configurations. After confirming that the application works correctly in production, you should regenerate all API keys and replace the old ones for security.

---

## High-Level Structure

```text
.
├── .github/workflows/         # CI/CD deployment pipelines (e.g., main_dk-workflow-ai.yml)
├── tests/                     # Global test suite (conftest.py, test_issp.py)
└── project/project/ISSP/      # Main Application Root
    ├── Joe-Teams/             # Manifest & icons for the Joe Chat Bot App
    ├── video-uploader/        # Manifest & icons for the SOP Uploader App
    ├── services/
    │   ├── ai_search.py       # Azure AI Search index management
    │   ├── graph_client.py    # Microsoft Graph API integration (Legacy/Context)
    │   └── llm_client.py      # Core AI logic (RAG querying and JSON SOP generation)
    ├── SOP_Workflow/          # Knowledge base source files (docx, csv) for RAG indexing
    ├── static/                # Frontend JS and CSS
    ├── templates/             # Frontend HTML (index.html)
    ├── app.py                 # Main Flask server & video upload endpoints
    ├── team_routes.py         # Microsoft Bot Framework routing for Joe
    ├── speech_service.py      # Audio extraction and transcription service
    ├── config.py              # Environment variable configuration
    ├── loom_videos.db         # Local SQLite database for background job tracking
    ├── test_joe.py            # Local terminal testing script for the Bot
    └── test_openai.py         # Local terminal testing script for LLM connectivity
```

---

## Application Entry & Processing

The core processing is divided across dedicated routing and service files within the `ISSP` directory.

### `app.py` (Web Server & Background Jobs)
* **Flask Web Server:** Serves the frontend UI and handles `/api/video/upload` and `/api/video/loom` REST endpoints.
* **Background Threading:** Video transcription and LLM generation can take minutes. `app.py` spawns daemon threads so the frontend doesn't time out.
* **SQLite Job Tracking:** Maintains `loom_videos.db` to track upload statuses (`processing`, `done`, `error`) so the frontend can poll for updates.

### `team_routes.py` (Bot Framework)
* Hosts the `/api/messages` route.
* Listens for Microsoft Teams messages, manages conversation memory (`_history`), and parses group chat mentions to trigger the AI safely.

### `speech_service.py` (Transcription)
* Dedicated service for extracting audio from uploaded video files and converting it to raw text for the LLM to process.

---

## Service Layer (`services/`)

### `llm_client.py`
This module encapsulates all LLM interaction logic, divided strictly into two distinct responsibilities:
* **Azure OpenAI + Azure AI Search (RAG):** Handled by `ask_llm()`. Used exclusively by Joe the Bot to pull verified SOP documents and answer questions.
* **Transcript-to-SOP Generation:** Handled by `generate_sop_from_transcript()`. Used by the background workers in `app.py` to force the Azure LLM into `json_object` mode, converting raw audio transcripts into structured SOP schemas.

### `ai_search.py` & `SOP_Workflow/`
* The `SOP_Workflow` directory contains the raw Word documents and Excel sheets provided by the sponsor.
* These files are indexed into Azure Search (managed via `ai_search.py`) to act as the authoritative knowledge base for Joe's RAG system.

---

## Microsoft Teams Integration & Bot Logic

This system uses a **Dual-Manifest Architecture** to prevent Teams UI rendering bugs and keep the user experience cleanly separated.

### App 1: The SOP Uploader (Sidebar Tab)
* **Manifest Location:** `ISSP/video-uploader/`
* **Behavior:** A Personal Static Tab that embeds the Flask web interface (`index.html`).
* **Purpose:** Allows employees to drag-and-drop video files or paste Loom URLs to generate new SOPs.
* **State:** Stateless. The UI polls the backend SQLite database for job progress.

### App 2: Joe (AI Chat Bot)
* **Manifest Location:** `ISSP/Joe-Teams/`
* **Behavior:** A conversational bot integrated into private and group chats.
* **State & Memory:** Stateful. Joe maintains a rolling `_history` dictionary (up to 10 turns per conversation) so users can ask follow-up questions.
* **Group Chat Logic (The Silent Listener):**
  * The manifest requests Resource-Specific Consent (`ChannelMessage.Read.Group`).
  * Joe reads every message in a group chat to build conversation context silently in his memory.
  * He only queries the LLM and replies if he is explicitly tagged using an `@Joe` mention or the `#Joe` hashtag.

---

## Local Development & Testing

Because packaging and uploading zip files to Microsoft Teams is slow, the project includes dedicated local testing tools.

### `test_joe.py` & `test_openai.py`
Local terminal testing scripts that bypass the Flask server and Microsoft Teams entirely.
* **Usage:** Run `python test_joe.py` in your terminal.
* **Purpose:** Allows developers to test Joe's memory, Azure Search RAG connectivity, and LLM prompt formatting instantly via the command line without needing to tunnel to Teams.

### How to Run Locally
1. Ensure `.env` is populated with Azure OpenAI, Azure Search, Azure Blob, and Teams Bot credentials.
2. Navigate to the `ISSP` directory.
3. Start the Flask server: `python app.py` (Runs on port `3978`).
4. Ensure Dev Tunnels / ngrok are updated in the Azure Bot Portal if testing the live Teams connection locally.

---

## Azure Infrastructure & Services

This application relies on a suite of Microsoft Azure services to handle hosting, artificial intelligence, data storage, audio transcription, and Teams routing.

### 1. Azure App Service (Web App)
* **Resource Name:** `dk-workflow-ai`
* **Purpose:** The primary hosting environment for the Python Flask backend.
* **Role in App:** Runs `app.py`, processes background threading for video uploads, serves the frontend React/HTML for the SOP Uploader Tab, and hosts the webhook (`/api/messages`) for the Teams bot.

### 2. Azure OpenAI Service
* **Purpose:** The core Artificial Intelligence engine.
* **Role in App:** Accessed via the `openai` Python SDK. It is used in two distinct ways:
  1. **Generative Processing:** Converts raw audio transcripts into strictly formatted JSON SOPs.
  2. **Conversational AI:** Powers Joe's chat responses using a specific deployment model (e.g., GPT-4o) configured in the `.env` file.

### 3. Azure AI Search
* **Resource Name:** `dk-workflow-ai-search`
* **Purpose:** The Vector Database and Retrieval-Augmented Generation (RAG) engine.
* **Role in App:** Stores the indexed SOP documents (Word docs, CSVs). When a user asks Joe a question, this service retrieves the most relevant documents to feed to the LLM.

### 4. Azure Blob Storage
* **Resource Name:** `dksopstorage` (Container: `sop`)
* **Purpose:** Unstructured cloud data storage.
* **Role in App:** When a video is transcribed and converted into a JSON SOP, the raw JSON file is pushed to the `sop` container for permanent safekeeping before it is indexed by Azure Search.

### 5. Azure Bot Service
* **Resource Name:** `workflow-ai-bot` (Joe)
* **Purpose:** The bridge between Microsoft Teams and your Flask server.
* **Role in App:** Manages the Bot Framework credentials. It routes messages typed by users in Microsoft Teams directly to your Flask app's `/api/messages` endpoint.

### 6. Azure AI Speech Service
* **Resource Name:** `loom-video-transcription`
* **Purpose:** Audio-to-text processing.
* **Role in App:** Used by `speech_service.py` to extract and transcribe the audio from video files uploaded through the Teams sidebar app.

---

## Environment Variables (.env Setup)

To run this application locally or deploy it to a new environment, you must create a `.env` file in the `ISSP` root directory. Do not commit this file to version control.

Below is the required template. Request the actual API keys and secrets from the Azure administrator.

```env
# ── Azure OpenAI Settings ──
AZURE_OPENAI_ENDPOINT="https://<YOUR_OPENAI_RESOURCE_NAME>[.openai.azure.com/](https://.openai.azure.com/)"
AZURE_OPENAI_API_KEY="<your-openai-api-key>"
AZURE_OPENAI_API_VERSION="2024-02-15-preview"
AZURE_OPENAI_DEPLOYMENT="<your-model-deployment-name>"

# ── Azure AI Search Settings ──
AZURE_SEARCH_ENDPOINT="[https://dk-workflow-ai-search.search.windows.net](https://dk-workflow-ai-search.search.windows.net)"
AZURE_SEARCH_KEY="<your-search-admin-key>"
AZURE_SEARCH_INDEX="<your-index-name>"

# ── Azure Blob Storage Settings ──
AZURE_BLOB_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=dksopstorage;AccountKey=<your-account-key>;EndpointSuffix=core.windows.net"
AZURE_BLOB_CONTAINER="sop"

# ── Azure AI Speech Settings ──
SPEECH_KEY="<your-speech-service-key>"
SPEECH_REGION="<your-speech-resource-region>"

# ── Teams Bot Settings (workflow-ai-bot) ──
TEAMS_APP_ID="289fecd3-6bc5-440b-b6ec-904e54becdde"
TEAMS_APP_PASSWORD="<your-azure-bot-client-secret>"
```

---

## Deployment (CI/CD)

This project uses **GitHub Actions** for continuous integration and deployment to Azure.

* **Workflow File:** `.github/workflows/main_dk-workflow-ai.yml`
* **Trigger:** Any push or merge to the `main` branch automatically triggers the build and deployment pipeline.
* **Process:** The action sets up the Python environment, installs dependencies from `requirements.txt`, and deploys the zipped artifact directly to the `dk-workflow-ai` Azure App Service.
* **Note for future devs:** If you add new system-level dependencies or change the startup command, ensure the GitHub Actions workflow and the Azure App Service startup configurations are updated accordingly.

---

## Installing the Apps in Microsoft Teams

Because this project uses a Dual-Manifest architecture, you must install two separate apps into Teams. 

### 1. Packaging the Apps
If you make changes to the names, descriptions, or URLs in the manifest files, you must re-package them:
1. Navigate to `ISSP/video-uploader/` and zip `manifest.json`, `color.png`, and `outline.png` into `sop-uploader-app.zip`.
2. Navigate to `ISSP/Joe-Teams/` and zip `manifest.json`, `color.png`, and `outline.png` into `joe-chat-app.zip`.

### 2. Uploading to Teams
1. Open Microsoft Teams and click on **Apps** in the left-hand sidebar.
2. Click **Manage your apps** at the bottom of the screen.
3. Click **Upload an app** -> **Upload a custom app**.
4. Select `sop-uploader-app.zip` to install the UI tab.
5. Repeat the process for `joe-chat-app.zip` to install Joe.
6. **Important:** When installing Joe, Teams will prompt you to grant the `ChannelMessage.Read.Group` permission. An admin must approve this for the "Silent Listener" logic to work in group chats.