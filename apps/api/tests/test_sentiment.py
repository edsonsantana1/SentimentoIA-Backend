from app.services.enrichment_service import EnrichmentService
from app.services.sentiment_service import SentimentService


def test_sentiment_service_positive() -> None:
    sentiment, confidence = SentimentService.analyze_sentiment(
        "Excelente produto, entrega rapida e atendimento perfeito.",
        rating=5,
    )

    assert sentiment.value == "positive"
    assert 0 <= confidence <= 1


def test_sentiment_service_negative() -> None:
    sentiment, confidence = SentimentService.analyze_sentiment(
        "Pessimo atendimento, atraso e problema grave no pedido.",
        rating=1,
    )

    assert sentiment.value == "negative"
    assert 0 <= confidence <= 1


def test_enrichment_structured_fields() -> None:
    analysis = EnrichmentService.analyze_mention(
        "Vou abrir processo no procon por atraso e cobranca indevida.",
        rating=1,
    )

    assert analysis["sentiment"] in {"positivo", "neutro", "negativo"}
    assert isinstance(analysis["critical_terms"], list)
    assert analysis["criticality"] in {"baixa", "media", "alta"}
    assert isinstance(analysis["aspects"], list)
    assert 0 <= float(analysis["urgency_score"]) <= 1
    assert 0 <= float(analysis["reputation_score"]) <= 100
