import logging
from fastapi import APIRouter, HTTPException, status, Query
from typing import Optional
from datetime import datetime
from bson import ObjectId
from app.schemas import ReportCreate, ReportResponse, ReportFormat, ReportType
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/generate", response_model=ReportResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_report(report_data: ReportCreate, user_id: str):
    """Inicia geração de um relatório"""
    try:
        db = get_db()
        
        # Validar datas
        if report_data.start_date >= report_data.end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Data de início deve ser anterior à data de fim"
            )
        
        # Criar documento de relatório
        report_doc = {
            "user_id": user_id,
            "brand_id": report_data.brand_id,
            "report_type": report_data.report_type.value,
            "format": report_data.format.value,
            "title": report_data.title,
            "description": report_data.description,
            "start_date": report_data.start_date,
            "end_date": report_data.end_date,
            "include_recommendations": report_data.include_recommendations,
            "status": "pending",
            "file_url": None,
            "file_key": None,
            "created_at": datetime.utcnow(),
            "completed_at": None,
            "error_message": None,
        }
        
        result = db.reports.insert_one(report_doc)
        report_doc["_id"] = result.inserted_id
        
        # Log de auditoria
        from app.services import AuthService
        AuthService.log_audit(
            user_id=user_id,
            action="report_generation_started",
            details={"report_id": str(result.inserted_id), "brand_id": report_data.brand_id}
        )
        
        logger.info(f"✓ Geração de relatório iniciada: {result.inserted_id}")
        
        return ReportResponse(
            id=str(report_doc["_id"]),
            user_id=report_doc["user_id"],
            brand_id=report_doc["brand_id"],
            report_type=report_doc["report_type"],
            format=report_doc["format"],
            title=report_doc["title"],
            description=report_doc["description"],
            start_date=report_doc["start_date"],
            end_date=report_doc["end_date"],
            include_recommendations=report_doc["include_recommendations"],
            status=report_doc["status"],
            created_at=report_doc["created_at"],
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ Erro ao gerar relatório: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao gerar relatório"
        )

@router.get("/list")
async def list_reports(
    user_id: str,
    brand_id: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
):
    """Lista relatórios do usuário"""
    try:
        db = get_db()
        
        # Construir filtro
        filter_query = {"user_id": user_id}
        if brand_id:
            filter_query["brand_id"] = brand_id
        
        # Buscar relatórios
        reports = list(
            db.reports.find(filter_query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        
        total = db.reports.count_documents(filter_query)
        
        return {
            "total": total,
            "limit": limit,
            "skip": skip,
            "reports": [
                {
                    "id": str(r["_id"]),
                    "brand_id": r["brand_id"],
                    "report_type": r["report_type"],
                    "format": r["format"],
                    "title": r["title"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "completed_at": r.get("completed_at"),
                    "file_url": r.get("file_url"),
                }
                for r in reports
            ]
        }
    
    except Exception as e:
        logger.error(f"✗ Erro ao listar relatórios: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao listar relatórios"
        )

@router.get("/{report_id}")
async def get_report(report_id: str, user_id: str):
    """Obtém detalhes de um relatório"""
    try:
        db = get_db()
        
        report = db.reports.find_one({
            "_id": ObjectId(report_id),
            "user_id": user_id
        })
        
        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Relatório não encontrado"
            )
        
        return {
            "id": str(report["_id"]),
            "brand_id": report["brand_id"],
            "report_type": report["report_type"],
            "format": report["format"],
            "title": report["title"],
            "description": report.get("description"),
            "status": report["status"],
            "start_date": report["start_date"],
            "end_date": report["end_date"],
            "include_recommendations": report["include_recommendations"],
            "file_url": report.get("file_url"),
            "created_at": report["created_at"],
            "completed_at": report.get("completed_at"),
            "error_message": report.get("error_message"),
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ Erro ao obter relatório: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter relatório"
        )

@router.delete("/{report_id}")
async def delete_report(report_id: str, user_id: str):
    """Deleta um relatório"""
    try:
        db = get_db()
        
        result = db.reports.delete_one({
            "_id": ObjectId(report_id),
            "user_id": user_id
        })
        
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Relatório não encontrado"
            )
        
        # Log de auditoria
        from app.services import AuthService
        AuthService.log_audit(
            user_id=user_id,
            action="report_deleted",
            details={"report_id": report_id}
        )
        
        return {"status": "success", "message": "Relatório deletado"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ Erro ao deletar relatório: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao deletar relatório"
        )
