from pathlib import Path
from typing import Any
from unicodedata import category, normalize
from uuid import uuid4
import re

from app.database import get_db
from app.services.dashboard_service import DashboardService
from app.services.insight_service import InsightService
from app.services.llm_service import LLMService
from app.services.normalization_service import utcnow
from app.services.search_service import SearchService

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "packages" / "prompts"
SYSTEM_PROMPT_FILE = "domain-closed-system-prompt.md"
KNOWLEDGE_FILE = "domain-knowledge-base.md"
DB_UNAVAILABLE_ERROR = "Banco de dados indisponivel"
DEFAULT_THREAD_TITLE = "Nova conversa"

DOMAIN_KEYWORDS = {
    "sentimentoia",
    "dashboard",
    "insight",
    "insights",
    "kpi",
    "kpis",
    "mencao",
    "mencoes",
    "reputacao",
    "ingestao",
    "busca",
    "search",
    "settings",
    "configuracao",
    "configuracoes",
    "tema",
    "idioma",
    "locale",
    "llm",
    "limiar",
    "threshold",
    "relatorio",
    "report",
    "chat",
    "thread",
    "navegacao",
    "pagina",
    "exportacao",
    "csv",
    "pdf",
}

GREETING_KEYWORDS = {
    "oi",
    "ola",
    "olá",
    "hello",
    "hi",
    "ajuda",
    "help",
}


class ChatService:
    @staticmethod
    def _sanitize_user_content(content: str) -> str:
        # Remove control chars and collapse excessive whitespace.
        text = (content or "").replace("\x00", "")
        text = "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)
        text = re.sub(r"[\t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        return text.strip()[:3000]

    @staticmethod
    def _normalize_text(value: str) -> str:
        lowered = (value or "").strip().lower()
        normalized = normalize("NFD", lowered)
        return "".join(ch for ch in normalized if category(ch) != "Mn")

    @staticmethod
    def _read_prompt_file(filename: str) -> str:
        path = PROMPTS_DIR / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _domain_prompt_bundle() -> tuple[str, str]:
        system_prompt = ChatService._read_prompt_file(SYSTEM_PROMPT_FILE)
        knowledge = ChatService._read_prompt_file(KNOWLEDGE_FILE)
        return system_prompt, knowledge

    @staticmethod
    def _has_domain_prompt_assets(system_prompt: str, knowledge: str) -> bool:
        return bool(system_prompt.strip()) and bool(knowledge.strip())

    @staticmethod
    def _is_in_scope(message: str) -> bool:
        normalized = ChatService._normalize_text(message)
        if not normalized:
            return False

        if normalized in GREETING_KEYWORDS:
            return True

        return any(keyword in normalized for keyword in DOMAIN_KEYWORDS)

    @staticmethod
    def _refusal_message(locale: str) -> str:
        if locale == "en-US":
            return "I can only help with SentimentoIA (navigation, settings, KPIs, and authorized account data)."
        return "Posso ajudar apenas com o SentimentoIA (navegacao, configuracoes, KPIs e dados autorizados da sua conta)."

    @staticmethod
    def _serialize_thread(item: dict[str, Any]) -> dict[str, Any]:
        serialized = SearchService.serialize(item)
        serialized["thread_id"] = serialized.get("thread_id") or serialized.get("id")
        return serialized

    @staticmethod
    def _serialize_message(item: dict[str, Any]) -> dict[str, Any]:
        serialized = SearchService.serialize(item)
        serialized["message_id"] = serialized.get("message_id") or serialized.get("id")
        return serialized

    @staticmethod
    def _ensure_thread(user_id: str, thread_id: str) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        thread = db.chat_threads.find_one({"_id": thread_id, "user_id": user_id})
        if not thread:
            raise ValueError("Thread nao encontrada")
        return thread

    @staticmethod
    def list_threads(user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        items = list(
            db.chat_threads.find({"user_id": user_id, "archived": {"$ne": True}})
            .sort("last_message_at", -1)
            .limit(max(1, min(limit, 200)))
        )
        return [ChatService._serialize_thread(item) for item in items]

    @staticmethod
    def create_thread(user_id: str, title: str | None = None, locale: str = "pt-BR") -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        now = utcnow()
        thread_id = f"thread_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        safe_title = (title or DEFAULT_THREAD_TITLE).strip()[:120] or DEFAULT_THREAD_TITLE

        doc = {
            "_id": thread_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "title": safe_title,
            "locale": locale,
            "archived": False,
            "created_at": now,
            "updated_at": now,
            "last_message_at": now,
        }
        db.chat_threads.insert_one(doc)
        return ChatService._serialize_thread(doc)

    @staticmethod
    def list_messages(user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        ChatService._ensure_thread(user_id=user_id, thread_id=thread_id)

        items = list(
            db.chat_messages.find({"user_id": user_id, "thread_id": thread_id})
            .sort("created_at", 1)
            .limit(max(1, min(limit, 500)))
        )
        return [ChatService._serialize_message(item) for item in items]

    @staticmethod
    def delete_thread(user_id: str, thread_id: str) -> bool:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        thread = db.chat_threads.find_one(
            {
                "user_id": user_id,
                "$or": [{"_id": thread_id}, {"thread_id": thread_id}],
            }
        )
        if not thread:
            raise ValueError("Thread nao encontrada")

        resolved_thread_id = str(thread.get("thread_id") or thread.get("_id"))
        db.chat_messages.delete_many({"user_id": user_id, "thread_id": resolved_thread_id})
        result = db.chat_threads.delete_one({"_id": thread.get("_id"), "user_id": user_id})
        return result.deleted_count > 0

    @staticmethod
    def delete_message(user_id: str, thread_id: str, message_id: str) -> bool:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        thread = db.chat_threads.find_one(
            {
                "user_id": user_id,
                "$or": [{"_id": thread_id}, {"thread_id": thread_id}],
            }
        )
        if not thread:
            raise ValueError("Thread nao encontrada")

        resolved_thread_id = str(thread.get("thread_id") or thread.get("_id"))
        result = db.chat_messages.delete_one(
            {
                "user_id": user_id,
                "thread_id": resolved_thread_id,
                "$or": [{"_id": message_id}, {"message_id": message_id}],
            }
        )
        return result.deleted_count > 0

    @staticmethod
    def delete_all_threads(user_id: str) -> dict[str, int]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        deleted_threads = db.chat_threads.delete_many({"user_id": user_id}).deleted_count
        deleted_messages = db.chat_messages.delete_many({"user_id": user_id}).deleted_count
        return {
            "deleted_threads": int(deleted_threads),
            "deleted_messages": int(deleted_messages),
        }

    @staticmethod
    def _authorized_context(user_id: str) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        settings_doc = InsightService.get_user_settings(user_id=user_id)
        dashboard = DashboardService.get_dashboard(user_id=user_id, limit_mentions=50)

        latest_batch = db.comment_batches.find_one({"user_id": user_id}, sort=[("updated_at", -1)])
        latest_insight = db.insights.find_one(
            {"user_id": user_id, "archived": False},
            sort=[("created_at", -1)],
        )

        metrics = dashboard.get("metrics") or {}
        latest_batch_summary = None
        if latest_batch:
            latest_batch_summary = {
                "batch_id": latest_batch.get("batch_id"),
                "status": latest_batch.get("status"),
                "accepted_count": latest_batch.get("accepted_count"),
                "processed_count": latest_batch.get("processed_count"),
                "error_count": latest_batch.get("error_count"),
                "updated_at": latest_batch.get("updated_at").isoformat()
                if hasattr(latest_batch.get("updated_at"), "isoformat")
                else latest_batch.get("updated_at"),
            }

        latest_insight_summary = None
        if latest_insight:
            latest_insight_summary = {
                "insight_id": latest_insight.get("insight_id") or latest_insight.get("_id"),
                "batch_id": latest_insight.get("batch_id"),
                "trend": latest_insight.get("trend"),
                "executive_summary": str(latest_insight.get("executive_summary") or "")[:400],
                "created_at": latest_insight.get("created_at").isoformat()
                if hasattr(latest_insight.get("created_at"), "isoformat")
                else latest_insight.get("created_at"),
            }

        return {
            "settings": settings_doc,
            "dashboard": {
                "batch_id": dashboard.get("batch_id"),
                "total_mentions": metrics.get("total_mentions", 0),
                "critical_mentions": metrics.get("critical_mentions", 0),
                "average_urgency": metrics.get("average_urgency", 0),
                "reputation_score": metrics.get("reputation_score", 0),
                "sentiment_distribution": metrics.get("sentiment_distribution", {}),
                "source_distribution": metrics.get("source_distribution", {}),
                "top_aspects": metrics.get("top_aspects", {}),
            },
            "latest_batch": latest_batch_summary,
            "latest_insight": latest_insight_summary,
            "allowed_pages": [
                "/search",
                "/dashboard",
                "/analysis",
                "/reports",
                "/settings",
            ],
        }

    @staticmethod
    async def send_message(
        user_id: str,
        thread_id: str,
        content: str,
        locale: str,
    ) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        thread = ChatService._ensure_thread(user_id=user_id, thread_id=thread_id)
        clean_content = ChatService._sanitize_user_content(content)
        if not clean_content:
            raise ValueError("Mensagem vazia")

        now = utcnow()
        user_message_id = f"cmsg_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        user_message = {
            "_id": user_message_id,
            "message_id": user_message_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "role": "user",
            "content": clean_content[:3000],
            "created_at": now,
        }
        db.chat_messages.insert_one(user_message)

        if ChatService._is_in_scope(clean_content):
            system_prompt, knowledge = ChatService._domain_prompt_bundle()
            if not ChatService._has_domain_prompt_assets(system_prompt, knowledge):
                assistant_content = ChatService._refusal_message(locale=locale)
            else:
                history_docs = list(
                    db.chat_messages.find(
                        {"user_id": user_id, "thread_id": thread_id, "role": {"$in": ["user", "assistant"]}}
                    )
                    .sort("created_at", -1)
                    .limit(12)
                )
                history_docs.reverse()
                history = [
                    {
                        "role": str(item.get("role") or "user"),
                        "content": str(item.get("content") or ""),
                    }
                    for item in history_docs
                ]
                allowed_context = ChatService._authorized_context(user_id=user_id)
                assistant_content = await LLMService.answer_domain_chat(
                    locale=locale,
                    system_prompt=system_prompt,
                    knowledge_base=knowledge,
                    authorized_context=allowed_context,
                    history=history,
                    user_message=clean_content,
                )
        else:
            assistant_content = ChatService._refusal_message(locale=locale)

        now_response = utcnow()
        assistant_id = f"cmsg_{now_response.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        assistant_message = {
            "_id": assistant_id,
            "message_id": assistant_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "role": "assistant",
            "content": assistant_content[:3500],
            "created_at": now_response,
        }
        db.chat_messages.insert_one(assistant_message)

        title = str(thread.get("title") or "").strip()
        if title in {"", DEFAULT_THREAD_TITLE}:
            title = clean_content[:72]

        db.chat_threads.update_one(
            {"_id": thread_id, "user_id": user_id},
            {
                "$set": {
                    "locale": locale,
                    "title": title,
                    "updated_at": now_response,
                    "last_message_at": now_response,
                }
            },
        )

        return {
            "thread": ChatService._serialize_thread(
                db.chat_threads.find_one({"_id": thread_id, "user_id": user_id})
                or {"_id": thread_id, "thread_id": thread_id, "title": title}
            ),
            "user_message": ChatService._serialize_message(user_message),
            "assistant_message": ChatService._serialize_message(assistant_message),
        }
