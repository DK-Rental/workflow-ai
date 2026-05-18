import asyncio
import logging
from typing import Dict, List

from flask import Flask, Response, request
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity

import config
from services.ai_search import ask_ai

app = Flask(__name__)

# ── Adapter ───────────────────────────────────────────────────────────────────
SETTINGS = BotFrameworkAdapterSettings(config.TEAMS_APP_ID, config.TEAMS_APP_PASSWORD)
ADAPTER  = BotFrameworkAdapter(SETTINGS)

# ── Per-conversation history ──────────────────────────────────────────────────
_history: Dict[str, List[Dict[str, str]]] = {}
_MAX_TURNS = 10


# ── AI helper ─────────────────────────────────────────────────────────────────
def _call_ai(question: str, history: list) -> str:
    """Call vector RAG and format response with source titles."""
    result  = ask_ai(question,history)
    answer  = result.get("answer", "I'm having trouble accessing the SOPs right now.")
    sources = result.get("sources", [])

    if sources:
        seen   = []
        titles = []
        for s in sources:
            t = s.get("title", "")
            if t and t not in seen:
                seen.append(t)
                blob_url = f"https://dksopstorage123.blob.core.windows.net/sop/{t}"
                titles.append(f"- [{t}]({blob_url})")
        if titles:
            answer += "\n\n**Sources:**\n" + "\n".join(titles)

    return answer


# ── Bot logic ─────────────────────────────────────────────────────────────────
async def on_turn(turn_context: TurnContext):
    if turn_context.activity.type != "message":
        return

    raw_text          = (turn_context.activity.text or "").strip()
    conversation_type = turn_context.activity.conversation.conversation_type
    conv_id           = turn_context.activity.conversation.id
    history           = _history.setdefault(conv_id, [])

    # ── Private chat: Joe answers everything ──────────────────────────────────
    if conversation_type == "personal":
        TurnContext.remove_recipient_mention(turn_context.activity)
        question = (turn_context.activity.text or "").strip()

        if not question:
            return

        logging.info("[PRIVATE CHAT] conv=%s q=%s", conv_id, question)
        answer = _call_ai(question, history)

        history.append({"role": "user",      "content": question})
        history.append({"role": "assistant", "content": answer})

        await turn_context.send_activity(answer)

    # ── Group chat: only respond when mentioned or #Joe ───────────────────────
    else:
        has_native_mention = False
        if turn_context.activity.entities:
            for entity in turn_context.activity.entities:
                if (entity.type == "mention" and
                        entity.mentioned.id == turn_context.activity.recipient.id):
                    has_native_mention = True
                    break

        has_hashtag = raw_text.lower().startswith("#joe")

        if has_native_mention or has_hashtag:
            TurnContext.remove_recipient_mention(turn_context.activity)
            question = (turn_context.activity.text or "").strip()

            if has_hashtag:
                question = question[4:].strip()

            if not question:
                return

            logging.info("[GROUP CHAT] Joe summoned! conv=%s q=%s", conv_id, question)
            answer = _call_ai(question, history)

            history.append({"role": "user",      "content": question})
            history.append({"role": "assistant", "content": answer})

            await turn_context.send_activity(answer)

        else:
            history.append({"role": "user", "content": f"Team context: {raw_text}"})

    # Trim history
    if len(history) > _MAX_TURNS * 2:
        _history[conv_id] = history[-(_MAX_TURNS * 2):]


# ── Flask route ───────────────────────────────────────────────────────────────
@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    activity    = Activity().deserialize(request.json)
    auth_header = request.headers.get("Authorization", "")

    asyncio.run(ADAPTER.process_activity(activity, auth_header, on_turn))

    return Response(status=200)


if __name__ == "__main__":
    app.run(port=3978)