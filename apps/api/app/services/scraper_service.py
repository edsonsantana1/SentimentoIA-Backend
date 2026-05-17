import asyncio
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
import hashlib
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.database import get_db
from app.services.normalization_service import canonicalize_url, compute_content_hash, utcnow
from app.services.source_registry_service import SourceRegistryService

logger = logging.getLogger(__name__)


class ScraperService:
    """Scraping com foco em fontes abertas e dedupe incremental persistido."""

    LOW_SIGNAL_TERMS = {
        "javascript",
        "enable javascript",
        "accept cookies",
        "cookie policy",
        "sign in",
        "cadastre-se",
        "faca login",
        "clique aqui",
        "ver mais",
        "read more",
    }

    # Mapeamento de nomes de empresas para slugs corretos do ReclameAqui
    # Atualizar conforme novos slugs forem descobertos
    RECLAMEAQUI_SLUG_MAP = {
        "brahma": "brahma",
        "brahma cerveja": "brahma",
        "ambev": "ambev",
        "vivo": "vivo-celular-fixo-internet-tv",
        "vivo telefonica": "vivo-celular-fixo-internet-tv",
        "vivo telefonica brasil": "vivo-celular-fixo-internet-tv",
        "mercado livre": "mercado-livre",
        "mercadolivre": "mercado-livre",
        "nike": "nike-loja-online",
        "nike brasil": "nike-loja-online",
        "nike loja online": "nike-loja-online",
        "acer": "acer",
        "samsung": "samsung",
        "sony": "sony",
        "lg": "lg-eletronicos",
    }

    @staticmethod
    def _ensure_windows_asyncio_policy() -> None:
        """Garante que a política asyncio correta está ativa no Windows.

        Crítico: sync_playwright() dentro de contexto assíncrono do FastAPI
        requer SelectorEventLoop, não ProactorEventLoop (padrão do Windows).
        """
        if sys.platform == 'win32':
            try:
                current_policy = asyncio.get_event_loop_policy()
                if not isinstance(current_policy, asyncio.WindowsSelectorEventLoopPolicy):
                    asyncio.set_event_loop_policy(
                        asyncio.WindowsSelectorEventLoopPolicy())
                    logger.debug(
                        "Política asyncio reforçada: WindowsSelectorEventLoopPolicy ativo")
            except Exception as exc:
                logger.warning(
                    f"Falha ao reforçar política asyncio no Windows: {exc}")

    @staticmethod
    def scrape(query: str, sources: list[str], limit_per_source: int | None = None, user_id: str = "") -> dict[str, Any]:
        from app.services.insight_service import InsightService
        user_settings = InsightService.get_user_settings(
            user_id=user_id) if user_id else {}
        relevance_threshold = float(user_settings.get(
            'scraper_relevance_threshold', getattr(settings, 'SCRAPER_RELEVANCE_THRESHOLD', 0.1)))
        term = (query or "").strip()
        if not term:
            raise ValueError("Termo de busca obrigatorio")

        normalized_sources, source_errors = SourceRegistryService.normalize_sources(
            sources)
        max_per_source = max(1, int(settings.SCRAPER_MAX_ITEMS_PER_SOURCE))
        limit = max(1, min(max_per_source, int(
            limit_per_source or settings.SCRAPER_DEFAULT_LIMIT)))
        max_total = max(limit, int(settings.SCRAPER_MAX_TOTAL_ITEMS))

        results: dict[str, list[dict[str, Any]]] = {
            source: [] for source in normalized_sources}
        errors: list[dict[str, str]] = list(source_errors)

        handlers = {
            "reclameaqui": ScraperService._scrape_reclameaqui,
            "reddit": ScraperService._scrape_reddit,
            "web": ScraperService._scrape_web,
            # Mantidos para compatibilidade futura. Só serão usados se também
            # estiverem ativos no SourceRegistryService.
            "mastodon": ScraperService._scrape_mastodon,
            "trustpilot": ScraperService._scrape_trustpilot,
            "consumidor": ScraperService._scrape_consumidor,
        }

        worker_count = min(max(1, len(normalized_sources)), 4)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_map = {
                pool.submit(ScraperService._run_source_pipeline, source, term, limit, handlers, user_id, relevance_threshold): source
                for source in normalized_sources
            }
            for future in as_completed(future_map):
                source = future_map[future]
                try:
                    source_items, source_error = future.result()
                    results[source] = source_items
                    if source_error:
                        errors.append(
                            {"source": source, "error": source_error})
                except Exception as exc:
                    logger.exception(
                        "Erro durante scraping da fonte %s", source)
                    results[source] = []
                    errors.append({"source": source, "error": str(exc)})

        total = sum(len(items) for items in results.values())
        if total > max_total:
            results = ScraperService._truncate_total_results(
                results, max_total)
            total = sum(len(items) for items in results.values())

        metadata = {
            "sources": SourceRegistryService.source_metadata(),
            "max_total_items": max_total,
            "incremental_mode": True,
        }

        return {
            "query": term,
            "sources": normalized_sources,
            "limit_per_source": limit,
            "total": total,
            "results": results,
            "errors": errors,
            "metadata": metadata,
        }

    @staticmethod
    def _run_source_pipeline(
        source: str,
        query: str,
        limit: int,
        handlers: dict[str, Any],
        user_id: str,
        relevance_threshold: float
    ) -> tuple[list[dict[str, Any]], str | None]:
        handler = handlers.get(source)
        if handler is None:
            return [], "Fonte sem handler de scraping"

        raw_items, source_error = handler(query, limit)
        if relevance_threshold > 0:
            raw_items = [i for i in raw_items if ScraperService._relevance_check(
                query, i.get('title', ''), i.get('snippet', '')) >= relevance_threshold]
        normalized = ScraperService._normalize_items(source, query, raw_items)
        filtered = ScraperService._dedupe_and_persist(
            source, query, normalized, limit, user_id=user_id)
        return filtered, source_error if source_error and not filtered else None

    @staticmethod
    def _truncate_total_results(
        results: dict[str, list[dict[str, Any]]],
        max_total: int,
    ) -> dict[str, list[dict[str, Any]]]:
        if max_total <= 0:
            return {source: [] for source in results}

        ordered_sources = sorted(
            results.keys(),
            key=SourceRegistryService.source_priority,
            reverse=True,
        )

        trimmed: dict[str, list[dict[str, Any]]] = {
            source: [] for source in results}
        remaining = max_total
        for source in ordered_sources:
            if remaining <= 0:
                break
            source_items = results.get(source) or []
            take = min(len(source_items), remaining)
            trimmed[source] = source_items[:take]
            remaining -= take

        return trimmed

    @staticmethod
    def _build_reddit_queries(query: str) -> list[str]:
        """Gera variações de consulta para melhorar relevância no Reddit."""
        q = query.strip()
        queries = [f'"{q}"']
        if len(q.split()) <= 3:
            queries.append(f'"{q}" brasil')
            queries.append(f'"{q}" reclamação OR problema OR experiência')
        return queries

    @staticmethod
    def _relevance_check(query: str, title: str, snippet: str) -> float:
        q_lower = query.strip().lower()
        combined = f"{title} {snippet}".lower()
        if q_lower in combined:
            return 1.0
        words = q_lower.split()
        matched = sum(1 for w in words if w in combined)
        return matched / max(len(words), 1)

    @staticmethod
    def _reddit_relevance(query: str, title: str, snippet: str) -> float:
        """Score de relevância: 0-1. Itens abaixo de 0.2 serão descartados."""
        q_lower = query.strip().lower()
        combined = f"{title} {snippet}".lower()
        if q_lower in combined:
            return 1.0
        words = q_lower.split()
        matched = sum(1 for w in words if w in combined)
        return matched / max(len(words), 1)

    @staticmethod
    def _scrape_reddit(query: str, limit: int) -> tuple[list[dict[str, Any]], str | None]:
        request_limit = max(limit * 4, limit)
        subreddits = ["brasil", "brdev",
                      "consumidor", "explainlikeimfive", "all"]
        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for sub in subreddits:
            endpoint = f"{settings.SCRAPER_REDDIT_URL.rstrip('/')}/r/{sub}/search.json"
            try:
                response = ScraperService._request(
                    url=endpoint,
                    params={"q": query, "sort": "relevance", "t": "year", "limit": request_limit,
                            "raw_json": 1, "restrict_sr": "on" if sub != "all" else "off"},
                    expect_json=True,
                )
                children = (response.json().get("data")
                            or {}).get("children") or []
                for child in children:
                    data = child.get("data") or {}
                    post_id = str(data.get("id") or "")
                    if post_id in seen_ids:
                        continue

                    title = ScraperService._clean_text(
                        str(data.get("title") or ""))
                    snippet = ScraperService._clean_text(
                        str(data.get("selftext") or ""))
                    if not title and not snippet:
                        continue

                    permalink = str(data.get("permalink") or "").strip()
                    item_url = urljoin(
                        "https://www.reddit.com", permalink) if permalink else str(data.get("url") or "")

                    published_at = None
                    created_utc = data.get("created_utc")
                    if isinstance(created_utc, (int, float)):
                        published_at = datetime.fromtimestamp(
                            float(created_utc), tz=timezone.utc).isoformat()

                    seen_ids.add(post_id)
                    all_items.append({
                        "id": post_id,
                        "title": title,
                        "snippet": snippet,
                        "url": item_url,
                        "author": ScraperService._clean_text(str(data.get("author") or "")) or None,
                        "published_at": published_at,
                        "raw": {
                            "subreddit": data.get("subreddit"),
                            "score": data.get("score"),
                            "num_comments": data.get("num_comments"),
                        },
                    })
                    if len(all_items) >= limit:
                        break
            except Exception:
                continue
            if len(all_items) >= limit:
                break

        if not all_items:
            return [], "Reddit sem resultados relevantes"
        return all_items, None

    @staticmethod
    def _scrape_reclameaqui(query: str, limit: int) -> tuple[list[dict[str, Any]], str | None]:
        import unicodedata
        import time
        import random

        # 1. Converter query em slug, consultando mapeamento conhecido
        query_lower = query.lower().strip()

        # Tentar encontrar slug conheco no mapa
        mapped_slug = ScraperService.RECLAMEAQUI_SLUG_MAP.get(query_lower)
        if mapped_slug:
            logger.info(
                f"ReclameAqui: usando slug mapeado '{query_lower}' -> '{mapped_slug}'")
            slug = mapped_slug
        else:
            # Fallback: gerar slug automaticamente
            slug = unicodedata.normalize('NFKD', query).encode(
                'ASCII', 'ignore').decode('utf-8')
            slug = slug.lower().replace(' ', '-')
            slug = re.sub(r'[^a-z0-9-]', '', slug)
            logger.info(f"ReclameAqui: slug gerado automaticamente: '{slug}'")

        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        base_url = settings.SCRAPER_RECLAMEAQUI_URL.rstrip("/")

        # Headers mais realistas
        extra_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        search_url = settings.SCRAPER_RECLAMEAQUI_SEARCH_URL.strip()
        encoded_query = quote_plus(query)
        if "{query}" in search_url:
            resolved_search_url = search_url.format(query=encoded_query)
        elif search_url.endswith("="):
            resolved_search_url = f"{search_url}{encoded_query}"
        else:
            separator = "&" if "?" in search_url else "?"
            resolved_search_url = f"{search_url}{separator}q={encoded_query}"

        urls_to_try = [
            resolved_search_url,
            f"{base_url}/empresa/{slug}/",
        ]

        for url in urls_to_try:
            try:
                time.sleep(random.uniform(2, 5))
                response = ScraperService._request(
                    url=url, params=None, extra_headers=extra_headers)
                soup = BeautifulSoup(response.text, "html.parser")

                for link_node in soup.select("a[href]"):
                    href = str(link_node.get("href") or "").strip()
                    lower_href = href.lower()
                    if "/reclamacao/" not in lower_href:
                        continue

                    item_url = canonicalize_url(urljoin(base_url, href))
                    if not item_url or item_url in seen_urls:
                        continue

                    title = ScraperService._clean_text(
                        link_node.get_text(" ", strip=True))
                    if not title:
                        continue

                    container = link_node.find_parent(
                        ["article", "li", "div", "section", "a"])
                    snippet = ""
                    published_at = None

                    if container is not None:
                        for snippet_node in container.select("p, span"):
                            text = ScraperService._clean_text(
                                snippet_node.get_text(" ", strip=True))
                            if text and text != title and len(text) >= int(settings.SCRAPER_MIN_TEXT_LENGTH):
                                snippet = text
                                break

                    items.append({
                        "id": item_url,
                        "title": title,
                        "snippet": snippet,
                        "url": item_url,
                        "author": None,
                        "published_at": published_at,
                        "raw": {},
                    })
                    seen_urls.add(item_url)

                    if len(items) >= limit:
                        break
            except Exception as exc:
                logger.debug(f"ReclameAqui tentativa {url} falhou: {exc}")
                continue

            if items:
                break

        if not items:
            # Se slug direto falhou, tentar busca dinâmica no ReclameAqui
            web_items = ScraperService._scrape_reclameaqui_dynamic_search(
                query=query,
                limit=limit,
                base_url=base_url,
            )
            if web_items:
                return web_items, None

            # Fallback via busca web externa
            web_items = ScraperService._scrape_reclameaqui_via_web_search(
                query=query,
                limit=limit,
            )
            if web_items:
                return web_items, None

            if bool(getattr(settings, "SCRAPER_ENABLE_BROWSER_FALLBACK", False)):
                browser_items = ScraperService._scrape_reclameaqui_browser_fallback(
                    query=query,
                    limit=limit,
                    base_url=base_url,
                    seen_urls=seen_urls,
                )
                if browser_items:
                    return browser_items, None

            return [], "Reclame Aqui sem resultados"
        return items, None

    @staticmethod
    def _scrape_reclameaqui_dynamic_search(query: str, limit: int, base_url: str) -> list[dict[str, Any]]:
        """Busca dinâmica: procura pela empresa no ReclameAqui e obtém URL real.

        Útil quando o slug não existe. Acessa a página de busca, encontra a empresa
        e depois busca reclamações dela.
        """
        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        try:
            import time
            import random

            # Primeiro, procurar pela empresa e reclamações na página de busca
            search_url = f"{base_url}/busca/?q={quote_plus(query)}"
            time.sleep(random.uniform(1, 3))

            response = ScraperService._request(
                url=search_url,
                params=None,
                extra_headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )

            soup = BeautifulSoup(response.text, "html.parser")

            # Capturar reclamações diretamente da página de busca
            for link_node in soup.select("a[href*='/reclamacao/']"):
                href = str(link_node.get("href") or "").strip()
                if not href or "/reclamacao/" not in href.lower():
                    continue

                item_url = canonicalize_url(urljoin(base_url, href))
                if not item_url or item_url in seen_urls:
                    continue

                title = ScraperService._clean_text(
                    link_node.get_text(" ", strip=True))
                if not title:
                    continue

                container = link_node.find_parent(
                    ["article", "li", "div", "section"])
                snippet = ""

                if container is not None:
                    for snippet_node in container.select("p, span"):
                        text = ScraperService._clean_text(
                            snippet_node.get_text(" ", strip=True))
                        if text and text != title and len(text) >= 20:
                            snippet = text
                            break

                seen_urls.add(item_url)
                items.append({
                    "id": item_url,
                    "title": title,
                    "snippet": snippet,
                    "url": item_url,
                    "author": None,
                    "published_at": None,
                    "raw": {"collector": "reclameaqui_dynamic_search"},
                })

                if len(items) >= limit:
                    break

            if items:
                return items[:limit]

            # Se não houver reclamações diretas, procurar pela empresa
            company_links = soup.select("a[href*='/empresa/']")
            if company_links:
                first_company_link = company_links[0]
                company_url = str(first_company_link.get("href") or "").strip()

                if company_url:
                    company_url = canonicalize_url(
                        urljoin(base_url, company_url))
                    logger.info(
                        f"ReclameAqui: empresa encontrada dinamicamente: {company_url}")

                    time.sleep(random.uniform(1, 3))
                    response = ScraperService._request(
                        url=company_url,
                        params=None,
                        extra_headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                    )

                    soup = BeautifulSoup(response.text, "html.parser")

                    for link_node in soup.select("a[href*='/reclamacao/']"):
                        href = str(link_node.get("href") or "").strip()
                        if not href or "/reclamacao/" not in href.lower():
                            continue

                        item_url = canonicalize_url(urljoin(base_url, href))
                        if not item_url or item_url in seen_urls:
                            continue

                        title = ScraperService._clean_text(
                            link_node.get_text(" ", strip=True))
                        if not title:
                            continue

                        container = link_node.find_parent(
                            ["article", "li", "div", "section"])
                        snippet = ""

                        if container is not None:
                            for snippet_node in container.select("p, span"):
                                text = ScraperService._clean_text(
                                    snippet_node.get_text(" ", strip=True))
                                if text and text != title and len(text) >= 20:
                                    snippet = text
                                    break

                        seen_urls.add(item_url)
                        items.append({
                            "id": item_url,
                            "title": title,
                            "snippet": snippet,
                            "url": item_url,
                            "author": None,
                            "published_at": None,
                            "raw": {"collector": "reclameaqui_dynamic"},
                        })

                        if len(items) >= limit:
                            break
        except Exception as exc:
            logger.debug(f"ReclameAqui busca dinâmica falhou: {exc}")

        return items[:limit]

    @staticmethod
    def _scrape_reclameaqui_via_web_search(query: str, limit: int) -> list[dict[str, Any]]:
        """Busca reclamações do ReclameAqui usando mecanismo de busca externo.

        Motivo: o slug /empresa/{nome}/ nem sempre existe. Exemplo: "gmail",
        "google" ou nomes com grafia diferente podem retornar 404 no ReclameAqui.
        Esse fallback procura páginas reais de reclamação indexadas na web.
        """
        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        search_query = (
            f'site:reclameaqui.com.br/reclamacao "{query}" '
            f'(reclamação OR problema OR atendimento OR suporte OR entrega OR cobrança)'
        )

        try:
            from ddgs import DDGS

            with DDGS() as ddgs:
                results = list(
                    ddgs.text(search_query, max_results=max(limit * 3, limit)))

            for result in results:
                url = canonicalize_url(str(result.get("href") or ""))
                if not url or url in seen_urls:
                    continue

                if "reclameaqui.com.br" not in url.lower() or "/reclamacao/" not in url.lower():
                    continue

                title = ScraperService._clean_text(
                    str(result.get("title") or ""))
                snippet = ScraperService._clean_text(
                    str(result.get("body") or ""))

                if not title and not snippet:
                    continue

                seen_urls.add(url)
                items.append(
                    {
                        "id": url,
                        "title": title or f"Reclamação sobre {query}",
                        "snippet": snippet,
                        "url": url,
                        "author": "ReclameAqui",
                        "published_at": None,
                        "raw": {"collector": "web_search_reclameaqui"},
                    }
                )

                if len(items) >= limit:
                    break

        except Exception as exc:
            logger.warning(
                "Fallback ReclameAqui via busca web falhou (DDGS): %s", exc)

        if len(items) < limit:
            items.extend(
                ScraperService._scrape_reclameaqui_via_duckduckgo_html(
                    query=query,
                    limit=limit - len(items),
                    seen_urls=seen_urls,
                )
            )

        return items[:limit]

    @staticmethod
    def _scrape_reclameaqui_via_duckduckgo_html(
        query: str,
        limit: int,
        seen_urls: set[str],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        try:
            search_query = (
                f'site:reclameaqui.com.br/reclamacao "{query}" '
                f'(reclamação OR problema OR atendimento OR suporte OR entrega OR cobrança)'
            )
            headers = {
                "User-Agent": settings.SCRAPER_USER_AGENT.strip() or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            response = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": search_query},
                headers=headers,
                timeout=max(5, int(settings.SCRAPER_TIMEOUT_SECONDS)),
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"DuckDuckGo HTML search HTTP {response.status_code}"
                )

            soup = BeautifulSoup(response.text, "html.parser")
            for link_node in soup.select("a[href*='/reclamacao/']"):
                href = str(link_node.get("href") or "").strip()
                if not href:
                    continue

                item_url = canonicalize_url(
                    urljoin("https://www.reclameaqui.com.br", href))
                if not item_url or item_url in seen_urls:
                    continue

                title = ScraperService._clean_text(
                    link_node.get_text(" ", strip=True))
                if not title:
                    continue

                snippet = ""
                container = link_node.find_parent(
                    ["article", "li", "div", "section"])
                if container is not None:
                    for snippet_node in container.select("p, span"):
                        text = ScraperService._clean_text(
                            snippet_node.get_text(" ", strip=True))
                        if text and text != title and len(text) >= 20:
                            snippet = text
                            break

                seen_urls.add(item_url)
                items.append(
                    {
                        "id": item_url,
                        "title": title,
                        "snippet": snippet,
                        "url": item_url,
                        "author": "ReclameAqui",
                        "published_at": None,
                        "raw": {"collector": "duckduckgo_html_reclameaqui"},
                    }
                )
                if len(items) >= limit:
                    break

        except Exception as exc:
            logger.warning(
                "Fallback ReclameAqui via DuckDuckGo HTML falhou: %s", exc)

        return items[:limit]

    @staticmethod
    def _scrape_reclameaqui_browser_fallback(
        query: str,
        limit: int,
        base_url: str,
        seen_urls: set[str],
    ) -> list[dict[str, Any]]:
        """Fallback opcional com browser headless.

        Em produção/Render, este fallback fica desligado por padrão.
        Playwright é pesado e pode falhar por event loop/subprocess em Windows
        ou por dependências de sistema em containers. O scraper não deve depender
        dele para responder ao usuário.
        """
        if not bool(getattr(settings, "SCRAPER_ENABLE_BROWSER_FALLBACK", False)):
            return []

        # Reforçar política asyncio ANTES de tentar usar Playwright no Windows.
        ScraperService._ensure_windows_asyncio_policy()

        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            # Dependencia opcional: sem playwright, segue sem fallback de browser.
            return []

        import unicodedata

        slug = unicodedata.normalize("NFKD", query).encode(
            "ASCII", "ignore").decode("utf-8")
        slug = slug.lower().replace(" ", "-")
        slug = re.sub(r"[^a-z0-9-]", "", slug)

        items: list[dict[str, Any]] = []
        targets = [
            f"{base_url}/empresa/{slug}/",
            f"{settings.SCRAPER_RECLAMEAQUI_SEARCH_URL.strip()}{quote_plus(query)}",
        ]

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=settings.SCRAPER_USER_AGENT.strip())
                page = context.new_page()
                page.set_default_timeout(
                    max(5000, int(settings.SCRAPER_TIMEOUT_SECONDS) * 1000))

                for target in targets:
                    try:
                        page.goto(target, wait_until="domcontentloaded")
                        page.wait_for_timeout(1200)
                        links = page.eval_on_selector_all(
                            "a[href*='/reclamacao/']",
                            "els => els.map(e => ({ href: e.getAttribute('href') || '', text: (e.textContent || '').trim() }))",
                        )
                        for link in links:
                            href = str((link or {}).get("href") or "").strip()
                            title = ScraperService._clean_text(
                                str((link or {}).get("text") or ""))
                            if not href or not title:
                                continue

                            item_url = canonicalize_url(
                                urljoin(base_url, href))
                            if not item_url or item_url in seen_urls:
                                continue

                            seen_urls.add(item_url)
                            items.append(
                                {
                                    "id": item_url,
                                    "title": title,
                                    "snippet": "",
                                    "url": item_url,
                                    "author": None,
                                    "published_at": None,
                                    "raw": {"collector": "playwright"},
                                }
                            )
                            if len(items) >= limit:
                                break
                    except Exception as exc:
                        logger.info(
                            "ReclameAqui fallback browser falhou em %s: %s", target, exc)
                    if len(items) >= limit:
                        break

                context.close()
                browser.close()
        except NotImplementedError as exc:
            logger.warning(
                "Playwright não pode iniciar no Windows atual: verifique asyncio.WindowsSelectorEventLoopPolicy: %s", exc)
            return []
        except Exception as exc:
            logger.info(
                "Playwright indisponivel para fallback ReclameAqui: %s", exc)
            return []

        return items[:limit]

    @staticmethod
    def _scrape_mastodon(query: str, limit: int) -> tuple[list[dict[str, Any]], str | None]:
        base_url = settings.SCRAPER_MASTODON_BASE_URL.rstrip("/")
        path = settings.SCRAPER_MASTODON_SEARCH_PATH.strip() or "/api/v2/search"
        if not path.startswith("/"):
            path = f"/{path}"
        endpoint = f"{base_url}{path}"
        access_token = (settings.SCRAPER_MASTODON_ACCESS_TOKEN or "").strip()

        request_limit = max(limit * 4, limit)

        # Monta params: sem 'resolve' e 'type' se nao houver token (modo publico)
        params: dict[str, Any] = {"q": query, "limit": request_limit}
        headers_extra: dict[str, str] = {}
        if access_token:
            params["type"] = "statuses"
            params["resolve"] = "true"
            headers_extra["Authorization"] = f"Bearer {access_token}"
            logger.info("Mastodon: modo autenticado")
        else:
            logger.info(
                "Mastodon: modo publico restrito (sem SCRAPER_MASTODON_ACCESS_TOKEN)")

        try:
            response = ScraperService._request(
                url=endpoint,
                params=params,
                expect_json=True,
                extra_headers=headers_extra,
            )
            resp_json = response.json()
            statuses = resp_json.get("statuses") or []

            # Fallback: se modo publico retornou hashtags/accounts em vez de statuses
            if not statuses and not access_token:
                logger.info(
                    "Mastodon modo publico: sem statuses retornados, degradacao elegante")
                return [], "Mastodon: modo publico nao retornou statuses. Configure SCRAPER_MASTODON_ACCESS_TOKEN para melhores resultados."

            items: list[dict[str, Any]] = []
            for status in statuses:
                content_html = str(status.get("content") or "")
                content = ScraperService._clean_text(BeautifulSoup(
                    content_html, "html.parser").get_text(" ", strip=True))
                if not content or len(content) < 10:
                    continue

                title = content if len(
                    content) <= 120 else f"{content[:117]}..."
                account = status.get("account") or {}
                author = ScraperService._clean_text(
                    str(account.get("display_name")
                        or account.get("username") or "")
                )
                item_url = str(status.get("url")
                               or status.get("uri") or "").strip()

                items.append({
                    "id": str(status.get("id") or ""),
                    "title": title,
                    "snippet": content,
                    "url": item_url,
                    "author": author or None,
                    "published_at": status.get("created_at"),
                    "raw": {
                        "replies_count": status.get("replies_count"),
                        "reblogs_count": status.get("reblogs_count"),
                        "favourites_count": status.get("favourites_count"),
                    },
                })

            if not items:
                return [], "Mastodon sem resultados"
            return items, None
        except Exception as exc:
            error_msg = str(exc)
            if "401" in error_msg:
                logger.warning(
                    "Mastodon 401: token invalido ou endpoint requer autenticacao")
                return [], "Mastodon: autenticacao falhou (401). Verifique SCRAPER_MASTODON_ACCESS_TOKEN."
            logger.warning("Mastodon falha: %s", error_msg)
            return [], f"Falha no Mastodon: {exc}"

    @staticmethod
    def _scrape_web(query: str, limit: int) -> tuple[list[dict[str, Any]], str | None]:
        """Busca web aberta com DDGS e enriquecimento leve dos primeiros links."""
        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        try:
            search_query = (
                f'"{query}" '
                f'(avaliação OR reclamação OR opinião OR review OR problema OR defeito '
                f'OR atraso OR suporte OR atendimento OR "não recomendo")'
            )

            try:
                from ddgs import DDGS

                with DDGS() as ddgs:
                    results = list(
                        ddgs.text(search_query, max_results=max(limit * 3, limit)))

                for result in results:
                    url = canonicalize_url(str(result.get("href") or ""))
                    if not url or url in seen_urls:
                        continue

                    title = ScraperService._clean_text(
                        str(result.get("title") or ""))
                    snippet = ScraperService._clean_text(
                        str(result.get("body") or ""))
                    if not title and not snippet:
                        continue

                    seen_urls.add(url)
                    items.append(
                        {
                            "id": url,
                            "title": title,
                            "snippet": snippet,
                            "url": url,
                            "author": urlparse(url).netloc,
                            "published_at": None,
                            "raw": {"collector": "ddgs", "search_query": search_query},
                        }
                    )
                    if len(items) >= limit * 2:
                        break

            except Exception as exc:
                logger.warning("DDGS falhou, usando fallback HTML. %s", exc)

                response = ScraperService._request(
                    url=settings.SCRAPER_WEB_SEARCH_URL.rstrip("/"),
                    params={"q": search_query},
                )
                soup = BeautifulSoup(response.text, "html.parser")

                for container in soup.select("div.result, article, li"):
                    link_node = container.select_one(
                        "a.result__a, h2 a, a[data-testid='result-title-a'], a[href]"
                    )
                    if not link_node:
                        continue

                    target = ScraperService._extract_web_target(
                        str(link_node.get("href") or "").strip()
                    )
                    url = canonicalize_url(target)
                    if not url or url in seen_urls:
                        continue

                    title = ScraperService._clean_text(
                        link_node.get_text(" ", strip=True))
                    if not title:
                        continue

                    seen_urls.add(url)
                    items.append(
                        {
                            "id": url,
                            "title": title,
                            "snippet": "",
                            "url": url,
                            "author": urlparse(url).netloc,
                            "published_at": None,
                            "raw": {"collector": "duckduckgo_html_fallback"},
                        }
                    )
                    if len(items) >= limit * 2:
                        break

            # Enriquecimento leve: acessa poucos links para melhorar snippet.
            # Isso aumenta qualidade dos insights, mas sem transformar o scraper em crawler pesado.
            try:
                import trafilatura
            except ImportError:
                trafilatura = None

            enrichment_limit = min(5, len(items))
            for item in items[:enrichment_limit]:
                try:
                    headers = {
                        "User-Agent": settings.SCRAPER_USER_AGENT.strip(),
                        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                    }
                    response = requests.get(
                        item["url"], headers=headers, timeout=10)
                    if response.status_code >= 400:
                        continue

                    if trafilatura:
                        text = trafilatura.extract(response.text)
                        if text:
                            item["snippet"] = ScraperService._clean_text(text)[
                                :1000]
                    else:
                        soup = BeautifulSoup(response.text, "html.parser")
                        page_text = soup.get_text(" ", strip=True)
                        if page_text:
                            item["snippet"] = ScraperService._clean_text(page_text)[
                                :1000]
                except Exception:
                    continue

            return items[:limit], None

        except Exception as exc:
            return [], f"Falha na busca Web: {exc}"

    @staticmethod
    def _scrape_trustpilot(query: str, limit: int) -> tuple[list[dict[str, Any]], str | None]:
        url = f"https://www.trustpilot.com/search?query={quote_plus(query)}"
        try:
            response = ScraperService._request(url=url, params=None)
            soup = BeautifulSoup(response.text, "html.parser")
            items = []
            for link in soup.select("a[name='business-unit-card']"):
                href = link.get("href")
                if not href:
                    continue
                item_url = urljoin("https://www.trustpilot.com", href)
                title = ScraperService._clean_text(
                    link.get_text(" ", strip=True))
                items.append({
                    "id": item_url,
                    "title": title,
                    "snippet": f"Trustpilot review para {query}",
                    "url": item_url,
                    "author": "Trustpilot",
                    "published_at": None,
                    "raw": {}
                })
                if len(items) >= limit:
                    break
            return items, None
        except Exception as e:
            return [], f"Falha Trustpilot: {e}"

    @staticmethod
    def _scrape_consumidor(query: str, limit: int) -> tuple[list[dict[str, Any]], str | None]:
        url = f"https://www.consumidor.gov.br/pages/empresa/buscarPorNome.json"
        try:
            response = requests.post(url, data={"query": query}, timeout=10)
            items = []
            # Consumidor.gov returns JSON if search matched
            try:
                data = response.json()
                for d in data:
                    item_url = f"https://www.consumidor.gov.br/pages/empresa/{d.get('id')}"
                    items.append({
                        "id": item_url,
                        "title": f"Consumidor.gov.br - {d.get('nomeFantasia', query)}",
                        "snippet": f"Avaliações oficiais no portal do consumidor.",
                        "url": item_url,
                        "author": "Consumidor.gov.br",
                        "published_at": None,
                        "raw": d
                    })
                    if len(items) >= limit:
                        break
            except:
                pass
            return items, None
        except Exception as e:
            return [], f"Falha Consumidor.gov.br: {e}"

    @staticmethod
    def _extract_web_target(href: str) -> str:
        if not href:
            return ""

        if href.startswith("/"):
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            if "uddg" in params and params["uddg"]:
                return unquote(params["uddg"][0])
            return urljoin("https://duckduckgo.com", href)

        return href

    @staticmethod
    def _normalize_items(source: str, query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for item in items:
            title = ScraperService._clean_text(str(item.get("title") or ""))
            snippet = ScraperService._clean_text(
                str(item.get("snippet") or ""))
            text = "\n".join(part for part in [title, snippet] if part).strip()
            if not text:
                continue

            if ScraperService._looks_like_low_signal(text):
                continue

            if len(text) < max(8, int(settings.SCRAPER_MIN_TEXT_LENGTH // 2)):
                continue

            original_url = str(item.get("url") or "").strip()
            canonical_url = canonicalize_url(
                str(item.get("canonical_url") or original_url))
            if not original_url and not canonical_url:
                continue

            author = ScraperService._clean_text(str(item.get("author") or ""))
            content_hash = compute_content_hash(
                source=source,
                author=author or "desconhecido",
                text=text,
                url=canonical_url or original_url,
            )
            dedupe_key = canonical_url or content_hash
            if dedupe_key in seen_keys:
                continue

            quality_score = ScraperService._quality_score(
                text, title=title, snippet=snippet)
            if quality_score < 0.25:
                continue

            seen_keys.add(dedupe_key)
            normalized.append(
                {
                    "id": str(item.get("id") or item.get("source_item_id") or content_hash),
                    "source": source,
                    "entity": query,
                    "title": title or text[:120],
                    "snippet": snippet,
                    "text": text,
                    "url": canonical_url or original_url,
                    "canonical_url": canonical_url or None,
                    "author": author or None,
                    "published_at": ScraperService._normalize_datetime(item.get("published_at")),
                    "collected_at": utcnow().isoformat(),
                    "content_hash": content_hash,
                    "source_priority": SourceRegistryService.source_priority(source),
                    "quality_score": round(quality_score, 3),
                    "raw": item.get("raw") if isinstance(item.get("raw"), dict) else item,
                }
            )

        normalized.sort(key=lambda entry: float(
            entry.get("quality_score") or 0), reverse=True)
        return normalized

    @staticmethod
    def _dedupe_and_persist(
        source: str,
        query: str,
        items: list[dict[str, Any]],
        limit: int,
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        """Remove duplicidade sem esconder dados de buscas repetidas.

        Regra anterior: se o item já existia no scrape_cache, ele não voltava
        no resultado da busca. Isso fazia a tela de Insights receber poucos ou
        nenhum item em pesquisas repetidas.

        Regra atual:
        - item novo: retorna e persiste;
        - item repetido: retorna como cached=True, mas não duplica no banco.
        """
        if not items:
            ScraperService._update_source_checkpoint(
                source=source,
                query=query,
                item_count=0,
                user_id=user_id,
            )
            return []

        db = get_db()
        if db is None:
            return items[:limit]

        query_key = query.strip().lower()
        existing_hashes: set[str] = set()

        # Hash operacional para cache por usuário. Mantém content_hash original
        # para normalização e usa sha256_hash para dedupe incremental.
        for item in items:
            source_val = item.get("source") or source
            url_val = item.get("canonical_url") or item.get("url") or ""
            content_val = (item.get("text") or item.get(
                "snippet") or item.get("title") or "")[:300]
            hash_base = f"{user_id}|{source_val}|{url_val}|{content_val}".encode(
                "utf-8")
            item["sha256_hash"] = hashlib.sha256(hash_base).hexdigest()

        candidate_hashes = [
            str(item.get("sha256_hash") or "")
            for item in items
            if item.get("sha256_hash")
        ]

        if candidate_hashes:
            try:
                existing = db.scrape_cache.find(
                    {
                        "user_id": user_id,
                        "hash": {"$in": candidate_hashes},
                    },
                    {"hash": 1},
                )
                existing_hashes = {str(doc.get("hash"))
                                   for doc in existing if doc.get("hash")}
            except Exception as exc:
                logger.warning("Consulta ao scrape_cache falhou: %s", exc)
                existing_hashes = set()

        fresh: list[dict[str, Any]] = []
        returned: list[dict[str, Any]] = []

        for item in items:
            item_hash = item.get("sha256_hash")
            item["user_id"] = user_id

            if item_hash and item_hash in existing_hashes:
                item["cached"] = True
                returned.append(item)

                if len(returned) >= limit:
                    break

                continue

            if item_hash:
                existing_hashes.add(item_hash)

            item["cached"] = False
            fresh.append(item)
            returned.append(item)

            if len(returned) >= limit:
                break

        if fresh:
            now = utcnow()
            docs = [
                {
                    "user_id": user_id,
                    "source": source,
                    "company": query,
                    "query": query,
                    "query_key": query_key,
                    "entity": item.get("entity") or query,
                    "title": item.get("title"),
                    "snippet": item.get("snippet"),
                    "text": item.get("text"),
                    "author": item.get("author"),
                    "url": item.get("url"),
                    "canonical_url": item.get("canonical_url"),
                    "published_at": item.get("published_at"),
                    "collected_at": item.get("collected_at"),
                    "content_hash": item.get("content_hash"),
                    "sha256_hash": item.get("sha256_hash"),
                    "cached": bool(item.get("cached", False)),
                    "quality_score": item.get("quality_score"),
                    "source_priority": item.get("source_priority"),
                    "raw": item.get("raw") or {},
                    "scraped_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
                for item in fresh
            ]

            try:
                db.scraped_items.insert_many(docs, ordered=False)
            except Exception as exc:
                logger.warning(
                    "Persistência em scraped_items falhou para %s: %s", source, exc)

            cache_docs = [
                {
                    "hash": item.get("sha256_hash"),
                    "user_id": user_id,
                    "source": source,
                    "query_key": query_key,
                    "created_at": now,
                }
                for item in fresh
                if item.get("sha256_hash")
            ]

            if cache_docs:
                try:
                    db.scrape_cache.insert_many(cache_docs, ordered=False)
                except Exception as exc:
                    # Não deve derrubar a busca. Cache é otimização, não regra de negócio crítica.
                    logger.warning(
                        "Persistência em scrape_cache falhou para %s: %s", source, exc)

        ScraperService._update_source_checkpoint(
            source=source,
            query=query,
            item_count=len(returned),
            user_id=user_id,
        )

        return returned[:limit]

    @staticmethod
    def _update_source_checkpoint(source: str, query: str, item_count: int, user_id: str = "") -> None:
        db = get_db()
        if db is None:
            return

        now = utcnow()
        source_config = SourceRegistryService.get_source_config(source)
        db.source_checkpoints.update_one(
            {"source": source, "query_key": query.strip().lower(),
             "user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "source": source,
                    "query": query,
                    "query_key": query.strip().lower(),
                    "item_count": int(item_count),
                    "fetchMode": source_config.fetch_mode if source_config else None,
                    "updatedAt": now,
                    "lastCollectedAt": now,
                },
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )

    @staticmethod
    def _request(
        url: str,
        params: dict[str, Any] | None,
        expect_json: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> requests.Response:
        headers = {
            "User-Agent": settings.SCRAPER_USER_AGENT.strip(),
            "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if extra_headers:
            headers.update(extra_headers)

        if not headers["User-Agent"]:
            headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )

        timeout = max(5, int(settings.SCRAPER_TIMEOUT_SECONDS))
        attempts = max(1, int(settings.SCRAPER_RETRY_ATTEMPTS))
        base_delay = max(0.1, float(settings.SCRAPER_DELAY_SECONDS))
        retry_backoff = max(0.2, float(settings.SCRAPER_RETRY_BACKOFF_SECONDS))

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(
                    url, params=params, headers=headers, timeout=timeout)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise RuntimeError(
                        f"HTTP {response.status_code} em {response.url}")
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"HTTP {response.status_code} em {response.url}")

                if expect_json:
                    response.json()
                return response
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                time.sleep(base_delay + ((attempt - 1) * retry_backoff))

        raise RuntimeError(
            str(last_error) if last_error else "Falha de rede desconhecida")

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join((value or "").split())

    @staticmethod
    def _normalize_datetime(value: Any) -> str | None:
        if not value:
            return None

        if isinstance(value, datetime):
            dt = value
        else:
            candidate = str(value).strip()
            if candidate.endswith("Z"):
                candidate = f"{candidate[:-1]}+00:00"
            try:
                dt = datetime.fromisoformat(candidate)
            except ValueError:
                return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _looks_like_low_signal(text: str) -> bool:
        lower = (text or "").strip().lower()
        if not lower:
            return True

        if any(term in lower for term in ScraperService.LOW_SIGNAL_TERMS):
            return True

        tokens = [token for token in re.findall(
            r"[a-z0-9à-ÿ]+", lower, flags=re.IGNORECASE) if len(token) > 2]
        if len(tokens) >= 8:
            diversity = len(set(tokens)) / max(1, len(tokens))
            if diversity < 0.35:
                return True

        return False

    @staticmethod
    def _quality_score(text: str, title: str, snippet: str) -> float:
        length_score = min(len(text) / 260.0, 1.0)
        title_bonus = 0.2 if title else 0.0
        snippet_bonus = 0.2 if snippet else 0.0
        signal_penalty = 0.3 if ScraperService._looks_like_low_signal(
            text) else 0.0
        return max(0.0, min(1.0, (length_score * 0.6) + title_bonus + snippet_bonus - signal_penalty))

    @staticmethod
    def build_debug_search_url(query: str, source: str) -> str:
        source = SourceRegistryService.normalize_source_name(source)
        encoded = quote_plus(query.strip())

        if source == "reclameaqui":
            search_url = settings.SCRAPER_RECLAMEAQUI_SEARCH_URL
            if "{query}" in search_url:
                return search_url.format(query=encoded)
            if search_url.endswith("="):
                return f"{search_url}{encoded}"
            separator = "&" if "?" in search_url else "?"
            return f"{search_url}{separator}q={encoded}"
        if source == "reddit":
            return f"{settings.SCRAPER_REDDIT_URL.rstrip('/')}/search.json?q={encoded}&sort=new&t=month"
        if source == "mastodon":
            path = settings.SCRAPER_MASTODON_SEARCH_PATH
            if not path.startswith("/"):
                path = f"/{path}"
            return f"{settings.SCRAPER_MASTODON_BASE_URL.rstrip('/')}{path}?q={encoded}&type=statuses"
        if source == "web":
            return f"{settings.SCRAPER_WEB_SEARCH_URL.rstrip('/')}?q={encoded}"

        return ""
