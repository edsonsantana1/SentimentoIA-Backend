from datetime import timedelta
from typing import Any

from app.database import get_db
from app.services.enrichment_service import EnrichmentService
from app.services.normalization_service import utcnow
from app.services.search_service import SearchService


class DashboardService:
    @staticmethod
    def get_dashboard(
        user_id: str,
        batch_id: str | None = None,
        period_days: int | None = None,
        limit_mentions: int = 200,
    ) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError("Banco de dados indisponivel")

        # Exige contexto de busca/batch para evitar dashboards agregados incorretos.
        if not batch_id:
            return {
                "search_id": None,
                "batch_id": None,
                "metrics": {
                    "total_mentions": 0,
                    "total_comments": 0,
                    "sentiment_distribution": {},
                    "source_distribution": {},
                    "top_aspects": {},
                    "critical_mentions": 0,
                    "average_urgency": 0,
                    "reputation_score": 0,
                    "trend": "indefinido",
                },
                "mentions": [],
                "latest_insight": None,
            }

        query: dict[str, Any] = {"user_id": user_id}
        query["$or"] = [{"batch_id": batch_id}, {"search_id": batch_id}]

        if period_days and period_days > 0:
            cutoff = utcnow() - timedelta(days=period_days)
            query["created_at"] = {"$gte": cutoff}

        mentions = list(
            db.mentions.find(query, {"raw": 0})
            .sort("created_at", -1)
            .limit(max(1, min(limit_mentions, 1000)))
        )

        if not mentions:
            return {
                "search_id": batch_id,
                "batch_id": batch_id,
                "metrics": {
                    "total_mentions": 0,
                    "total_comments": 0,
                    "sentiment_distribution": {},
                    "source_distribution": {},
                    "top_aspects": {},
                    "critical_mentions": 0,
                    "average_urgency": 0,
                    "reputation_score": 0,
                    "trend": "indefinido",
                },
                "mentions": [],
                "latest_insight": None,
            }

        metrics = EnrichmentService.aggregate(mentions)
        metrics["total_comments"] = metrics.get("total_mentions", 0)

        # Identifica batch/search ID do contexto
        selected_batch_id = batch_id
        if not selected_batch_id:
            selected_batch_id = str(
                mentions[0].get("batch_id") or mentions[0].get("search_id") or ""
            )

        # Busca insight mais recente
        insight_query: dict[str, Any] = {"user_id": user_id, "archived": False}
        if selected_batch_id:
            insight_query["batch_id"] = selected_batch_id
        latest_insight = db.insights.find_one(insight_query, sort=[("created_at", -1)])

        # Se nao encontrou insight por batch_id, tenta buscar llm_analysis do search_job
        llm_analysis = None
        if not latest_insight and selected_batch_id:
            search_job = db.search_jobs.find_one(
                {"user_id": user_id, "search_id": selected_batch_id, "status": "completed"},
                {"llm_analysis": 1},
            )
            if search_job and search_job.get("llm_analysis"):
                llm_analysis = search_job["llm_analysis"]

        return {
            "search_id": selected_batch_id or None,
            "batch_id": selected_batch_id or None,
            "metrics": metrics,
            "mentions": SearchService.serialize_many(mentions),
            "latest_insight": SearchService.serialize(latest_insight) if latest_insight else None,
            "llm_analysis": llm_analysis,
        }

    @staticmethod
    def list_mentions(
        user_id: str,
        batch_id: str | None = None,
        status: str = "processed",
        sentiment: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        db = get_db()
        if db is None:
            raise RuntimeError("Banco de dados indisponivel")

        if not batch_id:
            return []

        query: dict[str, Any] = {"user_id": user_id}
        query["$or"] = [{"batch_id": batch_id}, {"search_id": batch_id}]
        if status:
            query["status"] = status
        if sentiment:
            query["sentiment"] = sentiment

        mentions = list(
            db.mentions.find(query, {"raw": 0})
            .sort("created_at", -1)
            .limit(max(1, min(limit, 500)))
        )
        return SearchService.serialize_many(mentions)
