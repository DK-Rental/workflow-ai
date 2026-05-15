import asyncio
import logging
from typing import Any, Dict, List

from flask import Flask, Response, request
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity

import config
from services.llm_client import ask_llm

app = Flask(__name__)

# ── Adapter ───────────────────────────────────────────────────────────────────
SETTINGS = BotFrameworkAdapterSettings(config.TEAMS_APP_ID, config.TEAMS_APP_PASSWORD)
ADAPTER  = BotFrameworkAdapter(SETTINGS)

# ── Per-conversation history ──────────────────────────────────────────────────
_history: Dict[str, List[Dict[str, str]]] = {}
_MAX_TURNS = 10  # keep last N user+assistant pairs


# ── Bot logic ─────────────────────────────────────────────────────────────────

async def on_turn(turn_context: TurnContext):
    # Only handle real user messages
    if turn_context.activity.type != "message":
        return

    raw_text = (turn_context.activity.text or "").strip()
    conversation_type = turn_context.activity.conversation.conversation_type
    conv_id = turn_context.activity.conversation.id
    history = _history.setdefault(conv_id, [])

    # 1. PRIVATE CHAT: Joe answers everything directly
    if conversation_type == "personal":
        TurnContext.remove_recipient_mention(turn_context.activity)
        question = (turn_context.activity.text or "").strip()
        
        if not question:
            return

        logging.info("[PRIVATE CHAT] conv=%s q=%s", conv_id, question)
        answer = ask_llm(question, history=history)

        history.append({"role": "user",      "content": question})
        history.append({"role": "assistant", "content": answer})
        
        await turn_context.send_activity(answer)

    
    else:
        
        has_native_mention = False
        if turn_context.activity.entities:
            for entity in turn_context.activity.entities:
                if entity.type == "mention" and entity.mentioned.id == turn_context.activity.recipient.id:
                    has_native_mention = True
                    break
        
        # Check if the user used the #Joe hashtag
        has_hashtag = raw_text.lower().startswith("#joe")

        if has_native_mention or has_hashtag:
            # Joe is being talked to! Strip the mentions to get the real question.
            TurnContext.remove_recipient_mention(turn_context.activity)
            question = (turn_context.activity.text or "").strip()
            
            if has_hashtag:
                question = question[4:].strip()

            if not question:
                return

            logging.info("[GROUP CHAT] Joe summoned! conv=%s q=%s", conv_id, question)
            answer = ask_llm(question, history=history)

            history.append({"role": "user",      "content": question})
            history.append({"role": "assistant", "content": answer})
            
            await turn_context.send_activity(answer)
            
        else:
            
            history.append({"role": "user", "content": f"Team context: {raw_text}"})

    # Global History Trimming
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