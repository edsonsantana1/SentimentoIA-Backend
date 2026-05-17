from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth_utils import get_current_user
from app.schemas.ingestion import IngestionBatchRequest, IngestionBatchResponse
from app.services.ingestion_service import IngestionService

router = APIRouter()


@router.post("/comments", response_model=IngestionBatchResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_comments(
    payload: IngestionBatchRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user.get("_id") or current_user.get("id"))
    try:
        return IngestionService.ingest_comments(user_id=user_id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/batches")
async def list_ingestion_batches(
    limit: int = Query(20, ge=1, le=100),
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user.get("_id") or current_user.get("id"))
    return {
        "items": IngestionService.list_batches(user_id=user_id, limit=limit),
    }


@router.get("/batches/{batch_id}")
async def get_ingestion_batch(
    batch_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user.get("_id") or current_user.get("id"))
    result = IngestionService.get_batch(user_id=user_id, batch_id=batch_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch nao encontrado")
    return result
