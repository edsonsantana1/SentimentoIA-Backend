import logging
from fastapi import APIRouter, HTTPException, status, Query, Depends
from typing import Optional
from datetime import datetime
from bson import ObjectId
from app.database import get_db
from app.schemas import UserRole

logger = logging.getLogger(__name__)

router = APIRouter()

def verify_admin(user_id: str):
    """Verifica se o usuário é administrador"""
    db = get_db()
    user = db.users.find_one({"_id": ObjectId(user_id)})
    
    if not user or user.get("role") != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado. Privilégios de administrador necessários"
        )
    
    return user

@router.get("/users", dependencies=[Depends(verify_admin)])
async def list_users(
    user_id: str,
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
):
    """Lista todos os usuários (admin only)"""
    try:
        db = get_db()
        
        users = list(
            db.users.find({}, {"password_hash": 0, "mfa_secret": 0})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        
        total = db.users.count_documents({})
        
        return {
            "total": total,
            "limit": limit,
            "skip": skip,
            "users": [
                {
                    "id": str(u["_id"]),
                    "email": u["email"],
                    "name": u["name"],
                    "phone": u.get("phone"),
                    "role": u.get("role", "user"),
                    "mfa_enabled": u.get("mfa_enabled", False),
                    "is_active": u.get("is_active", True),
                    "created_at": u.get("created_at"),
                    "last_signed_in": u.get("last_signed_in"),
                }
                for u in users
            ]
        }
    
    except Exception as e:
        logger.error(f"✗ Erro ao listar usuários: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao listar usuários"
        )

@router.get("/audit-logs", dependencies=[Depends(verify_admin)])
async def get_audit_logs(
    user_id: str,
    action: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
):
    """Obtém logs de auditoria (admin only)"""
    try:
        db = get_db()
        
        filter_query = {}
        if action:
            filter_query["action"] = action
        
        logs = list(
            db.audit_logs.find(filter_query)
            .sort("timestamp", -1)
            .skip(skip)
            .limit(limit)
        )
        
        total = db.audit_logs.count_documents(filter_query)
        
        return {
            "total": total,
            "limit": limit,
            "skip": skip,
            "logs": [
                {
                    "id": str(l["_id"]),
                    "user_id": l.get("user_id"),
                    "action": l.get("action"),
                    "details": l.get("details"),
                    "ip_address": l.get("ip_address"),
                    "timestamp": l.get("timestamp"),
                }
                for l in logs
            ]
        }
    
    except Exception as e:
        logger.error(f"✗ Erro ao obter logs de auditoria: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter logs de auditoria"
        )

@router.get("/system-stats", dependencies=[Depends(verify_admin)])
async def get_system_stats(user_id: str):
    """Obtém estatísticas do sistema (admin only)"""
    try:
        db = get_db()
        
        total_users = db.users.count_documents({})
        total_brands = db.brands.count_documents({})
        total_mentions = db.mentions.count_documents({})
        total_analyses = db.sentiment_analysis.count_documents({})
        total_reports = db.reports.count_documents({})
        
        # Menções dos últimos 7 dias
        from datetime import timedelta
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_mentions = db.mentions.count_documents({"created_at": {"$gte": seven_days_ago}})
        
        # Taxa de sucesso de relatórios
        total_reports_completed = db.reports.count_documents({"status": "completed"})
        report_success_rate = (total_reports_completed / total_reports * 100) if total_reports > 0 else 0
        
        return {
            "total_users": total_users,
            "total_brands": total_brands,
            "total_mentions": total_mentions,
            "total_analyses": total_analyses,
            "total_reports": total_reports,
            "recent_mentions_7_days": recent_mentions,
            "report_success_rate": round(report_success_rate, 2),
            "timestamp": datetime.utcnow(),
        }
    
    except Exception as e:
        logger.error(f"✗ Erro ao obter estatísticas do sistema: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter estatísticas do sistema"
        )

@router.post("/users/{target_user_id}/toggle-active", dependencies=[Depends(verify_admin)])
async def toggle_user_active(user_id: str, target_user_id: str):
    """Ativa ou desativa um usuário (admin only)"""
    try:
        db = get_db()
        
        user = db.users.find_one({"_id": ObjectId(target_user_id)})
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuário não encontrado"
            )
        
        new_status = not user.get("is_active", True)
        
        db.users.update_one(
            {"_id": ObjectId(target_user_id)},
            {"$set": {"is_active": new_status}}
        )
        
        # Log de auditoria
        from app.services import AuthService
        AuthService.log_audit(
            user_id=user_id,
            action="user_status_changed",
            details={"target_user_id": target_user_id, "new_status": new_status}
        )
        
        return {
            "status": "success",
            "user_id": target_user_id,
            "is_active": new_status
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ Erro ao alterar status do usuário: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao alterar status do usuário"
        )

@router.get("/alerts", dependencies=[Depends(verify_admin)])
async def get_system_alerts(
    user_id: str,
    severity: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Obtém alertas do sistema (admin only)"""
    try:
        db = get_db()
        
        filter_query = {}
        if severity:
            filter_query["severity"] = severity
        
        alerts = list(
            db.alerts.find(filter_query)
            .sort("created_at", -1)
            .limit(limit)
        )
        
        return {
            "total": len(alerts),
            "alerts": [
                {
                    "id": str(a["_id"]),
                    "brand_id": a.get("brand_id"),
                    "severity": a.get("severity"),
                    "message": a.get("message"),
                    "created_at": a.get("created_at"),
                    "resolved": a.get("resolved", False),
                }
                for a in alerts
            ]
        }
    
    except Exception as e:
        logger.error(f"✗ Erro ao obter alertas: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter alertas"
        )
