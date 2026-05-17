import logging
import re
from typing import Dict, List, Tuple, Optional
from datetime import datetime

try:
    from textblob import TextBlob
except Exception:  # TextBlob fica como fallback opcional
    TextBlob = None

from app.schemas import SentimentType, AspectType
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class SentimentService:
    """Análise local de sentimento/aspectos com foco em PT-BR.

    A LLM configurada é usada para análise executiva e tomada de decisão.
    Para alto volume, esta camada usa regras + léxico + rating para classificar cada menção
    de forma rápida e previsível.
    """

    POSITIVE_WORDS = {
        "ótimo", "otimo", "excelente", "bom", "boa", "maravilhoso", "perfeito", "adorei",
        "gostei", "recomendo", "rápido", "rapido", "eficiente", "atencioso", "limpo",
        "qualidade", "satisfeito", "incrível", "incrivel", "top", "melhor", "amei",
        "great", "good", "excellent", "amazing", "recommend", "fast", "clean", "love",
    }

    NEGATIVE_WORDS = {
        "péssimo", "pessimo", "ruim", "horrível", "horrivel", "terrível", "terrivel",
        "demora", "demorado", "atraso", "atrasada", "atrasado", "espera", "fila",
        "defeito", "quebrado", "cobrança", "cobranca", "indevida", "caro", "lento",
        "não recomendo", "nao recomendo", "problema", "reclamação", "reclamacao",
        "ninguém resolveu", "ninguem resolveu", "cancelar", "fraude", "estorno",
        "pior", "decepcionado", "decepção", "decepcao", "sujo", "mal atendimento",
        "bad", "terrible", "awful", "slow", "delay", "late", "broken", "problem",
        "expensive", "complaint", "worst", "dirty",
    }

    NEGATORS = {"não", "nao", "nunca", "jamais", "sem", "not", "never", "no"}

    CRITICAL_TERMS = {
        "urgency": [
            "urgente", "crítico", "critico", "emergência", "emergencia", "problema grave",
            "falha crítica", "falha critica", "processo", "procon", "justiça", "justica",
            "urgent", "critical", "emergency", "severe", "lawsuit",
        ],
        "complaint": [
            "reclamação", "reclamacao", "queixa", "problema", "defeito", "erro",
            "não resolveu", "nao resolveu", "não recomendo", "nao recomendo",
            "complaint", "issue", "defect", "bug", "broken",
        ],
        "delay": [
            "atraso", "atrasada", "atrasado", "demora", "lento", "espera", "fila",
            "delay", "slow", "late", "waiting", "stuck",
        ],
        "payment": [
            "cobrança", "cobranca", "pagamento", "taxa", "preço", "preco", "caro",
            "indevida", "charge", "payment", "fee", "price", "expensive",
        ],
        "delivery": [
            "entrega", "envio", "logística", "logistica", "transportadora",
            "delivery", "shipping", "logistics", "carrier",
        ],
        "customer_service": [
            "atendimento", "suporte", "vendedor", "gerente", "responsável", "responsavel",
            "service", "support", "staff", "manager", "representative",
        ],
    }

    ASPECT_INDICATORS = {
        AspectType.PRICE: ["preço", "preco", "caro", "barato", "taxa", "valor", "cobrança", "cobranca", "price", "expensive", "cheap", "cost"],
        AspectType.DELIVERY: ["entrega", "envio", "logística", "logistica", "demora", "atraso", "delivery", "shipping", "late"],
        AspectType.CUSTOMER_SERVICE: ["atendimento", "suporte", "vendedor", "gerente", "service", "support", "staff"],
        AspectType.PRODUCT: ["produto", "qualidade", "defeito", "item", "product", "quality", "defect"],
        AspectType.SUPPORT: ["suporte", "ajuda", "técnico", "tecnico", "help", "support", "technical"],
        AspectType.STRUCTURE: ["loja", "restaurante", "ambiente", "estrutura", "limpo", "sujo", "store", "restaurant", "place"],
        AspectType.EXPERIENCE: ["experiência", "experiencia", "satisfação", "satisfacao", "feliz", "triste", "experience", "satisfaction"],
    }

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[a-záéíóúãõâêôç]+", text.lower())

    @staticmethod
    def analyze_sentiment(text: str, rating: Optional[float] = None) -> Tuple[SentimentType, float]:
        """Classifica sentimento usando rating + léxico PT-BR + TextBlob como fallback."""
        try:
            if rating is not None:
                if rating >= 4:
                    return SentimentType.POSITIVE, min(0.95, 0.70 + (rating - 4) * 0.15)
                if rating <= 2:
                    return SentimentType.NEGATIVE, min(0.95, 0.70 + (2 - rating) * 0.15)
                if rating == 3:
                    # rating neutro, mas texto ainda pode indicar problema
                    pass

            text_lower = text.lower()
            tokens = SentimentService._tokenize(text)

            positive_score = 0
            negative_score = 0

            for phrase in SentimentService.POSITIVE_WORDS:
                if " " in phrase and phrase in text_lower:
                    positive_score += 2
            for phrase in SentimentService.NEGATIVE_WORDS:
                if " " in phrase and phrase in text_lower:
                    negative_score += 2

            for i, token in enumerate(tokens):
                prev = tokens[i - 1] if i else ""
                negated = prev in SentimentService.NEGATORS
                if token in SentimentService.POSITIVE_WORDS:
                    negative_score += 1 if negated else 0
                    positive_score += 0 if negated else 1
                if token in SentimentService.NEGATIVE_WORDS:
                    positive_score += 0
                    negative_score += 1

            if negative_score > positive_score:
                diff = negative_score - positive_score
                return SentimentType.NEGATIVE, min(0.95, 0.55 + diff * 0.10)
            if positive_score > negative_score:
                diff = positive_score - negative_score
                return SentimentType.POSITIVE, min(0.95, 0.55 + diff * 0.10)

            if TextBlob:
                polarity = TextBlob(text).sentiment.polarity
                if polarity > 0.12:
                    return SentimentType.POSITIVE, min(abs(polarity), 0.9)
                if polarity < -0.12:
                    return SentimentType.NEGATIVE, min(abs(polarity), 0.9)

            return SentimentType.NEUTRAL, 0.5
        except Exception as e:
            logger.error("✗ Erro ao analisar sentimento: %s", e)
            return SentimentType.NEUTRAL, 0.5

    @staticmethod
    def extract_aspects(text: str) -> Dict[AspectType, float]:
        aspects: Dict[AspectType, float] = {}
        text_lower = text.lower()
        for aspect, keywords in SentimentService.ASPECT_INDICATORS.items():
            hits = sum(1 for keyword in keywords if keyword in text_lower)
            if hits:
                aspects[aspect] = min(hits / 3, 1.0)
        return aspects

    @staticmethod
    def identify_critical_terms(text: str) -> List[str]:
        found: List[str] = []
        text_lower = text.lower()
        for terms in SentimentService.CRITICAL_TERMS.values():
            for term in terms:
                if term in text_lower:
                    found.append(term)
        return sorted(set(found))

    @staticmethod
    def detect_urgency(text: str, sentiment: SentimentType, critical_terms: List[str]) -> float:
        score = 0.0
        if sentiment == SentimentType.NEGATIVE:
            score += 0.35
        if critical_terms:
            score += min(0.45, 0.12 * len(critical_terms))
        if len(text) > 500:
            score += 0.10
        if any(word in text.lower() for word in ["procon", "processo", "justiça", "urgente", "fraude"]):
            score += 0.25
        return min(score, 1.0)

    @staticmethod
    def detect_sarcasm(text: str) -> bool:
        text_lower = text.lower()
        sarcasm_markers = ["só que não", "so que nao", "parabéns", "parabens", "obrigado por nada", "claro"]
        return any(marker in text_lower for marker in sarcasm_markers) and ("!" in text or "não" in text_lower or "nao" in text_lower)

    @staticmethod
    def detect_ambiguity(text: str, aspects: Dict[AspectType, float]) -> bool:
        return any(word in text.lower() for word in ["mas", "porém", "porem", "entretanto", "however", "but"]) or len(aspects) > 2

    @staticmethod
    def calculate_reputation_score(brand_id: str) -> float:
        from app.database import get_db
        db = get_db()
        mentions = list(db.mentions.find({"brand_id": brand_id}))
        if not mentions:
            return 50.0
        total = 0.0
        for m in mentions:
            sentiment = m.get("sentiment")
            urgency = float(m.get("urgency_score", 0))
            if sentiment in ("positivo", "positive"):
                score = 100
            elif sentiment in ("negativo", "negative"):
                score = 15
            else:
                score = 55
            score -= urgency * 35
            total += max(0, min(100, score))
        return round(total / len(mentions), 2)

    @staticmethod
    def extract_themes(text: str, llm_service: Optional[LLMService] = None) -> List[str]:
        tokens = re.findall(r"\b[a-záéíóúãõâêôç]{4,}\b", text.lower())
        stopwords = {
            "para", "com", "que", "uma", "este", "esse", "aquele", "muito", "mais",
            "pela", "pelo", "como", "pois", "também", "tambem", "sobre", "apenas",
            "the", "and", "this", "that", "from", "with", "have",
        }
        freq: Dict[str, int] = {}
        for token in tokens:
            if token not in stopwords:
                freq[token] = freq.get(token, 0) + 1
        return [k for k, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:5]]

    @staticmethod
    async def full_analysis(mention_id: str, text: str, brand_id: str, rating: Optional[float] = None) -> dict:
        sentiment, confidence = SentimentService.analyze_sentiment(text, rating=rating)
        aspects = SentimentService.extract_aspects(text)
        critical_terms = SentimentService.identify_critical_terms(text)
        urgency_score = SentimentService.detect_urgency(text, sentiment, critical_terms)
        return {
            "mention_id": mention_id,
            "brand_id": brand_id,
            "sentiment": sentiment.value,
            "confidence": confidence,
            "aspects": {k.value: v for k, v in aspects.items()},
            "critical_terms": critical_terms,
            "urgency_score": urgency_score,
            "is_sarcasm": SentimentService.detect_sarcasm(text),
            "is_ambiguous": SentimentService.detect_ambiguity(text, aspects),
            "themes": SentimentService.extract_themes(text),
            "created_at": datetime.utcnow(),
        }
