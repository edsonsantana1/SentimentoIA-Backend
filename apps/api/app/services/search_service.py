import logging
from datetime import datetime, timedelta
from typing import Any

from app.config import settings
from app.database import get_db
from app.services.collector_service import CollectorService
from app.services.enrichment_service import EnrichmentService
from app.services.llm_service import LLMService
from app.services.normalization_service import canonicalize_url, make_search_id, utcnow

logger = logging.getLogger(__name__)


class SearchService:
    """Pipeline principal do produto.

    Fluxo:
    1. Coleta dados reais.
    2. Normaliza.
    3. Enriquece cada menção.
    4. Salva no MongoDB por search_id.
    5. Chama LLM para resumo/decisão.
    6. Gera alertas.
    """

    @staticmethod
    async def run_search(
        *,
        user_id: str,
        query: str,
        sources: list[str],
        period_days: int = 30,
        locality: str | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        db = get_db()
        now = utcnow()
        clean_query = (query or "").strip()

        # Cache inteligente: se busca igual existir dentro do TTL, retorna o resultado salvo.
        cache_key = {
            "user_id": user_id,
            "query": clean_query.lower(),
            "sources": sorted([s.lower() for s in sources]),
            "period_days": int(period_days),
            "locality": locality or "",
        }

        if use_cache:
            ttl_start = now - timedelta(minutes=settings.CACHE_TTL_MINUTES)
            cached = db.search_jobs.find_one(
                {
                    **cache_key,
                    "created_at": {"$gte": ttl_start},
                    "status": "completed",
                },
                sort=[("created_at", -1)],
            )
            if cached:
                mentions = list(
                    db.mentions.find(
                        {"user_id": user_id, "search_id": cached["search_id"]},
                        {"raw": 0},
                    ).sort("published_at", -1)
                )
                status = cached.get("result_status") or ("completed" if mentions else "no_results")
                message = cached.get("message") or (
                    "Busca recuperada do cache."
                    if mentions
                    else "Não encontramos menções públicas suficientes para essa busca nas fontes consultadas."
                )
                return {
                    "search_id": cached["search_id"],
                    "query": query,
                    "cached": True,
                    "total": len(mentions),
                    "mentions": SearchService.serialize_many(mentions),
                    "metrics": cached.get("metrics", {}),
                    "llm_analysis": cached.get("llm_analysis", {}),
                    "errors": cached.get("errors", []),
                    "status": status,
                    "message": message,
                    "suggestions": cached.get("suggestions", []),
                }

        search_id = make_search_id()

        db.search_jobs.insert_one({
            "search_id": search_id,
            **cache_key,
            "status": "running",
            "result_status": "running",
            "created_at": now,
            "updated_at": now,
            "errors": [],
        })

        collected, errors = await CollectorService.collect(
            clean_query,
            sources,
            period_days=period_days,
            locality=locality,
            user_id=user_id,
        )
        collected = SearchService._dedupe_mentions_in_memory(collected)

        enriched_mentions: list[dict[str, Any]] = []
        cached_mentions_for_this_search: list[dict[str, Any]] = []
        cutoff = now - timedelta(days=max(1, int(period_days)))
        existing_signatures = SearchService._load_existing_signatures(
            db=db,
            user_id=user_id,
            mentions=collected,
        )

        for mention in collected:
            published_at = mention.get("published_at")
            if isinstance(published_at, datetime):
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=cutoff.tzinfo)
                    mention["published_at"] = published_at
                if published_at < cutoff:
                    continue

            signature = SearchService._mention_signature(mention)
            if SearchService._signature_exists(signature, existing_signatures):
                existing_mention = SearchService._find_existing_mention_by_signature(
                    db=db,
                    user_id=user_id,
                    signature=signature,
                )
                if existing_mention:
                    # Clona a menção para o search_id atual. Isso mantém isolamento
                    # por busca e evita dashboard vazio em buscas repetidas.
                    existing_mention.pop("_id", None)
                    existing_mention.update({
                        "search_id": search_id,
                        "user_id": user_id,
                        "query": clean_query,
                        "cached": True,
                        "created_at": utcnow(),
                    })
                    cached_mentions_for_this_search.append(existing_mention)
                continue

            enrichment = EnrichmentService.analyze_mention(mention["text"], mention.get("rating"))
            mention_rank_score = SearchService._rank_mention(mention, enrichment)
            mention.update(enrichment)
            mention.update({
                "search_id": search_id,
                "user_id": user_id,
                "query": clean_query,
                "cached": False,
                "mention_rank_score": mention_rank_score,
                "created_at": utcnow(),
            })
            enriched_mentions.append(mention)
            SearchService._remember_signature(signature, existing_signatures)

        response_mentions = enriched_mentions + cached_mentions_for_this_search
        response_mentions.sort(
            key=lambda item: (
                float(item.get("mention_rank_score") or 0),
                str(item.get("published_at") or ""),
            ),
            reverse=True,
        )

        if enriched_mentions:
            db.mentions.insert_many(enriched_mentions)

        if cached_mentions_for_this_search:
            try:
                db.mentions.insert_many(cached_mentions_for_this_search, ordered=False)
            except Exception as exc:
                logger.warning("Falha ao clonar menções cached para search_id=%s: %s", search_id, exc)

        if not response_mentions:
            metrics = EnrichmentService.aggregate([])
            suggestions = [
                "Tente buscar apenas pelo nome principal da empresa.",
                "Evite termos muito longos na primeira busca.",
                "Teste variações como marca + reclamação, marca + atendimento ou marca + produto.",
                "Algumas fontes públicas podem bloquear scraping, retornar captcha ou não possuir dados disponíveis.",
            ]
            llm_analysis = {
                "summary": "Não encontramos menções públicas suficientes para gerar uma análise confiável nas fontes consultadas.",
                "risks": [],
                "opportunities": [],
                "recommendations": suggestions[:3],
                "confidence": "insuficiente",
                "status": "no_results",
            }

            db.search_jobs.update_one(
                {"search_id": search_id},
                {
                    "$set": {
                        "status": "completed",
                        "result_status": "no_results",
                        "updated_at": utcnow(),
                        "total": 0,
                        "metrics": metrics,
                        "llm_analysis": llm_analysis,
                        "errors": errors,
                        "message": llm_analysis["summary"],
                        "suggestions": suggestions,
                    }
                },
            )

            return {
                "search_id": search_id,
                "query": clean_query,
                "cached": False,
                "total": 0,
                "mentions": [],
                "metrics": metrics,
                "llm_analysis": llm_analysis,
                "alerts": [],
                "errors": errors,
                "status": "no_results",
                "message": llm_analysis["summary"],
                "suggestions": suggestions,
            }

        metrics = EnrichmentService.aggregate(response_mentions)
        llm_analysis = await LLMService.analyze_mentions(clean_query, response_mentions)

        alerts = SearchService.generate_alerts(
            user_id,
            search_id,
            clean_query,
            response_mentions,
            metrics,
            llm_analysis,
        )
        if alerts:
            db.alerts.insert_many(alerts)

        result_status = "completed" if len(response_mentions) >= int(settings.LLM_TRIGGER_MIN_COMMENTS) else "partial"
        message = (
            "Busca concluída com menções suficientes para análise."
            if result_status == "completed"
            else "Busca concluída com poucas menções. A análise é preliminar e tem baixa confiança."
        )

        db.search_jobs.update_one(
            {"search_id": search_id},
            {
                "$set": {
                    "status": "completed",
                    "result_status": result_status,
                    "updated_at": utcnow(),
                    "total": len(response_mentions),
                    "metrics": metrics,
                    "llm_analysis": llm_analysis,
                    "errors": errors,
                    "message": message,
                    "suggestions": [],
                }
            },
        )

        return {
            "search_id": search_id,
            "query": clean_query,
            "cached": False,
            "total": len(response_mentions),
            "mentions": SearchService.serialize_many(response_mentions),
            "metrics": metrics,
            "llm_analysis": llm_analysis,
            "alerts": SearchService.serialize_many(alerts),
            "errors": errors,
            "status": result_status,
            "message": message,
            "suggestions": [],
        }

    @staticmethod
    def generate_alerts(
        user_id: str,
        search_id: str,
        query: str,
        mentions: list[dict[str, Any]],
        metrics: dict[str, Any],
        llm_analysis: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Gera alertas internos a partir de criticidade e score."""
        alerts: list[dict[str, Any]] = []
        now = utcnow()

        critical_mentions = [m for m in mentions if m.get("criticality") == "alta"]

        if critical_mentions:
            alerts.append({
                "user_id": user_id,
                "search_id": search_id,
                "query": query,
                "type": "critical_mentions",
                "severity": "alta",
                "title": f"{len(critical_mentions)} menções críticas detectadas",
                "message": "Existem menções com risco reputacional alto. Priorize resposta operacional.",
                "is_read": False,
                "created_at": now,
            })

        if metrics.get("reputation_score", 100) < 45 and mentions:
            alerts.append({
                "user_id": user_id,
                "search_id": search_id,
                "query": query,
                "type": "low_reputation_score",
                "severity": "alta",
                "title": "Score de reputação baixo",
                "message": f"Score atual: {metrics.get('reputation_score')}. Revise riscos e recomendações.",
                "is_read": False,
                "created_at": now,
            })

        if llm_analysis.get("risks"):
            alerts.append({
                "user_id": user_id,
                "search_id": search_id,
                "query": query,
                "type": "llm_risk",
                "severity": "media",
                "title": "Riscos estratégicos identificados pela IA",
                "message": "; ".join(llm_analysis.get("risks", [])[:3]),
                "is_read": False,
                "created_at": now,
            })

        return alerts

    @staticmethod
    def dashboard(user_id: str, search_id: str | None = None) -> dict[str, Any]:
        """Retorna dashboard usando search_id para não misturar buscas."""
        db = get_db()

        if not search_id:
            last = db.search_jobs.find_one({"user_id": user_id, "status": "completed"}, sort=[("created_at", -1)])
            if not last:
                return {"search_id": None, "metrics": {}, "mentions": [], "alerts": [], "llm_analysis": {}}
            search_id = last["search_id"]

        job = db.search_jobs.find_one({"user_id": user_id, "search_id": search_id}) or {}
        mentions = list(db.mentions.find({"user_id": user_id, "search_id": search_id}, {"raw": 0}).sort("published_at", -1))
        alerts = list(db.alerts.find({"user_id": user_id, "search_id": search_id}).sort("created_at", -1))

        return {
            "search_id": search_id,
            "query": job.get("query"),
            "metrics": job.get("metrics") or EnrichmentService.aggregate(mentions),
            "llm_analysis": job.get("llm_analysis", {}),
            "mentions": SearchService.serialize_many(mentions),
            "alerts": SearchService.serialize_many(alerts),
            "errors": job.get("errors", []),
        }

    @staticmethod
    def history(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        db = get_db()
        jobs = list(db.search_jobs.find({"user_id": user_id}).sort("created_at", -1).limit(limit))
        return SearchService.serialize_many(jobs)

    @staticmethod
    def serialize_many(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [SearchService.serialize(i) for i in items]

    @staticmethod
    def serialize(item: dict[str, Any]) -> dict[str, Any]:
        output = {}
        for k, v in item.items():
            if k == "_id":
                output["id"] = str(v)
            elif hasattr(v, "isoformat"):
                output[k] = v.isoformat()
            else:
                output[k] = v
        return output

    @staticmethod
    def _dedupe_mentions_in_memory(mentions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique_items: list[dict[str, Any]] = []
        seen: set[str] = set()

        for mention in mentions:
            signature = SearchService._mention_signature(mention)
            dedupe_key = (
                signature.get("content_hash")
                or signature.get("canonical_url")
                or signature.get("text_fingerprint")
                or signature.get("external_id")
                or signature.get("fallback")
            )
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unique_items.append(mention)

        return unique_items

    @staticmethod
    def _load_existing_signatures(
        *,
        db,
        user_id: str,
        mentions: list[dict[str, Any]],
    ) -> dict[str, set[str]]:
        signatures = {
            "external_id": set(),
            "text_fingerprint": set(),
            "content_hash": set(),
            "canonical_url": set(),
            "fallback": set(),
        }

        external_ids = sorted({str(item.get("external_id") or "").strip() for item in mentions if item.get("external_id")})
        text_fingerprints = sorted(
            {str(item.get("text_fingerprint") or "").strip() for item in mentions if item.get("text_fingerprint")}
        )
        content_hashes = sorted({str(item.get("content_hash") or "").strip() for item in mentions if item.get("content_hash")})
        canonical_urls = sorted(
            {
                normalized_url
                for item in mentions
                for normalized_url in [canonicalize_url(str(item.get("canonical_url") or item.get("url") or ""))]
                if normalized_url
            }
        )

        or_filters: list[dict[str, Any]] = []
        if external_ids:
            or_filters.append({"external_id": {"$in": external_ids}})
        if text_fingerprints:
            or_filters.append({"text_fingerprint": {"$in": text_fingerprints}})
        if content_hashes:
            or_filters.append({"content_hash": {"$in": content_hashes}})
        if canonical_urls:
            or_filters.append({"canonical_url": {"$in": canonical_urls}})

        if not or_filters:
            return signatures

        existing = db.mentions.find(
            {
                "user_id": user_id,
                "$or": or_filters,
            },
            {
                "external_id": 1,
                "text_fingerprint": 1,
                "content_hash": 1,
                "canonical_url": 1,
            },
        )

        for item in existing:
            for key in ["external_id", "text_fingerprint", "content_hash", "canonical_url"]:
                value = str(item.get(key) or "").strip()
                if value:
                    signatures[key].add(value)

        return signatures

    @staticmethod
    def _mention_signature(mention: dict[str, Any]) -> dict[str, str]:
        source = str(mention.get("source") or "").strip().lower()
        author = str(mention.get("author") or "").strip().lower()
        text = str(mention.get("text") or "").strip().lower()

        external_id = str(mention.get("external_id") or "").strip()
        text_fingerprint = str(mention.get("text_fingerprint") or "").strip()
        content_hash = str(mention.get("content_hash") or "").strip()
        canonical_url = canonicalize_url(str(mention.get("canonical_url") or mention.get("url") or ""))
        fallback = f"{source}|{author}|{text[:220]}"

        return {
            "external_id": external_id,
            "text_fingerprint": text_fingerprint,
            "content_hash": content_hash,
            "canonical_url": canonical_url,
            "fallback": fallback,
        }

    @staticmethod
    def _signature_exists(signature: dict[str, str], signatures: dict[str, set[str]]) -> bool:
        for key in ["external_id", "text_fingerprint", "content_hash", "canonical_url", "fallback"]:
            value = signature.get(key) or ""
            if value and value in signatures[key]:
                return True
        return False

    @staticmethod
    def _remember_signature(signature: dict[str, str], signatures: dict[str, set[str]]) -> None:
        for key in ["external_id", "text_fingerprint", "content_hash", "canonical_url", "fallback"]:
            value = signature.get(key) or ""
            if value:
                signatures[key].add(value)

    @staticmethod
    def _rank_mention(mention: dict[str, Any], enrichment: dict[str, Any]) -> float:
        source_weight = float(mention.get("source_priority") or 50)
        quality_component = float(mention.get("quality_score") or 0) * 30

        criticality = str(enrichment.get("criticality") or "").lower()
        criticality_bonus = 0.0
        if criticality == "alta":
            criticality_bonus = 20.0
        elif criticality == "media":
            criticality_bonus = 10.0

        recency_bonus = 0.0
        published_at = mention.get("published_at")
        if isinstance(published_at, datetime):
            now = utcnow()
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=now.tzinfo)
            age_days = max(0.0, (now - published_at).total_seconds() / 86400)
            recency_bonus = max(0.0, 15.0 - min(15.0, age_days))

        return round(source_weight + quality_component + criticality_bonus + recency_bonus, 3)
