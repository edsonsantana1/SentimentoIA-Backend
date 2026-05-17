import hashlib
from typing import Any
from uuid import uuid4

from app.database import get_db
from app.schemas.ingestion import IngestionBatchRequest, IngestionBatchResponse, IngestionBatchSummary, IngestionRejectedItem
from app.services.normalization_service import normalize_mention, utcnow
from app.services.search_service import SearchService


class IngestionService:
    @staticmethod
    def _batch_id() -> str:
        timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
        return f"batch_{timestamp}_{uuid4().hex[:6]}"

    @staticmethod
    def _text_fingerprint(source: str, author_name: str, text: str) -> str:
        normalized = f"{source.lower()}|{author_name.strip().lower()}|{text.strip().lower()[:200]}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _serialize_batch(batch: dict[str, Any], status_counts: dict[str, int] | None = None) -> dict[str, Any]:
        item = SearchService.serialize(batch)
        counts = status_counts or {}
        item["pending_count"] = counts.get("pending", 0)
        item["processing_count"] = counts.get("processing", 0)
        item["processed_count"] = counts.get("processed", 0)
        item["error_count"] = counts.get("error", 0)
        return IngestionBatchSummary(**item).model_dump(mode="json")

    @staticmethod
    def ingest_comments(user_id: str, payload: IngestionBatchRequest) -> IngestionBatchResponse:
        db = get_db()
        if db is None:
            raise RuntimeError("Banco de dados indisponivel")

        now = utcnow()
        batch_id = IngestionService._batch_id()
        received = len(payload.comments)

        batch_doc = {
            "batch_id": batch_id,
            "user_id": user_id,
            "batch_name": payload.batch_name,
            "source": payload.source,
            "channel": payload.channel,
            "brand": payload.brand,
            "locale": payload.locale,
            "status": "queued",
            "received_count": received,
            "accepted_count": 0,
            "rejected_count": 0,
            "pending_count": 0,
            "processing_count": 0,
            "processed_count": 0,
            "error_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        db.comment_batches.insert_one(batch_doc)

        accepted_docs: list[dict[str, Any]] = []
        rejected_items: list[IngestionRejectedItem] = []

        for comment in payload.comments:
            fingerprint = IngestionService._text_fingerprint(payload.source, comment.author_name, comment.text)

            exists_external = db.mentions.find_one(
                {
                    "user_id": user_id,
                    "external_id": comment.external_id,
                    "batch_id": {"$exists": True},
                },
                {"_id": 1},
            )
            if exists_external:
                rejected_items.append(
                    IngestionRejectedItem(
                        external_id=comment.external_id,
                        reason="external_id ja existe para este usuario",
                    )
                )
                continue

            exists_fingerprint = db.mentions.find_one(
                {
                    "user_id": user_id,
                    "text_fingerprint": fingerprint,
                    "batch_id": {"$exists": True},
                },
                {"_id": 1},
            )
            if exists_fingerprint:
                rejected_items.append(
                    IngestionRejectedItem(
                        external_id=comment.external_id,
                        reason="comentario duplicado por fingerprint textual",
                    )
                )
                continue

            normalized = normalize_mention(
                query=payload.brand,
                source=payload.source,
                text=comment.text,
                author=comment.author_name,
                published_at=comment.created_at,
                rating=comment.rating,
                raw={
                    "metadata": comment.metadata,
                    "tags": comment.tags,
                    "batch_name": payload.batch_name,
                    "channel": payload.channel,
                },
            )

            if not normalized:
                rejected_items.append(
                    IngestionRejectedItem(
                        external_id=comment.external_id,
                        reason="comentario invalido sem texto util",
                    )
                )
                continue

            normalized.update(
                {
                    "user_id": user_id,
                    "batch_id": batch_id,
                    "status": "pending",
                    "external_id": comment.external_id,
                    "brand": payload.brand,
                    "channel": payload.channel,
                    "locale": payload.locale,
                    "author_name": comment.author_name,
                    "author_email": comment.author_email or "",
                    "author_phone": comment.author_phone or "",
                    "metadata": comment.metadata,
                    "tags": comment.tags,
                    "text_fingerprint": fingerprint,
                    "llm_eligible": False,
                    "archived": False,
                    "updated_at": now,
                }
            )
            accepted_docs.append(normalized)

        if accepted_docs:
            db.mentions.insert_many(accepted_docs)

        accepted = len(accepted_docs)
        rejected = len(rejected_items)
        status = "queued" if accepted > 0 else "error"

        db.comment_batches.update_one(
            {"batch_id": batch_id, "user_id": user_id},
            {
                "$set": {
                    "status": status,
                    "accepted_count": accepted,
                    "rejected_count": rejected,
                    "pending_count": accepted,
                    "updated_at": utcnow(),
                }
            },
        )

        return IngestionBatchResponse(
            batch_id=batch_id,
            received=received,
            accepted=accepted,
            rejected=rejected,
            status=status,
            rejected_items=rejected_items,
        )

    @staticmethod
    def list_batches(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        db = get_db()
        if db is None:
            raise RuntimeError("Banco de dados indisponivel")

        batches = list(
            db.comment_batches.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
        )
        return [IngestionService._serialize_batch(item) for item in batches]

    @staticmethod
    def get_batch(user_id: str, batch_id: str) -> dict[str, Any] | None:
        db = get_db()
        if db is None:
            raise RuntimeError("Banco de dados indisponivel")

        batch = db.comment_batches.find_one({"user_id": user_id, "batch_id": batch_id})
        if not batch:
            return None

        grouped = db.mentions.aggregate(
            [
                {"$match": {"user_id": user_id, "batch_id": batch_id}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            ]
        )
        status_counts = {item.get("_id") or "unknown": int(item.get("count", 0)) for item in grouped}

        recent_mentions = list(
            db.mentions.find(
                {"user_id": user_id, "batch_id": batch_id},
                {"raw": 0},
            )
            .sort("created_at", -1)
            .limit(20)
        )

        output = IngestionService._serialize_batch(batch, status_counts=status_counts)
        output["recent_mentions"] = SearchService.serialize_many(recent_mentions)
        return output
