from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class ReportFormat(str, Enum):
    CSV = "csv"
    PDF = "pdf"
    JSON = "json"

class ReportType(str, Enum):
    EXECUTIVE_SUMMARY = "executive_summary"
    DETAILED_ANALYSIS = "detailed_analysis"
    COMPARATIVE = "comparative"
    TREND_ANALYSIS = "trend_analysis"

class ReportBase(BaseModel):
    brand_id: str
    report_type: ReportType
    format: ReportFormat
    title: str
    description: Optional[str] = None
    start_date: datetime
    end_date: datetime
    include_recommendations: bool = True

class ReportCreate(ReportBase):
    pass

class ReportResponse(ReportBase):
    id: Optional[str] = None
    user_id: str
    file_url: Optional[str] = None
    file_key: Optional[str] = None
    status: str = "pending"
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    class Config:
        from_attributes = True

class ExecutiveSummary(BaseModel):
    title: str
    overview: str
    key_metrics: Dict[str, Any]
    top_themes: List[str]
    critical_issues: List[str]
    recommendations: List[str]
    generated_by_ai: bool = True
    generated_at: datetime

class ReportMetrics(BaseModel):
    total_mentions: int
    sentiment_distribution: Dict[str, int]
    average_sentiment_score: float
    top_aspects: Dict[str, int]
    critical_terms_frequency: Dict[str, int]
    source_distribution: Dict[str, int]
    urgency_breakdown: Dict[str, int]
    period: str

class ReportExport(BaseModel):
    report_id: str
    format: ReportFormat
    file_url: str
    file_size: int
    created_at: datetime
    expires_at: Optional[datetime] = None
