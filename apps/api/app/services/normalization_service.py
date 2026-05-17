import hashlib
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4

MENTION_TYPE_POSITIVE_TERMS = {
    "bom", "boa", "ótimo", "otimo", "excelente", "recomendo", "amei", "adorei",
    "good", "great", "excellent", "love", "ótimos", "fantástico", "maravilhoso",
}
MENTION_TYPE_NEGATIVE_TERMS = {
    "ruim", "péssimo", "péssima", "pessimo", "horrível", "horrivel", "demora", "atraso",
    "caro", "defeito", "problema", "reclamação", "reclamacao", "sujo", "pior", "péssima",
    "bad", "terrible", "slow", "late", "broken", "worst", "expensive", "ódio", "odeio",
}
MENTION_TYPE_QUESTION_TERMS = {
    "como", "onde", "quando", "por que", "porque", "qual", "quais", "alguém", "pode",
    "posso", "devo", "será", "gostaria", "dúvida", "duvida", "ajuda",
}
MENTION_TYPE_REVIEW_TERMS = {
    "avaliação", "avaliacao", "nota", "estrela", "classificação", "qualidade", "recomendo",
    "recomendaria", "comentário", "comentario", "review", "resenha",
}


def _classify_mention_type(text: str, rating: Optional[float] = None) -> str:
    normalized = (text or "").strip().lower()
    if not normalized:
        return "Menção Casual"

    is_question = "?" in normalized or any(term in normalized for term in MENTION_TYPE_QUESTION_TERMS)
    if is_question:
        return "Dúvida"

    positive_hits = sum(1 for term in MENTION_TYPE_POSITIVE_TERMS if term in normalized)
    negative_hits = sum(1 for term in MENTION_TYPE_NEGATIVE_TERMS if term in normalized)
    review_hits = sum(1 for term in MENTION_TYPE_REVIEW_TERMS if term in normalized)

    if rating is not None:
        try:
            r = float(rating)
            if len(normalized.split()) >= 20 or review_hits:
                return "Avaliação Completa"
            if r >= 4 and positive_hits >= negative_hits:
                return "Elogio"
            if r <= 2 and negative_hits >= positive_hits:
                return "Reclamação"
        except Exception:
            pass

    if review_hits >= 2 or len(normalized.split()) >= 35:
        return "Avaliação Completa"

    if negative_hits > positive_hits:
        return "Reclamação"

    if positive_hits > negative_hits:
        return "Elogio"

    return "Menção Casual"

TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "igshid",
    "ref",
    "ref_src",
}


def utcnow() -> datetime:
    """Retorna datetime UTC padronizado para todos os registros."""
    return datetime.now(timezone.utc)


def make_search_id() -> str:
    """Cria um ID único para cada busca.

    Esse search_id é a chave para evitar misturar dados antigos com dados novos.
    Dashboard, histórico, alertas, CSV e PDF devem sempre filtrar por search_id.
    """
    return str(uuid4())


def canonicalize_url(url: Optional[str]) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return ""

    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""

    host = parsed.netloc.lower()
    if ":" in host:
        host = host.split(":", 1)[0]

    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    clean_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in TRACKING_QUERY_PARAMS
    ]
    clean_query.sort()

    return urlunparse(
        (
            parsed.scheme.lower(),
            host,
            path,
            "",
            urlencode(clean_query, doseq=True),
            "",
        )
    )


def compute_text_fingerprint(*, source: str, author: str, text: str) -> str:
    normalized = f"{source.strip().lower()}|{author.strip().lower()}|{text.strip().lower()[:400]}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_content_hash(*, source: str, author: str, text: str, url: Optional[str] = None) -> str:
    canonical_url = canonicalize_url(url)
    seed = f"{source.strip().lower()}|{author.strip().lower()}|{text.strip().lower()[:500]}|{canonical_url}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            dt = utcnow()
    else:
        dt = utcnow()

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_mention(
    *,
    query: str,
    source: str,
    text: Optional[str],
    author: Optional[str] = None,
    published_at: Optional[Any] = None,
    url: Optional[str] = None,
    rating: Optional[float] = None,
    raw: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Normaliza qualquer item externo em um schema único e rastreável."""
    clean_text = " ".join((text or "").split())
    if not clean_text:
        return None

    dt = _coerce_datetime(published_at)
    raw_payload = raw or {}

    title = " ".join(str(raw_payload.get("title") or "").split())
    body = " ".join(str(raw_payload.get("snippet") or "").split())

    canonical_url = canonicalize_url(
        str(raw_payload.get("canonical_url") or raw_payload.get("url") or url or "")
    )
    normalized_text = clean_text[:5000]
    safe_source = str(source or "unknown").strip().lower()
    safe_author = str(author or raw_payload.get("author") or "desconhecido")

    source_item_id = str(
        raw_payload.get("id")
        or raw_payload.get("source_item_id")
        or raw_payload.get("external_id")
        or ""
    ).strip()

    content_hash = compute_content_hash(
        source=safe_source,
        author=safe_author,
        text=normalized_text,
        url=canonical_url,
    )
    text_fingerprint = compute_text_fingerprint(
        source=safe_source,
        author=safe_author,
        text=normalized_text,
    )

    external_id = str(
        source_item_id
        or raw_payload.get("external_id")
        or canonical_url
        or url
        or content_hash
        or uuid4()
    )

    try:
        source_priority = int(raw_payload.get("source_priority") or 0)
    except (TypeError, ValueError):
        source_priority = 0

    collected_at = _coerce_datetime(raw_payload.get("collected_at") or utcnow())

    return {
        "external_id": external_id,
        "source_item_id": source_item_id or None,
        "query": query,
        "entity": query,
        "source": safe_source,
        "source_priority": source_priority,
        "text": normalized_text,
        "normalized_text": normalized_text,
        "title": title,
        "body": body,
        "author": safe_author,
        "published_at": dt,
        "collected_at": collected_at,
        "url": canonical_url or url,
        "canonical_url": canonical_url or None,
        "rating": rating,
        "mention_type": _classify_mention_type(normalized_text, rating),
        "content_hash": content_hash,
        "text_fingerprint": text_fingerprint,
        "raw": raw_payload,
        "created_at": utcnow(),
    }
