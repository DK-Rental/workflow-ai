import os
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.getenv("TENANT_ID", "")
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
SHARED_MAILBOX = os.getenv("SHARED_MAILBOX", "")

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY  = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
ONEDRIVE_USER_UPN = os.getenv("ONEDRIVE_USER_UPN", globals().get("ONEDRIVE_USER_UPN", ""))
ONEDRIVE_DRIVE_ID = os.getenv("ONEDRIVE_DRIVE_ID", globals().get("ONEDRIVE_DRIVE_ID", ""))

SYSTEM_PROMPT = """
You are "Strata Workflow Assistant".
Your job:
1. Check recent notices, AGMs, insurance renewals, infractions, levy notices, leaks, or manager changes from the shared strata inbox.
2. Tell the assistant where to find the source (email subject + receivedDateTime).
3. If the question is about insurance / access / levy / infraction / leak, label it HIGH RISK.
4. If nothing relevant is found, say so politely.
Never invent an answer. Never promise legal or financial outcomes.
"""
