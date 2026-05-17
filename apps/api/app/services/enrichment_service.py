from collections import Counter
from typing import Any


POSITIVE_TERMS = {
    "bom", "boa", "ótimo", "otimo", "excelente", "recomendo", "amei", "adorei",
    "rápido", "rapido", "qualidade", "atencioso", "good", "great", "excellent", "love"
}

NEGATIVE_TERMS = {
    "ruim", "péssimo", "pessimo", "horrível", "horrivel", "demora", "atraso",
    "caro", "defeito", "problema", "reclamação", "reclamacao", "sujo", "pior",
    "bad", "terrible", "slow", "late", "broken", "worst", "expensive"
}

CRITICAL_TERMS = {
    "procon", "processo", "fraude", "golpe", "cancelar", "urgente", "crítico",
    "critico", "risco", "vazamento", "lawsuit", "fraud", "urgent", "critical"
}

ASPECT_MAP = {
    "preço": ["preço", "preco", "caro", "barato", "valor", "price", "expensive"],
    "entrega": ["entrega", "envio", "atraso", "delivery", "shipping", "late"],
    "atendimento": ["atendimento", "suporte", "vendedor", "service", "support", "staff"],
    "produto": ["produto", "qualidade", "defeito", "product", "quality", "broken"],
    "experiência": ["experiência", "experiencia", "ambiente", "loja", "experience", "store"],
}


class EnrichmentService:
    """Enriquece menções com sentimento, criticidade, score e tendência.

    É ML leve/regra de negócio: rápido, barato e estável.
    A LLM entra depois para resumo executivo e decisão.
    """

    @staticmethod
    def analyze_mention(text: str, rating: float | None = None) -> dict[str, Any]:
        lower = (text or "").lower()

        pos = sum(1 for w in POSITIVE_TERMS if w in lower)
        neg = sum(1 for w in NEGATIVE_TERMS if w in lower)

        # Rating explicito ajuda a calibrar sentimento quando a fonte fornecer nota.
        if rating is not None:
            try:
                r = float(rating)
                if r >= 4:
                    pos += 2
                elif r <= 2:
                    neg += 2
            except Exception:
                pass

        if neg > pos:
            sentiment = "negativo"
            confidence = min(0.95, 0.55 + (neg - pos) * 0.1)
        elif pos > neg:
            sentiment = "positivo"
            confidence = min(0.95, 0.55 + (pos - neg) * 0.1)
        else:
            sentiment = "neutro"
            confidence = 0.55

        critical_terms = [w for w in CRITICAL_TERMS if w in lower]
        urgency_score = min(1.0, (len(critical_terms) * 0.35) + (0.35 if sentiment == "negativo" else 0))

        if urgency_score >= 0.75:
            criticality = "alta"
        elif urgency_score >= 0.4:
            criticality = "media"
        else:
            criticality = "baixa"

        aspects = []
        for aspect, terms in ASPECT_MAP.items():
            if any(t in lower for t in terms):
                aspects.append(aspect)

        if sentiment == "positivo":
            reputation_score = 80 + confidence * 20
        elif sentiment == "negativo":
            reputation_score = 50 - confidence * 40
        else:
            reputation_score = 55

        return {
            "sentiment": sentiment,
            "confidence": round(confidence, 3),
            "critical_terms": critical_terms,
            "criticality": criticality,
            "urgency_score": round(urgency_score, 3),
            "aspects": aspects,
            "reputation_score": round(max(0, min(100, reputation_score)), 2),
        }

    @staticmethod
    def aggregate(mentions: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(mentions)
        sentiments = Counter(m.get("sentiment", "neutro") for m in mentions)
        sources = Counter(m.get("source", "unknown") for m in mentions)
        aspects = Counter(a for m in mentions for a in m.get("aspects", []))
        critical = sum(1 for m in mentions if m.get("criticality") == "alta")

        avg_score = round(sum(float(m.get("reputation_score", 50)) for m in mentions) / total, 2) if total else 0
        avg_urgency = round(sum(float(m.get("urgency_score", 0)) for m in mentions) / total, 3) if total else 0

        if total == 0:
            trend = "indefinido"
        elif sentiments.get("negativo", 0) > sentiments.get("positivo", 0):
            trend = "caindo"
        elif sentiments.get("positivo", 0) > sentiments.get("negativo", 0):
            trend = "subindo"
        else:
            trend = "estável"

        return {
            "total_mentions": total,
            "sentiment_distribution": dict(sentiments),
            "source_distribution": dict(sources),
            "top_aspects": dict(aspects.most_common(10)),
            "critical_mentions": critical,
            "average_urgency": avg_urgency,
            "reputation_score": avg_score,
            "trend": trend,
        }
