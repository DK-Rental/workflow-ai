from flask import Flask, request, jsonify
from flasgger import Swagger
import json

from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
import os

app = Flask(__name__)
swagger = Swagger(app)


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

blob_service_client = BlobServiceClient.from_connection_string(
    os.getenv("AZURE_STORAGE_CONNECTION_STRING")
)


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


# ---------------------------
# API: INDEX SOPs (UPLOAD)
# ---------------------------
@app.route("/index", methods=["POST"])
def index_sops():
    """
    Index all SOPs from Blob Storage
    ---
    responses:
      200:
        description: SOPs indexed successfully
    """
    all_docs = []

    for blob in container_client.list_blobs():
        if not blob.name.endswith(".json"):
            continue

        blob_client = container_client.get_blob_client(blob.name)
        content = blob_client.download_blob().readall()
        data = json.loads(content)

        text = build_embedding_text(data)

        embedding = client.embeddings.create(
            model="text-embedding-3-large",
            input=text
        )

        clean_id = blob.name.replace("/", "_").replace(".json", "")

        doc = {
            "id": clean_id,
            "title": data.get("title", ""),
            "content": text,
            "embedding": embedding.data[0].embedding
        }

        all_docs.append(doc)

    search_client.upload_documents(documents=all_docs)

    return jsonify({"message": "All SOPs indexed successfully"})


# ---------------------------
# API: ASK QUESTION
# ---------------------------
@app.route("/ask", methods=["POST"])
def ask():
    """
    Ask SOP question
    ---
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            question:
              type: string
              example: How do I evict a tenant?
    responses:
      200:
        description: Answer from SOP system
    """
    data = request.get_json()
    query = data.get("question")

    results = search_client.search(
        search_text=query,
        top=3
    )

    best_result = None
    for r in results:
        best_result = r
        break

    if not best_result:
        return jsonify({"answer": "No result found"})

    response = client.chat.completions.create(
        model="gpt-35-turbo",
        messages=[
            {"role": "system", "content": "Answer using SOP"},
            {"role": "user", "content": query},
            {"role": "assistant", "content": best_result["content"]}
        ]
    )

    return jsonify({
        "answer": response.choices[0].message.content
    })


# ---------------------------
# RUN APP
# ---------------------------
if __name__ == "__main__":
    app.run(debug=True)