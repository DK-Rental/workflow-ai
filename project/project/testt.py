import json
from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
import os

# --- LOAD JSON ---
with open("change_tenant_move_in_data_prior_to_signing.json") as f:
    data = json.load(f)

# --- BUILD TEXT ---
def build_embedding_text(data):
    text = f"Title: {data['title']}\n"
    text += f"Objective: {data['objective']}\n\n"

    for step in data["procedural_steps"]:
        text += f"Step {step['step']}: {step['task']}\n"
        text += f"{step['instruction']}\n\n"

    return text

text = build_embedding_text(data)
print("TEXT READY")

# --- OPENAI CLIENT ---
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_KEY"),
    api_version="2024-02-01",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)


# --- CREATE EMBEDDING ---
embedding = client.embeddings.create(
    model="text-embedding-3-large",
    input=text
)

print("Embedding created successfully")

# --- SEARCH CLIENT ---
search_client = SearchClient(
    endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
    index_name="sop-index",
    credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_KEY"))
)



# --- UPLOAD ---
doc = {
    "id": "1",
    "title": data["title"],
    "content": text,
    "embedding": embedding.data[0].embedding
}

UPLOAD = False 
if UPLOAD:
    search_client.upload_documents(documents=[doc])
    print("Uploaded to Azure Search")
# --- QUERY ---
while True:
    query = input("\nAsk a question (or type 'exit'): ")

    if query.lower() == "exit":
        break

    # create embedding for query
    query_embedding = client.embeddings.create(
        model="text-embedding-3-large",
        input=query
    ).data[0].embedding

    # search (still using fallback)
    results = search_client.search(
        search_text="*",
        top=3
    )

    best_result = None
    for result in results:
        best_result = result
        break

    response = client.chat.completions.create(
        model="gpt-35-turbo",
        messages=[
            {"role": "system", "content": "Answer using the SOP clearly."},
            {"role": "user", "content": query},
            {"role": "assistant", "content": best_result["content"]}
        ]
    )

    print("\nANSWER:\n")
    print(response.choices[0].message.content)