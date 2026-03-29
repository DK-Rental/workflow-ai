"""
ISSP Strata Workflow Assistant - Test Suite
BCIT CIT - Milestone 3 Testing
"""

import pytest
from unittest.mock import patch, MagicMock



# SECTION 1: APP FACTORY & HEALTH CHECK


class TestAppFactory:
    """Tests for app.py - Flask app creation."""

    def test_create_app_returns_flask_instance(self):
        """App factory should return a valid Flask app."""
        from app import create_app
        app = create_app()
        assert app is not None

    def test_app_has_blueprint_registered(self):
        """The 'api' blueprint should be registered."""
        from app import create_app
        app = create_app()
        assert "api" in app.blueprints

    def test_healthz_returns_200(self):
        """GET /healthz should return HTTP 200 and status ok."""
        from app import create_app
        client = create_app().test_client()
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}

    def test_root_redirects_to_chat(self):
        """GET / should redirect to /chat."""
        from app import create_app
        client = create_app().test_client()
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/chat" in resp.headers["Location"]


# SECTION 2: ROUTE HELPERS


class TestRouteHelpers:
    """Unit tests for private helper functions in routes.py."""

    def test_is_file_lookup_with_file_keyword(self):
        from routes import _is_file_lookup
        assert _is_file_lookup("find the lease file") is True

    def test_is_file_lookup_with_pdf(self):
        from routes import _is_file_lookup
        assert _is_file_lookup("show me the insurance pdf") is True

    def test_is_file_lookup_with_where_is(self):
        from routes import _is_file_lookup
        assert _is_file_lookup("where is the AGM document") is True

    def test_is_file_lookup_false_for_general_question(self):
        from routes import _is_file_lookup
        assert _is_file_lookup("what is the late fee policy") is False

    def test_is_email_question_true(self):
        from routes import _is_email_question
        assert _is_email_question("check my inbox for renewal notices") is True

    def test_is_email_question_with_message(self):
        from routes import _is_email_question
        assert _is_email_question("did we get a message about the leak") is True

    def test_is_email_question_false(self):
        from routes import _is_email_question
        assert _is_email_question("what is the move-in procedure") is False

    def test_friendly_location_with_root_path(self):
        from routes import _friendly_location
        result = _friendly_location("root:/Documents/Leases")
        assert "OneDrive" in result
        assert "Documents/Leases" in result

    def test_friendly_location_empty(self):
        from routes import _friendly_location
        result = _friendly_location("")
        assert result == "OneDrive root"

    def test_friendly_location_none(self):
        from routes import _friendly_location
        result = _friendly_location(None)
        assert result == "OneDrive root"


# SECTION 3: CHAT API - POST /chat


class TestChatEndpoint:
    """Integration tests for POST /chat route."""

    def setup_method(self):
        from app import create_app
        self.client = create_app().test_client()

    def test_missing_question_returns_400(self):
        """Empty payload should return 400."""
        resp = self.client.post("/chat", json={})
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_empty_question_string_returns_400(self):
        """Empty string question should return 400."""
        resp = self.client.post("/chat", json={"user_question": ""})
        assert resp.status_code == 400

    @patch("routes.find_files_in_onedrive")
    def test_file_search_mode_returns_files(self, mock_files):
        """File keyword question should trigger file_search mode."""
        mock_files.return_value = [
            {"name": "lease.pdf", "webUrl": "https://example.com/lease.pdf", "parentPath": "root:/Leases"}
        ]
        resp = self.client.post("/chat", json={"user_question": "find the lease pdf"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["mode"] == "file_search"
        assert len(data["evidence"]["files"]) == 1

    @patch("routes.find_files_in_onedrive")
    def test_file_search_no_results(self, mock_files):
        """File search with no results should return empty evidence."""
        mock_files.return_value = []
        resp = self.client.post("/chat", json={"user_question": "find the document"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["mode"] == "file_search"
        assert "No files found" in data["answer"]

    @patch("routes._SOP_OK", True)
    @patch("routes.ask_sop_ai")
    def test_sop_mode_returns_answer(self, mock_sop):
        """Non-file, non-email question should use SOP RAG mode."""
        mock_sop.return_value = {
            "answer": "Step 1: Notify the strata council.",
            "evidence": {"docs": ["SOP doc 1"]}
        }
        resp = self.client.post("/chat", json={"user_question": "what is the move-in procedure"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["mode"] == "workflow_sop"
        assert "answer" in data

    @patch("routes._SOP_OK", False)
    @patch("routes.ask_llm")
    def test_fallback_llm_when_sop_disabled(self, mock_llm):
        """When SOP service is down, fallback LLM should respond."""
        mock_llm.return_value = "I'm not sure, please contact the strata manager."
        resp = self.client.post("/chat", json={"user_question": "what is the late fee"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["mode"] == "fallback_llm"

    @patch("routes.get_recent_emails_for_context")
    @patch("routes.ask_llm")
    @patch("routes._SOP_OK", False)
    def test_email_mode_triggered_by_keyword(self, mock_llm, mock_emails):
        """'inbox' keyword should trigger email_context mode."""
        mock_emails.return_value = [
            {"subject": "AGM Notice", "sender": "manager@strata.com",
             "receivedDateTime": "2025-03-01", "bodyPreview": "Annual General Meeting..."}
        ]
        mock_llm.return_value = "There is an AGM notice in your inbox."
        resp = self.client.post("/chat", json={"user_question": "check inbox for AGM"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["mode"] == "email_context"
        assert "emails" in data["evidence"]

    def test_chat_accepts_alternate_key_question(self):
        """'question' key should work as an alternative to 'user_question'."""
        with patch("routes._SOP_OK", False), patch("routes.ask_llm", return_value="ok"):
            resp = self.client.post("/chat", json={"question": "hello"})
            assert resp.status_code == 200

    def test_teams_bot_endpoint_no_text(self):
        """POST /api/messages with no text should return a no-input message."""
        resp = self.client.post("/api/messages", json={})
        data = resp.get_json()
        assert resp.status_code == 200
        assert "No input received" in data["text"]

    @patch("routes.ask_llm")
    def test_teams_bot_endpoint_with_text(self, mock_llm):
        """POST /api/messages with text should return a response."""
        mock_llm.return_value = "Here is the answer."
        resp = self.client.post("/api/messages", json={"text": "what is the levy?"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["type"] == "message"
        assert data["text"] == "Here is the answer."


# SECTION 4: LLM CLIENT


class TestLLMClient:
    """Unit tests for llm_client.py logic."""

    def test_is_sop_question_tenant(self):
        from services.llm_client import _is_sop_question
        assert _is_sop_question("how do I process a tenant application") is True

    def test_is_sop_question_lease(self):
        from services.llm_client import _is_sop_question
        assert _is_sop_question("where do I find the lease agreement") is True

    def test_is_sop_question_false(self):
        from services.llm_client import _is_sop_question
        assert _is_sop_question("what is the weather today") is False

    def test_is_email_question_true(self):
        from services.llm_client import _is_email_question
        assert _is_email_question("did anyone reply to the email") is True

    def test_is_email_question_false(self):
        from services.llm_client import _is_email_question
        assert _is_email_question("what is the move-in checklist") is False

    def test_format_emails_no_emails(self):
        from services.llm_client import _format_emails_for_context
        result = _format_emails_for_context(None)
        assert "No recent emails" in result

    def test_format_emails_with_data(self):
        from services.llm_client import _format_emails_for_context
        emails = [
            {
                "subject": "Leak Report",
                "sender": {"emailAddress": {"name": "John", "address": "john@strata.com"}},
                "receivedDateTime": "2025-03-01T10:00:00Z",
                "bodyPreview": "There is a leak on floor 3."
            }
        ]
        result = _format_emails_for_context(emails)
        assert "Leak Report" in result
        assert "John" in result

    def test_format_emails_respects_max_limit(self):
        from services.llm_client import _format_emails_for_context
        emails = [{"subject": f"Email {i}", "sender": {}, "receivedDateTime": "", "bodyPreview": ""}
                  for i in range(10)]
        result = _format_emails_for_context(emails, max_emails=3)
        assert result.count("Subject:") == 3

    @patch("services.llm_client.client")
    def test_ask_llm_returns_string(self, mock_client):
        from services.llm_client import ask_llm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "This is the answer."
        mock_client.chat.completions.create.return_value = mock_response
        result = ask_llm("what is the late fee?", emails=[])
        assert isinstance(result, str)
        assert result == "This is the answer."

    @patch("services.llm_client.client")
    def test_ask_llm_with_email_context_for_email_question(self, mock_client):
        from services.llm_client import ask_llm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Found relevant email."
        mock_client.chat.completions.create.return_value = mock_response
        emails = [{"subject": "Notice", "sender": {}, "receivedDateTime": "", "bodyPreview": "Important"}]
        result = ask_llm("check my inbox for notices", emails=emails)
        call_args = mock_client.chat.completions.create.call_args
        user_msg = call_args[1]["messages"][1]["content"]
        assert "Notice" in user_msg  # email context should be injected

    @patch("services.llm_client.client")
    def test_ask_llm_no_email_context_for_non_email_question(self, mock_client):
        from services.llm_client import ask_llm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "SOP answer."
        mock_client.chat.completions.create.return_value = mock_response
        emails = [{"subject": "Notice", "sender": {}, "receivedDateTime": "", "bodyPreview": "Data"}]
        result = ask_llm("what is the move-in procedure?", emails=emails)
        call_args = mock_client.chat.completions.create.call_args
        user_msg = call_args[1]["messages"][1]["content"]
        assert "No email context" in user_msg  # should NOT inject emails for non-email questions



# SECTION 5: GRAPH CLIENT - TOKEN & EMAIL


class TestGraphClientAuth:
    """Tests for graph_client.py auth and email helpers."""

    @patch("services.graph_client.requests.post")
    def test_get_graph_token_success(self, mock_post):
        from services.graph_client import get_graph_token
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fake-token-abc"}
        )
        mock_post.return_value.raise_for_status = lambda: None
        token = get_graph_token()
        assert token == "fake-token-abc"

    @patch("services.graph_client.SHARED_MAILBOX", "")
    def test_get_recent_emails_no_mailbox_returns_empty(self):
        from services.graph_client import get_recent_emails_for_context
        result = get_recent_emails_for_context("any question")
        assert result == []

    @patch("services.graph_client.get_graph_token", return_value="tok")
    @patch("services.graph_client.requests.get")
    @patch("services.graph_client.SHARED_MAILBOX", "shared@strata.com")
    def test_get_recent_emails_returns_simplified_list(self, mock_get, mock_token):
        from services.graph_client import get_recent_emails_for_context
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "value": [
                    {
                        "subject": "AGM Reminder",
                        "from": {"emailAddress": {"name": "Manager"}},
                        "receivedDateTime": "2025-03-10T08:00:00Z",
                        "bodyPreview": "Please attend the AGM."
                    }
                ]
            }
        )
        result = get_recent_emails_for_context("AGM")
        assert len(result) == 1
        assert result[0]["subject"] == "AGM Reminder"

    @patch("services.graph_client.SHARED_MAILBOX", "")
    def test_search_shared_mailbox_no_mailbox_returns_empty(self):
        from services.graph_client import search_shared_mailbox_messages
        result = search_shared_mailbox_messages("insurance")
        assert result == []

    @patch("services.graph_client.get_graph_token", return_value="tok")
    @patch("services.graph_client.requests.get")
    @patch("services.graph_client.SHARED_MAILBOX", "shared@strata.com")
    def test_search_shared_mailbox_returns_results(self, mock_get, mock_token):
        from services.graph_client import search_shared_mailbox_messages
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "value": [
                    {
                        "subject": "Insurance Renewal",
                        "from": {},
                        "receivedDateTime": "2025-03-05",
                        "bodyPreview": "Renew by April.",
                        "webLink": "https://outlook.com/mail/123"
                    }
                ]
            }
        )
        result = search_shared_mailbox_messages("insurance")
        assert len(result) == 1
        assert result[0]["subject"] == "Insurance Renewal"



# SECTION 6: GRAPH CLIENT - ONEDRIVE


class TestGraphClientOneDrive:
    """Tests for OneDrive-related functions in graph_client.py."""

    def test_extract_strong_tokens_removes_stopwords(self):
        from services.graph_client import _extract_strong_tokens
        tokens = _extract_strong_tokens(["where is the lease file"])
        assert "where" not in tokens
        assert "is" not in tokens
        assert "file" not in tokens
        assert "lease" in tokens

    def test_extract_strong_tokens_keeps_numeric_tokens(self):
        from services.graph_client import _extract_strong_tokens
        tokens = _extract_strong_tokens(["find b-1209 renewal"])
        assert "b-1209" in tokens

    def test_extract_strong_tokens_empty_query(self):
        from services.graph_client import _extract_strong_tokens
        tokens = _extract_strong_tokens([])
        assert tokens == []

    @patch("services.graph_client.get_graph_token", return_value="tok")
    @patch("services.graph_client.requests.get")
    def test_resolve_user_drive_id_success(self, mock_get, mock_token):
        from services.graph_client import resolve_user_drive_id
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "drive-id-123"}
        )
        mock_get.return_value.raise_for_status = lambda: None
        result = resolve_user_drive_id("user@strata.com")
        assert result == "drive-id-123"

    def test_resolve_user_drive_id_raises_on_empty_upn(self):
        from services.graph_client import resolve_user_drive_id
        with pytest.raises(ValueError):
            resolve_user_drive_id("")

    @patch("services.graph_client.ONEDRIVE_DRIVE_ID", "hardcoded-drive-id")
    def test_get_user_drive_id_prefers_env_var(self):
        from services.graph_client import get_user_drive_id
        result = get_user_drive_id()
        assert result == "hardcoded-drive-id"

    @patch("services.graph_client.get_graph_token", return_value="tok")
    @patch("services.graph_client.requests.get")
    @patch("services.graph_client.ONEDRIVE_DRIVE_ID", "drive-abc")
    def test_find_files_returns_matching_results(self, mock_get, mock_token):
        from services.graph_client import find_files_in_onedrive
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "value": [
                    {
                        "id": "file-001",
                        "name": "lease_agreement.pdf",
                        "webUrl": "https://onedrive.com/lease_agreement.pdf",
                        "size": 12345,
                        "lastModifiedDateTime": "2025-01-01",
                        "parentReference": {"path": "root:/Leases"}
                    }
                ]
            }
        )
        mock_get.return_value.raise_for_status = lambda: None
        results = find_files_in_onedrive(["lease"], exts=[".pdf"])
        assert len(results) >= 1
        assert results[0]["name"] == "lease_agreement.pdf"

    @patch("services.graph_client.get_graph_token", return_value="tok")
    @patch("services.graph_client.requests.get")
    @patch("services.graph_client.ONEDRIVE_DRIVE_ID", "drive-abc")
    def test_find_files_filters_by_extension(self, mock_get, mock_token):
        from services.graph_client import find_files_in_onedrive
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "value": [
                    {"id": "1", "name": "lease.pdf", "webUrl": "", "size": 0,
                     "lastModifiedDateTime": "", "parentReference": {"path": "root:/Leases"}},
                    {"id": "2", "name": "notes.txt", "webUrl": "", "size": 0,
                     "lastModifiedDateTime": "", "parentReference": {"path": "root:/Notes"}}
                ]
            }
        )
        mock_get.return_value.raise_for_status = lambda: None
        results = find_files_in_onedrive(["lease"], exts=[".pdf"])
        names = [r["name"] for r in results]
        assert "notes.txt" not in names


# SECTION 7: AI SEARCH (SOP RAG)


class TestAISearch:
    """Unit tests for ai_search.py (SOP RAG pipeline)."""

    @patch("services.ai_search.client")
    @patch("services.ai_search.search_client")
    def test_ask_ai_returns_answer_dict(self, mock_search, mock_openai):
        from services.ai_search import ask_ai

        mock_openai.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1] * 10)]
        )
        mock_search.search.return_value = [{"chunk": "Step 1: Verify lease."}]
        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Verify the lease document."))]
        )

        result = ask_ai("how do I process a new tenant lease?")
        assert isinstance(result, dict)
        assert "answer" in result
        assert result["answer"] == "Verify the lease document."

    @patch("services.ai_search.client")
    @patch("services.ai_search.search_client")
    def test_ask_ai_no_docs_returns_no_sop_found(self, mock_search, mock_openai):
        from services.ai_search import ask_ai

        mock_openai.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.0] * 10)]
        )
        mock_search.search.return_value = []  # No docs returned

        result = ask_ai("something completely unrelated")
        assert result["answer"] == "No relevant SOP found."

    @patch("services.ai_search.client")
    @patch("services.ai_search.search_client")
    def test_ask_ai_includes_evidence(self, mock_search, mock_openai):
        from services.ai_search import ask_ai

        mock_openai.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.2] * 10)]
        )
        mock_search.search.return_value = [
            {"chunk": "SOP Step A"},
            {"chunk": "SOP Step B"},
        ]
        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Do Step A then Step B."))]
        )

        result = ask_ai("move-in checklist")
        assert "evidence" in result
        assert isinstance(result["evidence"], list)
        assert len(result["evidence"]) >= 1



# SECTION 8: CONFIG


class TestConfig:
    """Tests for config.py environment variable loading."""

    def test_config_variables_are_strings(self):
        import config
        for var in [config.TENANT_ID, config.CLIENT_ID, config.CLIENT_SECRET,
                    config.AZURE_OPENAI_ENDPOINT, config.AZURE_OPENAI_API_KEY,
                    config.AZURE_OPENAI_DEPLOYMENT]:
            assert isinstance(var, str)

    def test_system_prompt_is_non_empty(self):
        from config import SYSTEM_PROMPT
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT) > 0

    def test_system_prompt_contains_strata(self):
        from config import SYSTEM_PROMPT
        assert "Strata" in SYSTEM_PROMPT

    @patch.dict("os.environ", {"TENANT_ID": "test-tenant"})
    def test_env_variable_overrides_default(self):
        import importlib
        import config as cfg
        importlib.reload(cfg)
        assert cfg.TENANT_ID == "test-tenant"



# SECTION 9: EDGE CASE & SECURITY TESTS


class TestEdgeCases:
    """Edge case and basic security tests."""

    def setup_method(self):
        from app import create_app
        self.client = create_app().test_client()

    def test_chat_with_very_long_input(self):
        """App should handle a very long question without crashing."""
        long_q = "what is the policy " * 500
        with patch("routes._SOP_OK", False), patch("routes.ask_llm", return_value="ok"):
            resp = self.client.post("/chat", json={"user_question": long_q})
            assert resp.status_code == 200

    def test_chat_with_special_characters(self):
        """App should handle questions with special characters."""
        with patch("routes._SOP_OK", False), patch("routes.ask_llm", return_value="ok"):
            resp = self.client.post("/chat", json={"user_question": "<script>alert('xss')</script>"})
            assert resp.status_code == 200

    def test_chat_with_non_json_body_returns_400(self):
        """Non-JSON body should result in a 400 or graceful error."""
        resp = self.client.post("/chat", data="not json", content_type="text/plain")
        assert resp.status_code in [400, 500]

    def test_healthz_is_always_available(self):
        """Health check should never require auth or return 5xx."""
        resp = self.client.get("/healthz")
        assert resp.status_code == 200

    def test_unknown_route_returns_404(self):
        """Unknown routes should return 404."""
        resp = self.client.get("/nonexistent-route-xyz")
        assert resp.status_code == 404

    @patch("routes.find_files_in_onedrive")
    def test_file_search_exception_handled_gracefully(self, mock_files):
        """If OneDrive search throws, app should still return a response."""
        mock_files.side_effect = Exception("OneDrive timeout")
        resp = self.client.post("/chat", json={"user_question": "find the document"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["mode"] == "file_search"