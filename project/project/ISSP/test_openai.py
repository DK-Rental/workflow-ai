"""Run this to test your Azure OpenAI connection independently of Flask."""
import config
from openai import AzureOpenAI

print("Endpoint  :", config.AZURE_OPENAI_ENDPOINT)
print("Deployment:", config.AZURE_OPENAI_DEPLOYMENT)
print("API Ver   :", config.AZURE_OPENAI_API_VERSION)
print("Key (last4):", config.AZURE_OPENAI_API_KEY[-4:])
print()

client = AzureOpenAI(
    azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
    api_key=config.AZURE_OPENAI_API_KEY,
    api_version=config.AZURE_OPENAI_API_VERSION,
)

try:
    response = client.chat.completions.create(
        model=config.AZURE_OPENAI_DEPLOYMENT,
        messages=[{"role": "user", "content": "Say hello in one word."}],
        timeout=15,
    )
    print("SUCCESS:", response.choices[0].message.content)
except Exception as e:
    print("FAILED:", e)