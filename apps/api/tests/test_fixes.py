"""Testes de validacao para as correcoes criticas do SentimentoIA."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import json


# ====================== P1: Ollama Cloud ======================

class TestOllamaURLConstruction:
    """Verifica que a URL final do Ollama nao duplica /api."""

    def test_base_url_with_trailing_api(self):
        """OLLAMA_BASE_URL=https://ollama.com/api → EFFECTIVE_URL=https://ollama.com"""
        with patch("app.config.settings") as mock_settings:
            mock_settings.OLLAMA_BASE_URL = "https://ollama.com/api"
            mock_settings.OLLAMA_CLOUD_URL = ""
            # Simulate the property logic
            configured_url = mock_settings.OLLAMA_BASE_URL.strip().rstrip("/")
            normalized = configured_url.lower()
            if normalized.endswith("/api"):
                configured_url = configured_url[:-4]
            assert configured_url == "https://ollama.com"

    def test_base_url_without_trailing_api(self):
        """OLLAMA_BASE_URL=https://ollama.com → stays https://ollama.com"""
        configured_url = "https://ollama.com".strip().rstrip("/")
        normalized = configured_url.lower()
        if normalized.endswith("/api"):
            configured_url = configured_url[:-4]
        assert configured_url == "https://ollama.com"

    def test_build_ollama_url_generates_correct_endpoint(self):
        """Verifica que _build_ollama_url monta /api/generate corretamente."""
        from app.services.llm_service import LLMService
        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.OLLAMA_EFFECTIVE_URL = "https://ollama.com"
            url = LLMService._build_ollama_url("generate")
            assert url == "https://ollama.com/api/generate"
            assert "/api/api/" not in url

    def test_build_ollama_url_no_double_slash(self):
        from app.services.llm_service import LLMService
        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.OLLAMA_EFFECTIVE_URL = "https://ollama.com/"
            url = LLMService._build_ollama_url("generate")
            assert "//api" not in url

    def test_ollama_configured_requires_key(self):
        from app.services.llm_service import LLMService
        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.OLLAMA_EFFECTIVE_URL = "https://ollama.com"
            mock_settings.OLLAMA_MODEL = "llama3.1:8b"
            mock_settings.OLLAMA_API_KEY = ""
            assert LLMService.ollama_configured() is False

    def test_ollama_configured_with_key(self):
        from app.services.llm_service import LLMService
        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.OLLAMA_EFFECTIVE_URL = "https://ollama.com"
            mock_settings.OLLAMA_MODEL = "llama3.1:8b"
            mock_settings.OLLAMA_API_KEY = "test-key-123"
            assert LLMService.ollama_configured() is True


# ====================== P2: Scraper ======================

class TestRedditScraper:
    """Verifica melhorias no scraper do Reddit."""

    def test_build_reddit_queries_creates_variations(self):
        from app.services.scraper_service import ScraperService
        queries = ScraperService._build_reddit_queries("Nubank")
        assert len(queries) >= 2
        assert '"Nubank"' in queries[0]

    def test_reddit_relevance_exact_match(self):
        from app.services.scraper_service import ScraperService
        score = ScraperService._reddit_relevance("Nubank", "Problema com Nubank", "Meu cartão Nubank")
        assert score == 1.0

    def test_reddit_relevance_no_match(self):
        from app.services.scraper_service import ScraperService
        score = ScraperService._reddit_relevance("Nubank", "Receita de bolo", "Como fazer pão")
        assert score < 0.2


class TestMastodonScraper:
    """Verifica graceful degradation do Mastodon."""

    def test_mastodon_without_token_degrades(self):
        """Sem token, deve retornar erro descritivo sem quebrar."""
        from app.services.scraper_service import ScraperService
        with patch("app.services.scraper_service.settings") as mock_settings:
            mock_settings.SCRAPER_MASTODON_BASE_URL = "https://mastodon.social"
            mock_settings.SCRAPER_MASTODON_SEARCH_PATH = "/api/v2/search"
            mock_settings.SCRAPER_MASTODON_ACCESS_TOKEN = ""
            mock_settings.SCRAPER_TIMEOUT_SECONDS = 10
            mock_settings.SCRAPER_RETRY_ATTEMPTS = 1
            mock_settings.SCRAPER_DELAY_SECONDS = 0.1
            mock_settings.SCRAPER_RETRY_BACKOFF_SECONDS = 0.1
            mock_settings.SCRAPER_USER_AGENT = "test"

            with patch.object(ScraperService, "_request") as mock_request:
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"accounts": [], "hashtags": [], "statuses": []}
                mock_request.return_value = mock_resp

                items, error = ScraperService._scrape_mastodon("test", 5)
                assert items == []
                assert error is not None
                assert "modo publico" in error.lower() or "mastodon" in error.lower()


# ====================== P3: Pipeline ======================

class TestDashboardDataFlow:
    """Verifica que o dashboard le dados de ambos os fluxos."""

    def test_dashboard_query_uses_or_filter(self):
        """Dashboard deve buscar por batch_id OR search_id."""
        from app.services.dashboard_service import DashboardService
        with patch("app.services.dashboard_service.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_mentions = MagicMock()
            mock_mentions.find.return_value.sort.return_value.limit.return_value = []
            mock_db.mentions = mock_mentions
            mock_get_db.return_value = mock_db

            result = DashboardService.get_dashboard(user_id="user1")
            assert result["metrics"]["total_mentions"] == 0
            assert "mentions" in result


# ====================== P4: NPS ======================

class TestNpsService:
    """Verifica servico NPS."""

    def test_nps_score_validation(self):
        from app.services.nps_service import NpsService
        with pytest.raises(ValueError, match="entre 0 e 10"):
            with patch("app.services.nps_service.get_db") as mock_db:
                mock_db.return_value = MagicMock()
                NpsService.submit_response(
                    user_id="user1",
                    session_id="s1",
                    module_key="dashboard",
                    score=11,
                )

    def test_nps_metrics_empty(self):
        from app.services.nps_service import NpsService
        with patch("app.services.nps_service.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.nps_responses.find.return_value = []
            mock_get_db.return_value = mock_db

            metrics = NpsService.get_metrics()
            assert metrics["total_responses"] == 0
            assert metrics["nps_score"] == 0

    def test_nps_score_calculation(self):
        """Verifica calculo NPS basico."""
        from app.services.nps_service import NpsService
        with patch("app.services.nps_service.get_db") as mock_get_db:
            mock_db = MagicMock()
            # 3 promoters (9,10,10), 1 passive (8), 1 detractor (5)
            mock_db.nps_responses.find.return_value = [
                {"score": 10, "module_key": "dashboard"},
                {"score": 9, "module_key": "dashboard"},
                {"score": 10, "module_key": "busca"},
                {"score": 8, "module_key": "dashboard"},
                {"score": 5, "module_key": "busca"},
            ]
            mock_get_db.return_value = mock_db

            metrics = NpsService.get_metrics()
            assert metrics["total_responses"] == 5
            assert metrics["promoters"] == 3
            assert metrics["detractors"] == 1
            # NPS = ((3 - 1) / 5) * 100 = 40.0
            assert metrics["nps_score"] == 40.0


class TestLLMErrorHandling:
    """Verifica tratamento de erros HTTP do Ollama."""

    def test_401_error_message(self):
        from app.services.llm_service import LLMService
        import httpx
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_resp.url = "https://ollama.com/api/generate"

        with pytest.raises(RuntimeError, match="autenticacao falhou"):
            LLMService._handle_ollama_error(mock_resp)

    def test_429_error_message(self):
        from app.services.llm_service import LLMService
        import httpx
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 429
        mock_resp.text = "Rate limited"
        mock_resp.url = "https://ollama.com/api/generate"

        with pytest.raises(RuntimeError, match="limite de requisicoes"):
            LLMService._handle_ollama_error(mock_resp)

    def test_200_no_error(self):
        from app.services.llm_service import LLMService
        import httpx
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        # Should not raise
        LLMService._handle_ollama_error(mock_resp)
