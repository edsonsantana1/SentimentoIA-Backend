from collections import Counter
from datetime import datetime
from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument

from app.config import settings
from app.database import get_db
from app.services.enrichment_service import EnrichmentService
from app.services.llm_service import LLMService
from app.services.normalization_service import utcnow
from app.services.search_service import SearchService


DB_UNAVAILABLE_ERROR = "Banco de dados indisponivel"
ALLOWED_LOCALES = {"pt-BR", "en-US"}
ALLOWED_THEMES = {"light", "dark"}
ALLOWED_PRIORITIES = {"high", "medium", "low"}
ALLOWED_URGENCIES = {"high", "medium", "low"}
ALLOWED_RESOLUTIONS = {"pending", "in_progress", "resolved"}
ALLOWED_STATUSES = {"open", "in_progress", "resolved"}


class InsightService:
    @staticmethod
    def _default_dashboard_settings(user_id: str) -> dict[str, Any]:
        now = utcnow()
        return {
            "user_id": user_id,
            "locale": "pt-BR",
            "theme": "light",
            "llm_trigger_min_comments": max(1, int(settings.LLM_TRIGGER_MIN_COMMENTS)),
            "auto_archive_insights": False,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _get_or_create_dashboard_settings(user_id: str) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)
        defaults = InsightService._default_dashboard_settings(user_id=user_id)
        current = db.dashboard_settings.find_one_and_update(
            {"user_id": user_id},
            {"$setOnInsert": defaults},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if not current:
            # Fallback defensivo para cenarios raros de retorno nulo.
            current = db.dashboard_settings.find_one({"user_id": user_id})
        if not current:
            raise RuntimeError("Falha ao inicializar dashboard_settings")
        return current

    @staticmethod
    def _serialize_settings(settings_doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "theme": settings_doc.get("theme", "light"),
            "locale": settings_doc.get("locale", "pt-BR"),
            "llm_trigger_min_comments": int(
                settings_doc.get("llm_trigger_min_comments", settings.LLM_TRIGGER_MIN_COMMENTS)
            ),
            "updated_at": settings_doc.get("updated_at").isoformat()
            if isinstance(settings_doc.get("updated_at"), datetime)
            else settings_doc.get("updated_at"),
        }

    @staticmethod
    def get_user_settings(user_id: str) -> dict[str, Any]:
        settings_doc = InsightService._get_or_create_dashboard_settings(user_id=user_id)
        return InsightService._serialize_settings(settings_doc)

    @staticmethod
    def update_user_settings(
        user_id: str,
        *,
        locale: str,
        theme: str,
        llm_trigger_min_comments: int,
    ) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        normalized_locale = (locale or "pt-BR").strip()
        normalized_theme = (theme or "light").strip().lower()
        normalized_threshold = max(1, int(llm_trigger_min_comments))

        if normalized_locale not in ALLOWED_LOCALES:
            raise ValueError("Locale invalido. Use pt-BR ou en-US")
        if normalized_theme not in ALLOWED_THEMES:
            raise ValueError("Tema invalido. Use light ou dark")

        updated = db.dashboard_settings.find_one_and_update(
            {"user_id": user_id},
            {
                "$set": {
                    "locale": normalized_locale,
                    "theme": normalized_theme,
                    "llm_trigger_min_comments": normalized_threshold,
                    "updated_at": utcnow(),
                },
                "$setOnInsert": {
                    "user_id": user_id,
                    "auto_archive_insights": False,
                    "created_at": utcnow(),
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raise RuntimeError("Falha ao atualizar settings")
        return InsightService._serialize_settings(updated)

    @staticmethod
    def get_threshold(user_id: str) -> int:
        settings_doc = InsightService._get_or_create_dashboard_settings(user_id=user_id)
        try:
            threshold = int(settings_doc.get("llm_trigger_min_comments", settings.LLM_TRIGGER_MIN_COMMENTS))
        except (TypeError, ValueError):
            threshold = int(settings.LLM_TRIGGER_MIN_COMMENTS)
        return max(1, threshold)

    @staticmethod
    def _normalize_priority(value: str | None, fallback: str = "medium") -> str:
        candidate = str(value or "").strip().lower().replace(" ", "_")
        if candidate in {"alta", "high", "critica", "critical"}:
            return "high"
        if candidate in {"media", "medium", "moderada", "moderate"}:
            return "medium"
        if candidate in {"baixa", "low", "ok"}:
            return "low"
        return fallback if fallback in ALLOWED_PRIORITIES else "medium"

    @staticmethod
    def _normalize_urgency(value: str | None, fallback: str = "medium") -> str:
        candidate = str(value or "").strip().lower().replace(" ", "_")
        if candidate in {"alta", "high", "critica", "critical", "urgent", "urgente"}:
            return "high"
        if candidate in {"media", "medium", "moderada", "moderate"}:
            return "medium"
        if candidate in {"baixa", "low", "ok"}:
            return "low"
        return fallback if fallback in ALLOWED_URGENCIES else "medium"

    @staticmethod
    def _normalize_resolution(value: str | None, fallback: str = "pending") -> str:
        candidate = str(value or "").strip().lower().replace(" ", "_")
        if candidate in {"resolvido", "resolved", "done", "finalizado", "concluido", "concluído"}:
            return "resolved"
        if candidate in {"in_progress", "em_andamento", "processing", "working", "ongoing"}:
            return "in_progress"
        if candidate in {"pendente", "pending", "open", "novo", "new"}:
            return "pending"
        return fallback if fallback in ALLOWED_RESOLUTIONS else "pending"

    @staticmethod
    def _normalize_status(value: str | None, fallback: str = "open") -> str:
        candidate = str(value or "").strip().lower().replace(" ", "_")
        if candidate in {"open", "aberto", "new", "novo", "pending"}:
            return "open"
        if candidate in {"in_progress", "em_andamento", "processing", "working", "ongoing"}:
            return "in_progress"
        if candidate in {"resolved", "resolvido", "closed", "concluido", "concluído", "done"}:
            return "resolved"
        return fallback if fallback in ALLOWED_STATUSES else "open"

    @staticmethod
    def _priority_rank(priority: str) -> int:
        mapping = {"low": 0, "medium": 1, "high": 2}
        return mapping.get(priority, 1)

    @staticmethod
    def _merge_priority(objective_priority: str, ai_priority: str) -> str:
        return objective_priority if InsightService._priority_rank(objective_priority) >= InsightService._priority_rank(ai_priority) else ai_priority

    @staticmethod
    def _resolve_context(
        user_id: str,
        context_id: str,
        context_type: str | None = None,
    ) -> tuple[str, str]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        safe_context_id = str(context_id or "").strip()
        if not safe_context_id:
            raise ValueError("Contexto de insight invalido")

        if context_type == "batch":
            if db.comment_batches.find_one({"user_id": user_id, "batch_id": safe_context_id}):
                return "batch", safe_context_id
            raise ValueError("Batch nao encontrado")

        if context_type == "search":
            if db.search_jobs.find_one({"user_id": user_id, "search_id": safe_context_id}):
                return "search", safe_context_id
            if db.mentions.find_one({"user_id": user_id, "search_id": safe_context_id}):
                return "search", safe_context_id
            raise ValueError("Busca nao encontrada")

        if db.comment_batches.find_one({"user_id": user_id, "batch_id": safe_context_id}):
            return "batch", safe_context_id
        if db.search_jobs.find_one({"user_id": user_id, "search_id": safe_context_id}):
            return "search", safe_context_id
        if db.mentions.find_one({"user_id": user_id, "search_id": safe_context_id}):
            return "search", safe_context_id
        if db.mentions.find_one({"user_id": user_id, "batch_id": safe_context_id}):
            return "batch", safe_context_id

        raise ValueError("Contexto de insight nao encontrado")

    @staticmethod
    def _latest_context(user_id: str) -> tuple[str, str]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        latest_batch = db.comment_batches.find_one(
            {"user_id": user_id},
            sort=[("updated_at", -1), ("created_at", -1)],
        )
        latest_search = db.search_jobs.find_one(
            {"user_id": user_id, "status": "completed"},
            sort=[("updated_at", -1), ("created_at", -1)],
        )

        if latest_batch and latest_search:
            batch_updated_at = latest_batch.get("updated_at") or latest_batch.get("created_at") or datetime.min
            search_updated_at = latest_search.get("updated_at") or latest_search.get("created_at") or datetime.min
            if search_updated_at >= batch_updated_at:
                return "search", str(latest_search.get("search_id"))
            return "batch", str(latest_batch.get("batch_id"))

        if latest_search:
            return "search", str(latest_search.get("search_id"))
        if latest_batch:
            return "batch", str(latest_batch.get("batch_id"))

        raise ValueError("Nenhum dado recente encontrado para gerar insight")

    @staticmethod
    def _context_mentions_count(user_id: str, context_type: str, context_id: str) -> int:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        if context_type == "batch":
            return int(
                db.mentions.count_documents(
                    {"user_id": user_id, "batch_id": context_id, "status": "processed"}
                )
            )
        return int(db.mentions.count_documents({"user_id": user_id, "search_id": context_id}))

    @staticmethod
    def enqueue_job_if_threshold_reached(
        user_id: str,
        context_id: str,
        trigger: str = "auto",
        force: bool = False,
        context_type: str | None = None,
    ) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        try:
            resolved_context_type, resolved_context_id = InsightService._resolve_context(
                user_id=user_id,
                context_id=context_id,
                context_type=context_type,
            )
        except ValueError as exc:
            return {"queued": False, "reason": str(exc)}

        threshold = InsightService.get_threshold(user_id=user_id)
        processed_count = InsightService._context_mentions_count(
            user_id=user_id,
            context_type=resolved_context_type,
            context_id=resolved_context_id,
        )
        if not force and processed_count < threshold:
            return {
                "queued": False,
                "reason": "threshold_not_met",
                "threshold": threshold,
                "processed_count": processed_count,
            }

        active_job = db.insight_jobs.find_one(
            {
                "user_id": user_id,
                "context_id": resolved_context_id,
                "context_type": resolved_context_type,
                "status": {"$in": ["queued", "processing"]},
            }
        )
        if active_job:
            return {"queued": False, "reason": "active_job_exists", "job_id": active_job.get("job_id")}

        if trigger == "auto" and not force:
            active_insight = db.insights.find_one(
                {
                    "user_id": user_id,
                    "context_id": resolved_context_id,
                    "context_type": resolved_context_type,
                    "archived": False,
                }
            )
            if active_insight:
                return {
                    "queued": False,
                    "reason": "active_insight_exists",
                    "insight_id": active_insight.get("insight_id") or active_insight.get("_id"),
                }

        now = utcnow()
        job_id = f"ijob_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        job_doc = {
            "_id": job_id,
            "job_id": job_id,
            "user_id": user_id,
            "context_id": resolved_context_id,
            "context_type": resolved_context_type,
            "batch_id": resolved_context_id if resolved_context_type == "batch" else None,
            "search_id": resolved_context_id if resolved_context_type == "search" else None,
            "trigger": trigger,
            "status": "queued",
            "threshold": threshold,
            "processed_count_at_enqueue": processed_count,
            "created_at": now,
            "updated_at": now,
        }
        db.insight_jobs.insert_one(job_doc)
        return {
            "queued": True,
            "job_id": job_id,
            "threshold": threshold,
            "processed_count": processed_count,
            "context_id": resolved_context_id,
            "context_type": resolved_context_type,
        }

    @staticmethod
    def enqueue_jobs_for_ready_batches(limit: int = 100) -> dict[str, int]:
        db = get_db()
        if db is None:
            return {"checked": 0, "queued": 0}

        batches = list(
            db.comment_batches.find(
                {
                    "status": {"$in": ["processed", "processed_with_errors"]},
                    "processed_count": {"$gt": 0},
                }
            )
            .sort("updated_at", -1)
            .limit(max(1, limit))
        )

        queued = 0
        for batch in batches:
            result = InsightService.enqueue_job_if_threshold_reached(
                user_id=str(batch.get("user_id", "")),
                context_id=str(batch.get("batch_id", "")),
                trigger="auto",
                force=False,
                context_type="batch",
            )
            if result.get("queued"):
                queued += 1

        return {"checked": len(batches), "queued": queued}

    @staticmethod
    def _build_snapshot(user_id: str, context_id: str, context_type: str = "batch") -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        user_settings = InsightService.get_user_settings(user_id=user_id)
        locale = str(user_settings.get("locale", "pt-BR") or "pt-BR")

        if context_type == "batch":
            batch = db.comment_batches.find_one({"user_id": user_id, "batch_id": context_id})
            if not batch:
                raise ValueError("Batch nao encontrado")

            mentions = list(
                db.mentions.find(
                    {
                        "user_id": user_id,
                        "batch_id": context_id,
                        "status": "processed",
                    },
                    {"raw": 0},
                ).sort("urgency_score", -1)
            )
            brand_name = batch.get("brand") or "indefinida"
        else:
            search_job = db.search_jobs.find_one(
                {"user_id": user_id, "search_id": context_id, "status": "completed"}
            )
            mentions = list(
                db.mentions.find(
                    {
                        "user_id": user_id,
                        "search_id": context_id,
                    },
                    {"raw": 0},
                ).sort("mention_rank_score", -1)
            )
            brand_name = (
                (search_job or {}).get("query")
                or (mentions[0].get("query") if mentions else None)
                or (mentions[0].get("entity") if mentions else None)
                or "indefinida"
            )

        if not mentions:
            raise ValueError("Contexto sem mencoes processadas")

        metrics = EnrichmentService.aggregate(mentions)

        critical_terms_counter: Counter[str] = Counter()
        for mention in mentions:
            for term in mention.get("critical_terms") or []:
                critical_terms_counter[term] += 1

        max_samples = max(1, int(settings.LLM_MAX_SAMPLE_MENTIONS))
        sample_mentions = []
        for mention in mentions[:max_samples]:
            sample_mentions.append(
                {
                    "text": (mention.get("text") or "")[:500],
                    "sentiment": mention.get("sentiment"),
                    "criticality": mention.get("criticality"),
                    "urgency_score": mention.get("urgency_score"),
                    "source": mention.get("source"),
                }
            )

        source_distribution = Counter(str(mention.get("source") or "unknown") for mention in mentions)
        source_references = []
        for mention in mentions[:50]:
            source_references.append(
                {
                    "mention_id": str(mention.get("_id") or ""),
                    "source": str(mention.get("source") or "unknown"),
                    "url": str(mention.get("url") or ""),
                    "canonical_url": str(mention.get("canonical_url") or ""),
                    "published_at": mention.get("published_at").isoformat()
                    if isinstance(mention.get("published_at"), datetime)
                    else mention.get("published_at"),
                }
            )

        published = [m.get("published_at") for m in mentions if isinstance(m.get("published_at"), datetime)]
        if published:
            period = f"{min(published).date().isoformat()}/{max(published).date().isoformat()}"
        else:
            period = "indefinido"

        return {
            "batch_id": context_id if context_type == "batch" else None,
            "search_id": context_id if context_type == "search" else None,
            "context_id": context_id,
            "context_type": context_type,
            "brand": brand_name,
            "locale": locale,
            "period": period,
            "total_comments": len(mentions),
            "sentiment_distribution": metrics.get("sentiment_distribution", {}),
            "critical_mentions": int(metrics.get("critical_mentions", 0)),
            "average_urgency": float(metrics.get("average_urgency", 0)),
            "top_aspects": list((metrics.get("top_aspects") or {}).keys())[:10],
            "top_critical_terms": [term for term, _ in critical_terms_counter.most_common(10)],
            "source_distribution": dict(source_distribution),
            "source_references": source_references,
            "sample_mentions": sample_mentions,
        }

    @staticmethod
    def _build_operational_fields(snapshot: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        total_comments = max(1, int(snapshot.get("total_comments", 0) or 0))
        average_urgency = float(snapshot.get("average_urgency", 0) or 0)
        critical_mentions = int(snapshot.get("critical_mentions", 0) or 0)
        sentiment_distribution = snapshot.get("sentiment_distribution") or {}
        negative_mentions = int(sentiment_distribution.get("negativo", 0) or 0)

        critical_ratio = min(1.0, critical_mentions / total_comments)
        negative_ratio = min(1.0, negative_mentions / total_comments)
        objective_score = min(1.0, (average_urgency * 0.5) + (critical_ratio * 0.3) + (negative_ratio * 0.2))

        if objective_score >= 0.66:
            objective_priority = "high"
        elif objective_score >= 0.40:
            objective_priority = "medium"
        else:
            objective_priority = "low"

        if average_urgency >= 0.70:
            objective_urgency = "high"
        elif average_urgency >= 0.40:
            objective_urgency = "medium"
        else:
            objective_urgency = "low"

        ai_priority = InsightService._normalize_priority(str(analysis.get("priority") or ""), fallback=objective_priority)
        ai_urgency = InsightService._normalize_urgency(str(analysis.get("urgency") or ""), fallback=objective_urgency)

        final_priority = InsightService._merge_priority(objective_priority, ai_priority)
        final_urgency = InsightService._merge_priority(objective_urgency, ai_urgency)

        resolution = InsightService._normalize_resolution(str(analysis.get("resolution") or ""), fallback="pending")
        default_status = "resolved" if resolution == "resolved" else "open"
        status = InsightService._normalize_status(str(analysis.get("status") or ""), fallback=default_status)

        root_cause = str(analysis.get("root_cause") or "").strip()
        if not root_cause:
            critical_terms = snapshot.get("top_critical_terms") or []
            if critical_terms:
                root_cause = f"Padroes criticos detectados: {', '.join(critical_terms[:3])}"
            else:
                root_cause = "Causa raiz em investigacao"

        recommended_action = str(analysis.get("recommended_action") or "").strip()
        if not recommended_action:
            action_list = analysis.get("recommended_actions") or []
            recommended_action = str(action_list[0]) if action_list else "Priorizar tratativas de alta severidade."

        return {
            "company": str(snapshot.get("brand") or "indefinida"),
            "priority": final_priority,
            "urgency": final_urgency,
            "root_cause": root_cause,
            "recommended_action": recommended_action,
            "status": status,
            "resolution": resolution,
            "timestamp": utcnow().isoformat(),
            "priority_score": round(objective_score, 4),
        }

    @staticmethod
    async def process_queued_jobs(limit: int = 3, job_id: str | None = None) -> dict[str, int]:
        db = get_db()
        if db is None:
            return {"picked": 0, "completed": 0, "failed": 0}

        job_query: dict[str, Any] = {"status": "queued"}
        if job_id:
            job_query["_id"] = job_id

        jobs = list(
            db.insight_jobs.find(job_query).sort("created_at", 1).limit(max(1, limit))
        )

        completed = 0
        failed = 0

        for job in jobs:
            job_id = str(job.get("job_id") or job.get("_id"))
            user_id = str(job.get("user_id", ""))
            context_type = str(job.get("context_type") or ("batch" if job.get("batch_id") else "search"))
            context_id = str(job.get("context_id") or job.get("batch_id") or job.get("search_id") or "")

            claimed = db.insight_jobs.update_one(
                {"_id": job_id, "status": "queued"},
                {"$set": {"status": "processing", "updated_at": utcnow()}},
            )
            if claimed.modified_count == 0:
                continue

            try:
                if not context_id:
                    raise ValueError("contexto do job invalido")

                snapshot = InsightService._build_snapshot(
                    user_id=user_id,
                    context_id=context_id,
                    context_type=context_type,
                )
                analysis = await LLMService.analyze_snapshot(snapshot)
                operational_fields = InsightService._build_operational_fields(snapshot=snapshot, analysis=analysis)
                processed_count_now = InsightService._context_mentions_count(
                    user_id=user_id,
                    context_type=context_type,
                    context_id=context_id,
                )

                now = utcnow()
                insight_id = f"insight_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
                audit_metadata = {
                    "generated_at": now.isoformat(),
                    "provider": settings.LLM_PROVIDER,
                    "model": settings.OLLAMA_MODEL,
                    "threshold": int(job.get("threshold") or InsightService.get_threshold(user_id=user_id)),
                    "processed_count_at_enqueue": int(job.get("processed_count_at_enqueue") or 0),
                    "processed_count_at_generation": processed_count_now,
                    "job_trigger": str(job.get("trigger") or "auto"),
                    "deterministic_rules_version": "priority-v1",
                }
                insight_doc = {
                    "_id": insight_id,
                    "insight_id": insight_id,
                    "job_id": job_id,
                    "user_id": user_id,
                    "batch_id": context_id if context_type == "batch" else None,
                    "search_id": context_id if context_type == "search" else None,
                    "context_id": context_id,
                    "context_type": context_type,
                    "trigger": job.get("trigger", "auto"),
                    "archived": False,
                    **operational_fields,
                    "source_references": snapshot.get("source_references", []),
                    "source_distribution": snapshot.get("source_distribution", {}),
                    "audit_metadata": audit_metadata,
                    "snapshot": snapshot,
                    "executive_summary": analysis.get("executive_summary"),
                    "sentiment_overview": analysis.get("sentiment_overview"),
                    "risks": analysis.get("risks", []),
                    "opportunities": analysis.get("opportunities", []),
                    "recommended_actions": analysis.get("recommended_actions", []),
                    "decision_guidance": analysis.get("decision_guidance"),
                    "trend": analysis.get("trend", "indefinido"),
                    "llm_payload": analysis,
                    "created_at": now,
                    "updated_at": now,
                }
                db.insights.insert_one(insight_doc)

                db.insight_jobs.update_one(
                    {"_id": job_id},
                    {
                        "$set": {
                            "status": "completed",
                            "insight_id": insight_id,
                            "updated_at": utcnow(),
                        }
                    },
                )
                completed += 1
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                db.insight_jobs.update_one(
                    {"_id": job_id},
                    {
                        "$set": {
                            "status": "failed",
                            "error_message": str(exc)[:500],
                            "updated_at": utcnow(),
                        }
                    },
                )
                failed += 1

        return {"picked": len(jobs), "completed": completed, "failed": failed}

    @staticmethod
    def list_insights(
        user_id: str,
        include_archived: bool = False,
        limit: int = 50,
        batch_id: str | None = None,
        search_id: str | None = None,
        priority: str | None = None,
        resolution: str | None = None,
    ) -> list[dict[str, Any]]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        query: dict[str, Any] = {"user_id": user_id}
        if not include_archived:
            query["archived"] = False
        if batch_id or search_id:
            context_key = batch_id or search_id
            query["$or"] = [
                {"batch_id": context_key},
                {"search_id": context_key},
                {"context_id": context_key},
            ]
        else:
            return []
        if priority:
            query["priority"] = InsightService._normalize_priority(priority, fallback="medium")
        if resolution:
            query["resolution"] = InsightService._normalize_resolution(resolution, fallback="pending")

        items = list(
            db.insights.find(query).sort("created_at", -1).limit(max(1, min(limit, 200)))
        )
        return SearchService.serialize_many(items)

    @staticmethod
    async def generate_insight(
        user_id: str,
        batch_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        if batch_id:
            try:
                context_type, context_id = InsightService._resolve_context(
                    user_id=user_id,
                    context_id=batch_id,
                    context_type=None,
                )
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        else:
            context_type, context_id = InsightService._latest_context(user_id=user_id)

        # Geração manual precisa funcionar mesmo com poucas menções.
        # O threshold continua existindo para geração automática, mas o botão manual
        # não deve bloquear o usuário quando a busca retornou 1-4 itens.
        manual_force = bool(force) or batch_id is None

        enqueue_result = InsightService.enqueue_job_if_threshold_reached(
            user_id=user_id,
            context_id=context_id,
            trigger="manual",
            force=manual_force,
            context_type=context_type,
        )

        if not enqueue_result.get("queued"):
            reason = enqueue_result.get("reason", "nao_enfileirado")
            existing_insight_id = enqueue_result.get("insight_id")
            if existing_insight_id:
                existing = db.insights.find_one({"_id": existing_insight_id, "user_id": user_id})
                if existing:
                    return SearchService.serialize(existing)
            raise ValueError(f"Nao foi possivel enfileirar insight: {reason}")

        job_id = str(enqueue_result.get("job_id") or "")
        result = await InsightService.process_queued_jobs(limit=1, job_id=job_id)

        if result.get("failed"):
            failed_job = db.insight_jobs.find_one({"_id": job_id, "user_id": user_id}) or {}
            detail = failed_job.get("error_message") or "Insight nao foi gerado"
            raise ValueError(str(detail))

        generated = db.insights.find_one(
            {
                "user_id": user_id,
                "context_id": context_id,
                "context_type": context_type,
            },
            sort=[("created_at", -1)],
        )
        if not generated:
            raise ValueError("Insight nao foi gerado")
        return SearchService.serialize(generated)

    @staticmethod
    async def regenerate_insight(user_id: str, insight_id: str) -> dict[str, Any]:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        current = db.insights.find_one({"_id": insight_id, "user_id": user_id})
        if not current:
            raise ValueError("Insight nao encontrado")

        context_id = str(current.get("context_id") or current.get("batch_id") or current.get("search_id") or "")
        context_type = str(current.get("context_type") or ("batch" if current.get("batch_id") else "search"))
        if not context_id:
            raise RuntimeError("Insight sem contexto para regeneracao")

        enqueue_result = InsightService.enqueue_job_if_threshold_reached(
            user_id=user_id,
            context_id=context_id,
            trigger="regenerate",
            force=True,
            context_type=context_type,
        )
        if not enqueue_result.get("queued"):
            raise RuntimeError("Nao foi possivel enfileirar regeneracao")

        await InsightService.process_queued_jobs(limit=1)

        regenerated = db.insights.find_one(
            {
                "user_id": user_id,
                "context_id": context_id,
                "context_type": context_type,
            },
            sort=[("created_at", -1)],
        )
        if not regenerated:
            raise RuntimeError("Insight nao foi regenerado")
        return SearchService.serialize(regenerated)

    @staticmethod
    def archive_insight(user_id: str, insight_id: str) -> bool:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        result = db.insights.update_one(
            {"_id": insight_id, "user_id": user_id},
            {
                "$set": {
                    "archived": True,
                    "archived_at": utcnow(),
                    "updated_at": utcnow(),
                }
            },
        )
        return result.modified_count > 0

    @staticmethod
    def delete_insight(user_id: str, insight_id: str) -> bool:
        db = get_db()
        if db is None:
            raise RuntimeError(DB_UNAVAILABLE_ERROR)

        result = db.insights.delete_one({"_id": insight_id, "user_id": user_id})
        return result.deleted_count > 0
