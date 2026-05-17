from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field
from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class SentimentType(str, Enum):
    POSITIVO = "positivo"
    NEUTRO = "neutro"
    NEGATIVO = "negativo"


class CriticalityLevel(str, Enum):
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"


class DataSource(str, Enum):
    RECLAMEAQUI = "reclameaqui"
    REDDIT = "reddit"
    MASTODON = "mastodon"
    WEB = "web"

    # Legado
    GOOGLE = "google"
    X = "x"
    TWITTER = "twitter"

    # Compatibilidade adicional
    TRUSTPILOT = "trustpilot"
    YELP = "yelp"
    TRIPADVISOR = "tripadvisor"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"


class ScrapeSource(str, Enum):
    RECLAMEAQUI = "reclameaqui"
    GOOGLE = "google"
    REDDIT = "reddit"
    MASTODON = "mastodon"
    WEB = "web"
    X = "x"
    TWITTER = "twitter"


# ==================== USER MODELS ====================

class UserBase(BaseModel):
    email: EmailStr
    name: str
    phone: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None


class UserInDB(UserBase):
    id: str = Field(alias="_id")
    password_hash: str
    role: UserRole = UserRole.USER
    mfa_enabled: bool = False
    mfa_secret: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_signed_in: Optional[datetime] = None
    is_active: bool = True

    class Config:
        populate_by_name = True


class UserResponse(UserBase):
    id: str = Field(alias="_id")
    role: UserRole
    mfa_enabled: bool
    created_at: datetime
    is_active: bool

    class Config:
        populate_by_name = True


# ==================== BRAND MODELS ====================

class BrandBase(BaseModel):
    name: str
    description: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None


class BrandCreate(BrandBase):
    pass


class BrandInDB(BrandBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True


class BrandResponse(BrandBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime

    class Config:
        populate_by_name = True


# ==================== MENTION MODELS ====================

class MentionBase(BaseModel):
    text: str
    source: DataSource
    source_id: str
    author: Optional[str] = None
    rating: Optional[float] = None
    url: Optional[str] = None
    published_at: datetime


class MentionCreate(MentionBase):
    brand_id: str


class MentionInDB(MentionBase):
    id: str = Field(alias="_id")
    brand_id: str
    collected_at: datetime

    class Config:
        populate_by_name = True


class MentionResponse(MentionBase):
    id: str = Field(alias="_id")
    brand_id: str
    collected_at: datetime

    class Config:
        populate_by_name = True


# ==================== SENTIMENT ANALYSIS MODELS ====================

class SentimentAnalysisBase(BaseModel):
    sentiment: SentimentType
    confidence: float = Field(ge=0, le=1)
    aspects: List[str] = []
    criticality: CriticalityLevel
    urgency_score: float = Field(ge=0, le=1)
    reputation_score: float = Field(ge=0, le=10)
    key_terms: List[str] = []


class SentimentAnalysisCreate(SentimentAnalysisBase):
    mention_id: str


class SentimentAnalysisInDB(SentimentAnalysisBase):
    id: str = Field(alias="_id")
    mention_id: str
    analyzed_at: datetime

    class Config:
        populate_by_name = True


class SentimentAnalysisResponse(SentimentAnalysisBase):
    id: str = Field(alias="_id")
    mention_id: str
    analyzed_at: datetime

    class Config:
        populate_by_name = True


# ==================== REPORT MODELS ====================

class ReportBase(BaseModel):
    title: str
    description: Optional[str] = None
    report_type: str = "executive"


class ReportCreate(ReportBase):
    brand_id: str
    mention_ids: List[str] = []


class ReportInDB(ReportBase):
    id: str = Field(alias="_id")
    user_id: str
    brand_id: str
    mention_ids: List[str]
    pdf_url: Optional[str] = None
    csv_url: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True


class ReportResponse(ReportBase):
    id: str = Field(alias="_id")
    user_id: str
    brand_id: str
    pdf_url: Optional[str] = None
    csv_url: Optional[str] = None
    created_at: datetime

    class Config:
        populate_by_name = True


# ==================== ALERT MODELS ====================

class AlertBase(BaseModel):
    title: str
    message: str
    alert_type: str
    severity: CriticalityLevel


class AlertCreate(AlertBase):
    brand_id: str
    mention_id: Optional[str] = None


class AlertInDB(AlertBase):
    id: str = Field(alias="_id")
    user_id: str
    brand_id: str
    mention_id: Optional[str] = None
    is_read: bool = False
    created_at: datetime

    class Config:
        populate_by_name = True


class AlertResponse(AlertBase):
    id: str = Field(alias="_id")
    user_id: str
    brand_id: str
    is_read: bool
    created_at: datetime

    class Config:
        populate_by_name = True


# ==================== AUDIT LOG MODELS ====================

class AuditLogBase(BaseModel):
    action: str
    resource: str
    details: Optional[dict] = None


class AuditLogCreate(AuditLogBase):
    user_id: str
    ip_address: Optional[str] = None


class AuditLogInDB(AuditLogBase):
    id: str = Field(alias="_id")
    user_id: str
    ip_address: Optional[str] = None
    created_at: datetime

    class Config:
        populate_by_name = True


# ==================== AUTH MODELS ====================

class TokenData(BaseModel):
    sub: str
    exp: datetime
    role: UserRole


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


class MFASetupResponse(BaseModel):
    secret: str
    qr_code: str


class MFAVerifyRequest(BaseModel):
    token: str


# ==================== SEARCH MODELS ====================

class SearchRequest(BaseModel):
    brand_name: str
    sources: List[DataSource] = Field(
        default_factory=lambda: [
            DataSource.RECLAMEAQUI,
            DataSource.REDDIT,
            DataSource.WEB,
        ]
    )
    period_days: int = 30
    sentiment_filter: Optional[SentimentType] = None
    locality: Optional[str] = None
    min_criticality: Optional[CriticalityLevel] = None
    replace_existing: bool = True


class SearchResponse(BaseModel):
    search_id: str
    brand_name: str
    status: str
    mentions_found: int
    created_at: datetime


class ScrapeRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=160)
    sources: List[ScrapeSource] = Field(default_factory=lambda: [
                                        ScrapeSource.RECLAMEAQUI, ScrapeSource.REDDIT, ScrapeSource.WEB])
    limit_per_source: int = Field(default=5, ge=1, le=10)
