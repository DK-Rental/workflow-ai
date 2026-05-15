import os
from dotenv import load_dotenv

load_dotenv()

# --- Identity Mapping ---
TENANT_ID = os.getenv("TENANT_ID", "")
# We map your CLIENT_ID to the Bot ID Joe needs
BOT_APP_ID = os.getenv("CLIENT_ID", "")          
BOT_APP_PASSWORD = os.getenv("CLIENT_SECRET", "")  

# --- OpenAI Mapping ---
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY  = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "")

# --- Search Mapping ---
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")

AZURE_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX","")
AZURE_SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING")
AZURE_BLOB_CONTAINER         = os.getenv("AZURE_BLOB_CONTAINER", "")
TEAMS_APP_PASSWORD = os.getenv("TEAMS_APP_PASSWORD", "")

AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "")

SYSTEM_PROMPT = """
You are "Joe Workflow", a senior property manager at DK Rentals.
Your sole responsibility is to help coworkers by providing instructions from the official SOP documents.

Rules:
1. Grounding: Answer ONLY using the provided SOP data (extracted from training videos).
2. Missing Info: If the SOP does not contain the answer, say: "I'm not sure — this is not in the SOP."
3. No Guesses: Do not use outside knowledge. Do not invent procedures.
4. Formatting: Use numbered steps for instructions and bold text for important warnings.
5. Tone: Professional, calm, and senior-level.
"""

