from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
import os

# --- AZURE OPENAI CLIENT ---
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

def ask_ai(question: str):

    # 1. embedding
    query_embedding = client.embeddings.create(
        model="text-embedding-3-large",
        input=question
    ).data[0].embedding

    # 2. vector search
    results = search_client.search(
        search_text="",
        vector_queries=[
            {
                "vector": query_embedding,
                "k": 3,
                "fields": "text_vector"
            }
        ]
    )

    # 3. collect docs
    docs = [r["chunk"] for r in results if "chunk" in r]

    if not docs:
        return {"answer": "No relevant SOP found.", "evidence": {}}

    context = "\n\n".join(docs[:3])

    # 4. GPT
    response = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a property management assistant.\n"
                    "Answer using ONLY relevant SOP steps.\n"
                    "Do NOT summarize everything.\n"
                    "Give precise actionable steps."
                )
            },
            {
                "role": "user",
                "content": f"{question}\n\nSOP:\n{context}"
            }
        ]
    )

    return {
        "answer": response.choices[0].message.content,
        "evidence": docs
    }