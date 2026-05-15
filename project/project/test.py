import json
from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
import os

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_KEY"),
    api_version="2024-02-01",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

search_client = SearchClient(
    endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
    index_name="sop-index",
    credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_KEY"))
)


# --- BLOB STORAGE CONNECTION ---
connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
container_name = "sops"

blob_service_client = BlobServiceClient.from_connection_string(connection_string)
container_client = blob_service_client.get_container_client(container_name)

# --- BUILD TEXT ---
# --- BUILD TEXT ---
def build_embedding_text(data):
    text = f"Title: {data.get('title', '')}\n"
    text += f"Objective: {data.get('objective', '')}\n\n"

    for step in data.get("procedural_steps", []):
        text += f"Step {step.get('step', '')}: {step.get('task', '')}\n"

        for key in ["instruction", "note", "warning", "constraint"]:
            if key in step:
                text += f"{step[key]}\n"

        text += "\n"

    return text

# --- LOAD + EMBED ---
all_docs = []

for blob in container_client.list_blobs():
    if not blob.name.endswith(".json"):
        continue

    blob_client = container_client.get_blob_client(blob.name)
    content = blob_client.download_blob().readall()
    data = json.loads(content)

    text = build_embedding_text(data)
    print(f"Processing: {blob.name}")

    embedding = client.embeddings.create(
        model="text-embedding-3-large",
        input=text
    )

    clean_id = blob.name.replace("/", "_").replace(".json", "")

    systems = data.get("systems", "")
    if isinstance(systems, dict):
        systems_list = [systems.get("primary", "")]
    elif isinstance(systems, list):
        systems_list = systems
    else:
        systems_list = [str(systems)]

    doc = {
        "chunk_id": clean_id,
        "parent_id": data.get("sop_id", clean_id),
        "chunk": text,
        "text_vector": embedding.data[0].embedding,
        "sop_id": data.get("sop_id", ""),
        "title": data.get("title", ""),
        "systems": systems_list
    }

    all_docs.append(doc)

print("ALL TEXT READY")

# --- UPLOAD (RUN ONCE ONLY) ---
UPLOAD = False

if UPLOAD:
    search_client.upload_documents(documents=all_docs)
    print("Uploaded ALL SOPs")

# --- QUERY LOOP ---
while True:
    query = input("\nAsk a question (or type 'exit'): ")

    if query.lower() == "exit":
        break

    # 1. Create query embedding
    query_embedding = client.embeddings.create(
        model="text-embedding-3-large",
        input=query
    ).data[0].embedding

    # 2. VECTOR SEARCH
    results = search_client.search(
        search_text=None,
        vector={
            "value": query_embedding,
            "fields": "text_vector",
            "k": 3
        }
    )

    docs = []
    for r in results:
        docs.append(r["chunk"])

    if not docs:
        print("No results found.")
        continue

    context = "\n\n".join(docs)

    # 3. GPT RESPONSE
    response = client.chat.completions.create(
        model="gpt-35-turbo",
        messages=[
            {"role": "system", "content": "Answer ONLY using SOP data."},
            {"role": "user", "content": query},
            {"role": "assistant", "content": context}
        ]
    )

    print("\nANSWER:\n")
    print(response.choices[0].message.content)