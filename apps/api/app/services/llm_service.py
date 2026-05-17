import json
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LLMService:
    """Servico de IA com foco oficial em Ollama Cloud."""

    @staticmethod
    def ollama_configured() -> bool:
        base_url = settings.OLLAMA_EFFECTIVE_URL.strip()
        return bool(base_url.startswith("https://") and settings.OLLAMA_MODEL and settings.OLLAMA_API_KEY.strip())

    @staticmethod
    def _ollama_headers() -> dict[str, str]:
        api_key = settings.OLLAMA_API_KEY.strip()
        if not api_key:
            raise RuntimeError("OLLAMA_API_KEY nao configurada")
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    @staticmethod
    def _build_ollama_url(endpoint: str) -> str:
        """Monta URL final do Ollama sem duplicar /api.

        OLLAMA_EFFECTIVE_URL ja retorna a base SEM trailing /api.
        Portanto, sempre concatena /api/<endpoint>.
        Exemplo: https://ollama.com + /api/generate = https://ollama.com/api/generate
        """
        base_url = settings.OLLAMA_EFFECTIVE_URL.rstrip("/")
        if not base_url:
            raise RuntimeError("OLLAMA_EFFECTIVE_URL nao configurada")
        return f"{base_url}/api/{endpoint.lstrip('/')}"

    @staticmethod
    def _handle_ollama_error(resp: httpx.Response) -> None:
        """Trata erros HTTP do Ollama com mensagens claras."""
        status = resp.status_code
        if status < 400:
            return

        # Trunca body para log, sem expor chaves
        body_preview = resp.text[:300] if resp.text else "(sem corpo)"

        if status == 401:
            logger.error("Ollama Cloud 401 Unauthorized. Verifique OLLAMA_API_KEY.")
            raise RuntimeError("Ollama Cloud: autenticacao falhou (401). Verifique OLLAMA_API_KEY.")
        elif status == 404:
            logger.error("Ollama Cloud 404 Not Found. URL: %s", resp.url)
            raise RuntimeError(f"Ollama Cloud: endpoint nao encontrado (404). URL: {resp.url}")
        elif status == 429:
            logger.warning("Ollama Cloud 429 Rate Limited. Aguarde antes de tentar novamente.")
            raise RuntimeError("Ollama Cloud: limite de requisicoes atingido (429). Tente novamente em instantes.")
        elif status >= 500:
            logger.error("Ollama Cloud %d Server Error. Body: %s", status, body_preview)
            raise RuntimeError(f"Ollama Cloud: erro no servidor ({status}). Servico pode estar instavel.")
        else:
            logger.error("Ollama Cloud HTTP %d. Body: %s", status, body_preview)
            raise RuntimeError(f"Ollama Cloud: erro HTTP {status}.")

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict[str, Any]:
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError("Ollama Cloud retornou payload nao-JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Ollama Cloud retornou payload invalido")
        return payload

    @staticmethod
    async def _call_ollama(prompt: str, model: str | None = None) -> dict[str, Any]:
        url = LLMService._build_ollama_url("generate")
        timeout = max(10, int(settings.OLLAMA_TIMEOUT_SECONDS))

        payload = {
            "model": model or settings.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.2,
            },
        }

        logger.info("Ollama Cloud request: POST %s model=%s timeout=%ds", url, payload["model"], timeout)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers=LLMService._ollama_headers(),
                json=payload,
            )
            LLMService._handle_ollama_error(resp)
            content = LLMService._safe_json(resp).get("response", "")
            logger.info("Ollama Cloud response OK (%d chars)", len(content))
            return LLMService._parse_json(content)

    @staticmethod
    async def _call_ollama_text(prompt: str, model: str | None = None) -> str:
        url = LLMService._build_ollama_url("generate")
        timeout = max(10, int(settings.OLLAMA_TIMEOUT_SECONDS))

        payload = {
            "model": model or settings.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.15,
            },
        }

        logger.info("Ollama Cloud text request: POST %s model=%s", url, payload["model"])

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers=LLMService._ollama_headers(),
                json=payload,
            )
            LLMService._handle_ollama_error(resp)
            return str(LLMService._safe_json(resp).get("response", "")).strip()

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        """Extrai JSON mesmo se o modelo devolver texto ao redor."""
        content = (content or "").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start : end + 1])
            raise ValueError("LLM nao retornou JSON valido") from exc

    @staticmethod
    def empty_analysis(reason: str) -> dict[str, Any]:
        return {
            "executive_summary": reason,
            "sentiment_overview": "indefinido",
            "risks": [],
            "opportunities": [],
            "recommended_actions": [],
            "decision_guidance": reason,
            "trend": "indefinido",
            "priority": "medium",
            "urgency": "medium",
            "root_cause": "indefinido",
            "recommended_action": "Revisar dados recentes e priorizar tratativas criticas.",
            "status": "open",
            "resolution": "pending",
        }

    @staticmethod
    def _normalize_analysis(data: dict[str, Any], reason_if_empty: str = "Analise vazia") -> dict[str, Any]:
        base = LLMService.empty_analysis(reason_if_empty)
        sentiment_overview = data.get("sentiment_overview")
        if isinstance(sentiment_overview, (dict, list)):
            sentiment_overview = json.dumps(sentiment_overview, ensure_ascii=False, indent=2)

        base.update({
            "executive_summary": str(data.get("executive_summary") or base["executive_summary"]),
            "sentiment_overview": str(sentiment_overview or base["sentiment_overview"]),
            "decision_guidance": str(data.get("decision_guidance") or base["decision_guidance"]),
            "trend": str(data.get("trend") or base["trend"]),
            "risks": data.get("risks") if isinstance(data.get("risks"), list) else [],
            "opportunities": data.get("opportunities") if isinstance(data.get("opportunities"), list) else [],
            "recommended_actions": data.get("recommended_actions")
            if isinstance(data.get("recommended_actions"), list)
            else [],
            "priority": str(data.get("priority") or base["priority"]),
            "urgency": str(data.get("urgency") or base["urgency"]),
            "root_cause": str(data.get("root_cause") or base["root_cause"]),
            "recommended_action": str(data.get("recommended_action") or base["recommended_action"]),
            "status": str(data.get("status") or base["status"]),
            "resolution": str(data.get("resolution") or base["resolution"]),
        })
        return base

    @staticmethod
    def _snapshot_prompt(snapshot: dict[str, Any]) -> str:
        locale = str(snapshot.get("locale") or "pt-BR")
        language_hint = "Português brasileiro" if locale == "pt-BR" else "English"
        return (
            "Use somente os dados fornecidos. Nao invente fatos. "
            "Seu foco e orientar tomada de decisao executiva. "
            "Responda no idioma especificado pelo contexto de usuario: "
            f"{language_hint}. "
            "Retorne SOMENTE JSON valido com as chaves: "
            "executive_summary, sentiment_overview, risks, opportunities, "
            "recommended_actions, decision_guidance, trend, priority, urgency, "
            "root_cause, recommended_action, status, resolution. "
            "O campo sentiment_overview deve ser um paragrafo conciso em linguagem natural, "
            "sem JSON interno, sem listas de metadados e sem estrutura de dicionario.\n\n"
            f"Contexto:\n{json.dumps(snapshot, ensure_ascii=False)}"
        )

    @staticmethod
    async def analyze_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        if not snapshot or int(snapshot.get("total_comments", 0)) <= 0:
            return LLMService.empty_analysis("Sem comentarios processados para gerar insight.")

        if not LLMService.ollama_configured():
            return LLMService.empty_analysis(
                "Ollama Cloud nao configurado. Verifique OLLAMA_BASE_URL, OLLAMA_API_KEY e OLLAMA_MODEL."
            )

        try:
            raw = await LLMService._call_ollama(LLMService._snapshot_prompt(snapshot))
            return LLMService._normalize_analysis(raw, reason_if_empty="Resposta LLM incompleta")
        except (httpx.HTTPError, httpx.TimeoutException, RuntimeError, ValueError) as exc:
            logger.exception("Falha no Ollama Cloud: %s", exc)
            return LLMService.empty_analysis("Falha temporaria na LLM. Insight nao disponivel no momento.")

    @staticmethod
    async def analyze_mentions(brand: str, mentions: list[dict[str, Any]]) -> dict[str, Any]:
        """Compatibilidade com o fluxo legado de busca."""
        if not mentions:
            return LLMService.empty_analysis("Sem mencoes coletadas para analise.")

        sample = []
        sentiments: dict[str, int] = {}
        for mention in mentions[: max(1, int(settings.LLM_MAX_SAMPLE_MENTIONS))]:
            sentiment = str(mention.get("sentiment") or "neutro")
            sentiments[sentiment] = sentiments.get(sentiment, 0) + 1
            sample.append(
                {
                    "source": mention.get("source"),
                    "rating": mention.get("rating"),
                    "text": (mention.get("text") or "")[:500],
                    "criticality": mention.get("criticality"),
                }
            )

        snapshot = {
            "batch_id": None,
            "brand": brand or "indefinida",
            "period": "legado",
            "total_comments": len(mentions),
            "sentiment_distribution": sentiments,
            "critical_mentions": len([m for m in mentions if m.get("criticality") == "alta"]),
            "average_urgency": 0,
            "top_aspects": [],
            "top_critical_terms": [],
            "sample_mentions": sample,
        }
        return await LLMService.analyze_snapshot(snapshot)

    @staticmethod
    async def answer_domain_chat(
        *,
        locale: str,
        system_prompt: str,
        knowledge_base: str,
        authorized_context: dict[str, Any],
        history: list[dict[str, str]],
        user_message: str,
    ) -> str:
        fallback = (
            "No momento nao consegui gerar a resposta. Tente novamente em instantes."
            if locale == "pt-BR"
            else "I could not generate a response right now. Please try again shortly."
        )

        if not LLMService.ollama_configured():
            return fallback

        history_lines = []
        for item in history[-10:]:
            role = str(item.get("role") or "user").upper()
            content = str(item.get("content") or "")[:800]
            history_lines.append(f"{role}: {content}")

        # Incluir insights como contexto adicional, se disponiveis no authorized_context
        insights_context = ""
        if authorized_context and authorized_context.get("user_insights"):
            insights_list = authorized_context["user_insights"]
            insights_text = "\\n".join([f"- {i.get('title', '')}: {i.get('summary', '')}" for i in insights_list])
            insights_context = f"# Contexto dos insights do usuário:\\n{insights_text}\\n\\n"

        prompt = (
            f"{system_prompt}\\n\\n"
            f"{insights_context}"
            "# Base de conhecimento\\n"
            f"{knowledge_base}\\n\\n"
            "# Contexto autorizado do usuario (JSON)\n"
            f"{json.dumps(authorized_context, ensure_ascii=False)}\n\n"
            "# Historico recente\n"
            f"{chr(10).join(history_lines) if history_lines else '(sem historico)'}\n\n"
            f"# Idioma alvo\n{locale}\n\n"
            f"# Pergunta do usuario\n{user_message}\n\n"
            "Responda de forma objetiva, pratica e estritamente dentro do dominio SentimentoIA."
        )

        try:
            response = await LLMService._call_ollama_text(prompt)
            return response[:2500] if response else fallback
        except (httpx.HTTPError, httpx.TimeoutException, RuntimeError, ValueError) as exc:
            logger.exception("Falha no chat Ollama Cloud: %s", exc)
            return fallback

    @staticmethod
    async def healthcheck() -> dict[str, Any]:
        """Diagnostico simples para /api/status/integrations."""
        base_url = settings.OLLAMA_EFFECTIVE_URL.rstrip("/")
        result = {
            "provider": settings.LLM_PROVIDER,
            "ollama_mode": "cloud",
            "ollama_configured": LLMService.ollama_configured(),
            "ollama_model": settings.OLLAMA_MODEL,
            "ollama_url": base_url,
            "ollama_timeout_seconds": settings.OLLAMA_TIMEOUT_SECONDS,
        }

        if LLMService.ollama_configured():
            try:
                tags_url = f"{base_url}/api/tags"
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(tags_url, headers=LLMService._ollama_headers())
                    result["ollama_ok"] = resp.status_code < 400
                    if resp.status_code >= 400:
                        result["ollama_error"] = f"HTTP {resp.status_code}"
            except (httpx.HTTPError, httpx.TimeoutException, RuntimeError) as exc:
                result["ollama_ok"] = False
                result["ollama_error"] = str(exc)

        return result

    @staticmethod
    async def validate_connection() -> None:
        """Valida conexao com Ollama na inicializacao. Nao crasha a aplicacao."""
        if not LLMService.ollama_configured():
            logger.warning("Ollama nao configurado.")
            return
            
        base_url = settings.OLLAMA_EFFECTIVE_URL.rstrip("/")
        tags_url = f"{base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(tags_url, headers=LLMService._ollama_headers())
                if resp.status_code >= 400:
                    logger.warning(f"Ollama nao acessivel em {tags_url}. Verifique OLLAMA_BASE_URL no .env. HTTP {resp.status_code}")
                else:
                    logger.info("✓ Conexao com Ollama Cloud validada com sucesso")
        except (httpx.HTTPError, httpx.TimeoutException, RuntimeError) as exc:
            logger.warning(f"Ollama nao acessivel em {tags_url}. Verifique OLLAMA_BASE_URL no .env. Detalhes: {exc}")
