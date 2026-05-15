function renderMarkdownSafe(text) {
  if (!text) return "";
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  return escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`([^`]+?)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderEvidence(container, evidence) {
  container.innerHTML = "";
  if (!evidence) return;

  let html = "";

  // ----------------------------
  // EMAILS
  // ----------------------------
  const emails = evidence.emails || evidence.emails_checked || [];
  if (emails.length > 0) {
    html += `<div class="section-title">Emails</div>`;
    html += emails.map((e) => {
      const sender =
        e?.sender?.emailAddress?.address ||
        e?.sender?.emailAddress?.name ||
        "Unknown";

      const subject = e?.subject || "(no subject)";
      const preview = e?.bodyPreview || "";
      const when = fmtDate(e?.receivedDateTime);

      return `
        <div class="card">
          <div class="meta">${when} • ${sender}</div>
          <div><strong>${subject}</strong></div>
          <div>${preview ? renderMarkdownSafe(preview) : ""}</div>
        </div>
      `;
    }).join("");
  }

  // ----------------------------
  // FILES (FIXED)
  // ----------------------------
  const files = evidence.files || evidence.onedrive_checked || [];
  if (files.length > 0) {
    html += `<div class="section-title">Files</div>`;
    html += files.map((f) => {
      const name = f?.name || "(unnamed)";
      const location = f?.location || "";
      const link = f?.url || f?.webUrl;

      return `
        <div class="card">
          <div><strong>${name}</strong></div>
          <div class="meta">${location}</div>
          <div>
            ${link ? `<a href="${link}" target="_blank">Open</a>` : ""}
          </div>
        </div>
      `;
    }).join("");
  }

  // ----------------------------
  // SOP / DOCUMENT SOURCES
  // ----------------------------
  const docs = evidence.docs || [];
  if (docs.length > 0) {
    html += `<div class="section-title">Sources</div>`;
    html += docs.map((d, i) => {
      return `<div class="card">[doc${i + 1}] ${renderMarkdownSafe(d)}</div>`;
    }).join("");
  }

  container.innerHTML = html || "<div>No evidence available</div>";
}

// ----------------------------
// DOM ELEMENTS
// ----------------------------
const form = document.getElementById("chat-form");
const promptEl = document.getElementById("prompt");
const statusEl = document.getElementById("status");
const chatLogEl = document.getElementById("chat-log");
const evidenceEl = document.getElementById("evidence");
const sendBtn = document.getElementById("send");

// ----------------------------
// MODE SWITCH
// ----------------------------
function ensureChatMode() {
  if (document.body.classList.contains("empty")) {
    document.body.classList.remove("empty");
    document.body.classList.add("chat-mode");
  }
}

// ----------------------------
// CHAT HELPERS
// ----------------------------
function appendMessage(role, text) {
  if (!text) return;

  const div = document.createElement("div");
  div.classList.add("msg");
  div.classList.add(role === "user" ? "user" : "bot");

  div.innerHTML = renderMarkdownSafe(text);
  chatLogEl.appendChild(div);

  chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

async function postChat(userQuestion) {
  return fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_question: userQuestion }),
  });
}

// ----------------------------
// SEND BUTTON STATE
// ----------------------------
function updateSendState() {
  const hasText = promptEl.value.trim().length > 0;

  if (hasText) {
    sendBtn.disabled = false;
    sendBtn.textContent = "➤";
    sendBtn.classList.add("active");
  } else {
    sendBtn.disabled = true;
    sendBtn.textContent = "D";
    sendBtn.classList.remove("active");
  }
}

promptEl.addEventListener("input", updateSendState);
updateSendState();

// ----------------------------
// ENTER HANDLING
// ----------------------------
promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

// ----------------------------
// SUBMIT HANDLER
// ----------------------------
form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const userQuestion = promptEl.value.trim();
  if (!userQuestion) return;

  ensureChatMode();
  appendMessage("user", userQuestion);

  sendBtn.disabled = true;
  statusEl.textContent = "Sending…";
  evidenceEl.innerHTML = "";

  try {
    const res = await postChat(userQuestion);
    const data = await res.json().catch(() => ({}));

    statusEl.textContent = res.ok ? "Done" : `HTTP ${res.status}`;

    const botText = data.answer || data.error || "";
    appendMessage("bot", botText);

    renderEvidence(evidenceEl, data.evidence);

  } catch (err) {
    statusEl.textContent = "Error";
    appendMessage("bot", String(err));
  } finally {
    promptEl.value = "";
    promptEl.focus();
    updateSendState();
  }
});