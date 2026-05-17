import logging
from fastapi import APIRouter, HTTPException, status, Query
from typing import Optional, List
from datetime import datetime
from bson import ObjectId
from app.schemas import (
    MentionCreate,
    MentionResponse,
    SentimentAnalysisResponse,
    MentionFilter,
    ReputationScore,
)
from app.services import SentimentService
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/analyze", response_model=SentimentAnalysisResponse)
async def analyze_mention(mention_data: MentionCreate):
    """Analisa uma menção e retorna classificação de sentimento"""
    try:
        db = get_db()
        
        # Validar comprimento do texto
        if len(mention_data.text) > 5000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Texto excede o limite de 5000 caracteres"
            )
        
        if len(mention_data.text) < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Texto não pode estar vazio"
            )
        
        # Realizar análise completa
        analysis = await SentimentService.full_analysis(
            mention_id=str(ObjectId()),
            text=mention_data.text,
            brand_id=mention_data.brand_id
        )
        
        # Salvar análise no banco
        result = db.sentiment_analysis.insert_one(analysis)
        analysis["_id"] = result.inserted_id
        
        return SentimentAnalysisResponse(
            id=str(analysis["_id"]),
            mention_id=analysis["mention_id"],
            brand_id=analysis["brand_id"],
            sentiment=analysis["sentiment"],
            confidence=analysis["confidence"],
            aspects={k: v for k, v in analysis["aspects"].items()},
            critical_terms=analysis["critical_terms"],
            urgency_score=analysis["urgency_score"],
            is_sarcasm=analysis["is_sarcasm"],
            is_ambiguous=analysis["is_ambiguous"],
            created_at=analysis["created_at"],
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ Erro ao analisar menção: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao analisar menção"
        )

@router.get("/brand/{brand_id}/reputation", response_model=ReputationScore)
async def get_reputation_score(brand_id: str):
    """Obtém score de reputação de uma marca"""
    try:
        db = get_db()
        
        # Calcular score de reputação
        score = SentimentService.calculate_reputation_score(brand_id)
        
        # Obter estatísticas
        analyses = list(db.sentiment_analysis.find({"brand_id": brand_id}))
        
        if not analyses:
            return ReputationScore(
                brand_id=brand_id,
                overall_score=50.0,
                sentiment_distribution={},
                total_mentions=0,
                positive_count=0,
                neutral_count=0,
                negative_count=0,
                critical_count=0,
                last_updated=datetime.utcnow(),
            )
        
        # Contar sentimentos
        sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
        critical_count = 0
        
        for analysis in analyses:
            sentiment = analysis.get("sentiment", "neutral")
            sentiment_counts[sentiment] = sentiment_counts.get(sentiment, 0) + 1
            
            if analysis.get("urgency_score", 0) > 0.7:
                critical_count += 1
        
        # Calcular média de rating
        mentions = list(db.mentions.find({"brand_id": brand_id, "rating": {"$exists": True}}))
        avg_rating = None
        if mentions:
            avg_rating = sum(m.get("rating", 0) for m in mentions) / len(mentions)
        
        return ReputationScore(
            brand_id=brand_id,
            overall_score=score,
            sentiment_distribution=sentiment_counts,
            total_mentions=len(analyses),
            positive_count=sentiment_counts.get("positive", 0),
            neutral_count=sentiment_counts.get("neutral", 0),
            negative_count=sentiment_counts.get("negative", 0),
            critical_count=critical_count,
            average_rating=avg_rating,
            last_updated=datetime.utcnow(),
        )
    
    except Exception as e:
        logger.error(f"✗ Erro ao obter score de reputação: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter score de reputação"
        )

@router.get("/brand/{brand_id}/summary")
async def get_brand_summary(
    brand_id: str,
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """Obtém resumo de análises de uma marca"""
    try:
        db = get_db()
        
        # Construir filtro
        filter_query = {"brand_id": brand_id}
        
        if start_date or end_date:
            filter_query["created_at"] = {}
            if start_date:
                filter_query["created_at"]["$gte"] = start_date
            if end_date:
                filter_query["created_at"]["$lte"] = end_date
        
        # Buscar análises
        analyses = list(db.sentiment_analysis.find(filter_query))
        
        if not analyses:
            return {
                "brand_id": brand_id,
                "total_analyses": 0,
                "sentiment_distribution": {},
                "top_themes": [],
                "critical_issues": [],
                "average_urgency": 0.0,
            }
        
        # Calcular estatísticas
        sentiment_dist = {"positive": 0, "neutral": 0, "negative": 0}
        all_themes = []
        critical_issues = []
        total_urgency = 0
        
        for analysis in analyses:
            sentiment = analysis.get("sentiment", "neutral")
            sentiment_dist[sentiment] = sentiment_dist.get(sentiment, 0) + 1
            
            all_themes.extend(analysis.get("themes", []))
            
            if analysis.get("urgency_score", 0) > 0.7:
                critical_issues.extend(analysis.get("critical_terms", []))
            
            total_urgency += analysis.get("urgency_score", 0)
        
        # Top themes
        theme_freq = {}
        for theme in all_themes:
            theme_freq[theme] = theme_freq.get(theme, 0) + 1
        
        top_themes = sorted(theme_freq.items(), key=lambda x: x[1], reverse=True)[:10]
        top_themes = [theme[0] for theme in top_themes]
        
        # Critical issues
        critical_freq = {}
        for issue in critical_issues:
            critical_freq[issue] = critical_freq.get(issue, 0) + 1
        
        critical_issues = sorted(critical_freq.items(), key=lambda x: x[1], reverse=True)[:5]
        critical_issues = [issue[0] for issue in critical_issues]
        
        return {
            "brand_id": brand_id,
            "total_analyses": len(analyses),
            "sentiment_distribution": sentiment_dist,
            "top_themes": top_themes,
            "critical_issues": critical_issues,
            "average_urgency": total_urgency / len(analyses) if analyses else 0,
        }
    
    except Exception as e:
        logger.error(f"✗ Erro ao obter resumo da marca: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter resumo da marca"
        )

@router.get("/brand/{brand_id}/critical")
async def get_critical_mentions(brand_id: str, limit: int = Query(10, ge=1, le=100)):
    """Obtém menções críticas de uma marca"""
    try:
        db = get_db()
        
        # Buscar análises críticas
        critical_analyses = list(
            db.sentiment_analysis.find(
                {"brand_id": brand_id, "urgency_score": {"$gt": 0.7}}
            ).sort("urgency_score", -1).limit(limit)
        )
        
        results = []
        for analysis in critical_analyses:
            mention = db.mentions.find_one({"_id": ObjectId(analysis["mention_id"])})
            
            if mention:
                results.append({
                    "mention_id": str(mention["_id"]),
                    "text": mention.get("text", ""),
                    "source": mention.get("source", ""),
                    "sentiment": analysis.get("sentiment", ""),
                    "urgency_score": analysis.get("urgency_score", 0),
                    "critical_terms": analysis.get("critical_terms", []),
                    "created_at": mention.get("published_at", mention.get("created_at")),
                })
        
        return {
            "brand_id": brand_id,
            "total_critical": len(critical_analyses),
            "mentions": results,
        }
    
    except Exception as e:
        logger.error(f"✗ Erro ao obter menções críticas: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao obter menções críticas"
        )
