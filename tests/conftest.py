import sys
import os
from unittest.mock import MagicMock

# Add project root to Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Set fake environment variables (required for llm_client import)
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://fake-endpoint.openai.azure.com/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT", "fake-deployment")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "fake-api-key-for-testing")

# Mock external dependencies so they don't need to be installed
for mod in [
    "openai",
    "azure",
    "azure.identity",
    "azure.search",
    "azure.search.documents",
    "azure.core",
    "azure.core.credentials",
    "dotenv",
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()