from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class SentimentType(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"

class CriticalityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class AspectType(str, Enum):
    PRICE = "price"
    DELIVERY = "delivery"
    CUSTOMER_SERVICE = "customer_service"
    PRODUCT = "product"
    SUPPORT = "support"
    STRUCTURE = "structure"
    EXPERIENCE = "experience"

class MentionSource(str, Enum):
    RECLAMEAQUI = "reclameaqui"
    REDDIT = "reddit"
    MASTODON = "mastodon"
    WEB = "web"

    # Legado/compatibilidade
    GOOGLE = "google"
    X = "x"
    TWITTER = "twitter"

    # Canais opcionais
    TRUSTPILOT = "trustpilot"
    YELP = "yelp"
    TRIPADVISOR = "tripadvisor"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"

class MentionType(str, Enum):
    REVIEW = "Avaliação Completa"
    COMPLAINT = "Reclamação"
    PRAISE = "Elogio"
    QUESTION = "Dúvida"
    CASUAL = "Menção Casual"


class MentionBase(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    source: MentionSource
    mention_type: MentionType = MentionType.CASUAL
    author: Optional[str] = None
    author_url: Optional[str] = None
    rating: Optional[float] = Field(None, ge=0, le=5)
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    location: Optional[str] = None

class MentionCreate(MentionBase):
    brand_id: str

class MentionResponse(MentionBase):
    id: Optional[str] = None
    brand_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class SentimentAnalysisBase(BaseModel):
    sentiment: SentimentType
    confidence: float = Field(..., ge=0, le=1)
    aspects: Dict[AspectType, float] = {}
    critical_terms: List[str] = []
    urgency_score: float = Field(..., ge=0, le=1)
    is_sarcasm: bool = False
    is_ambiguous: bool = False
    themes: List[str] = []

class SentimentAnalysisCreate(SentimentAnalysisBase):
    mention_id: str

class SentimentAnalysisResponse(SentimentAnalysisBase):
    id: Optional[str] = None
    mention_id: str
    brand_id: str
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class ReputationScore(BaseModel):
    brand_id: str
    overall_score: float = Field(..., ge=0, le=100)
    sentiment_distribution: Dict[str, float] = {}
    total_mentions: int = 0
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    critical_count: int = 0
    average_rating: Optional[float] = None
    last_updated: datetime
    
    class Config:
        from_attributes = True

class MentionFilter(BaseModel):
    brand_id: Optional[str] = None
    source: Optional[MentionSource] = None
    sentiment: Optional[SentimentType] = None
    criticality: Optional[CriticalityLevel] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    location: Optional[str] = None
    min_rating: Optional[float] = None
    max_rating: Optional[float] = None
