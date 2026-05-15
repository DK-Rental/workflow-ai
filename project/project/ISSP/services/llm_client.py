import json
import logging
from openai import AzureOpenAI
import config

client = AzureOpenAI(
    azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
    api_key=config.AZURE_OPENAI_API_KEY,
    api_version=config.AZURE_OPENAI_API_VERSION,
)


def ask_llm(question: str, history: list = None) -> str:
    """
    Queries the LLM with Azure Search RAG enabled. Used for Teams chat.
    Returns a formatted string (Answer + Citations).
    """
    messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    extra_body = {
        "data_sources": [{
            "type": "azure_search",
            "parameters": {
                "endpoint": config.AZURE_SEARCH_ENDPOINT,
                "index_name": config.AZURE_SEARCH_INDEX,
                "authentication": {"type": "api_key", "key": config.AZURE_SEARCH_KEY},
                "in_scope": True,
                "top_n_documents": 3,
                "strictness": 3,
            }
        }]
    }

    try:
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            temperature=0.1,
            extra_body=extra_body,
            timeout=30,
        )
        
        message = response.choices[0].message
        answer = message.content or "I couldn't find an answer in the SOPs."
        
        
        citations = []
        if hasattr(message, "context") and isinstance(message.context, dict):
            citations = message.context.get("citations", [])
        elif hasattr(message, "model_extra") and isinstance(message.model_extra, dict):
            citations = message.model_extra.get("context", {}).get("citations", [])

        
        if citations:
            answer += "\n\n**Sources:**\n"
            for idx, cit in enumerate(citations, 1):
                title = cit.get("title", f"Document {idx}")
                answer += f"- {title}\n"

        return answer

    except Exception as e:
        logging.error("AI Error: %s", e)
        return "I'm having trouble accessing my files right now."


def generate_sop_from_transcript(transcript: str) -> dict:
    """
    Converts a raw transcript into a structured SOP dict.
    Calls the LLM directly — no RAG, no Azure Search.
    Returns: {"summary": str, "steps": [...], "key_points": [...]}
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert at converting spoken transcripts into clear, "
                "structured Standard Operating Procedures (SOPs). "
                "Always respond with valid JSON only — no markdown, no explanation."
            )
        },
        {
            "role": "user",
            "content": (
                "Convert the following transcript into a JSON SOP with exactly these keys:\n"
                "  - summary: a detailed paragraph summarizing what was discussed, including specific issues raised, decisions made, and context\n"
                "  - steps: a list of specific, actionable steps mentioned in the transcript — use the exact language, names, details, and examples from the transcript, not generic placeholders\n"
                "  - key_points: a list of specific warnings, deadlines, names, amounts, or important details explicitly mentioned\n\n"
                "Rules:\n"
                "  - Do NOT generalize or paraphrase into vague steps\n"
                "  - Preserve specific names, dates, dollar amounts, unit numbers, and any other concrete details from the transcript\n"
                "  - Each step should be a complete sentence describing exactly what was said or decided\n"
                "  - Aim for at least 5-10 steps and 3-5 key points\n\n"
                f"Transcript:\n{transcript}"
            )
        }
    ]

    try:
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            temperature=0.1,
            response_format={"type": "json_object"}, # Forces pure JSON output
            timeout=60,  
        )
        raw = response.choices[0].message.content or ""

        
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```", 2)[-1] if clean.count("```") >= 2 else clean
            clean = clean.removeprefix("json").strip().rstrip("`").strip()

        return json.loads(clean)

    except json.JSONDecodeError as e:
        logging.error("SOP JSON parse error: %s — raw: %s", e, raw[:200])
        # Return the raw text so at least something is stored
        return {"summary": raw, "steps": [], "key_points": []}

    except Exception as e:
        logging.error("SOP generation error: %s", e)
        raise RuntimeError(f"Failed to generate SOP: {e}")
    

def refine_sop(sop: dict, instruction: str) -> dict:
    """
    Take an existing SOP dict and an instruction string,
    return a refined SOP dict with the same structure.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert editor for Standard Operating Procedures. "
                "You will receive an existing SOP in JSON format and an instruction for how to improve it. "
                "Return ONLY the improved SOP as valid JSON with the same keys: summary, steps, key_points. "
                "No markdown, no explanation."
            )
        },
        {
            "role": "user",
            "content": (
                f"Instruction: {instruction}\n\n"
                f"Existing SOP:\n{json.dumps(sop, indent=2)}"
            )
        }
    ]

    try:
        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
            timeout=60,
        )
        raw = response.choices[0].message.content or ""
        return json.loads(raw.strip())

    except json.JSONDecodeError as e:
        logging.error("Refine JSON parse error: %s", e)
        return sop  # return original if parse fails

    except Exception as e:
        logging.error("SOP refine error: %s", e)
        raise RuntimeError(f"Failed to refine SOP: {e}")