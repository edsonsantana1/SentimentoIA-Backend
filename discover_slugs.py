#!/usr/bin/env python3
"""
Script para descobrir os slugs corretos das empresas no ReclameAqui.

Execução:
    python discover_slugs.py "Brahma" "Vivo" "Nike" "Mercado Livre"
"""

import sys
import time
from urllib.parse import quote_plus, urljoin
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.reclameaqui.com.br"
SEARCH_URL = f"{BASE_URL}/busca/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def discover_slug(company_name: str) -> dict:
    """Descobre o slug correto de uma empresa no ReclameAqui."""
    
    print(f"\n{'='*60}")
    print(f"Buscando slug para: {company_name}")
    print(f"{'='*60}")
    
    try:
        # Fazer busca
        search_params = {"q": company_name}
        response = requests.get(
            SEARCH_URL,
            params=search_params,
            headers=HEADERS,
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"❌ Erro HTTP {response.status_code}")
            return {
                "company": company_name,
                "slug": None,
                "url": None,
                "status": "erro_http"
            }
        
        # Parsear HTML
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Procurar links de empresa
        company_links = soup.select("a[href*='/empresa/']")
        
        if not company_links:
            print(f"⚠️  Nenhuma empresa encontrada na busca")
            return {
                "company": company_name,
                "slug": None,
                "url": None,
                "status": "nenhum_resultado"
            }
        
        # Pegar primeira resultado
        first_link = company_links[0]
        href = first_link.get("href", "")
        full_url = urljoin(BASE_URL, href)
        
        # Extrair slug
        slug = href.split("/empresa/")[1].rstrip("/") if "/empresa/" in href else None
        
        print(f"✓ Slug encontrado: {slug}")
        print(f"✓ URL completa: {full_url}")
        print(f"✓ Título: {first_link.get_text().strip()}")
        
        # Verificar se página existe
        time.sleep(1)
        verify_response = requests.head(full_url, headers=HEADERS, timeout=10, allow_redirects=True)
        
        if verify_response.status_code == 200:
            print(f"✓ Página verificada: OK ({verify_response.status_code})")
        else:
            print(f"⚠️  Status da página: {verify_response.status_code}")
        
        return {
            "company": company_name,
            "slug": slug,
            "url": full_url,
            "status": "sucesso",
            "http_status": verify_response.status_code
        }
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Erro de requisição: {e}")
        return {
            "company": company_name,
            "slug": None,
            "url": None,
            "status": "erro_requisicao",
            "error": str(e)
        }
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        return {
            "company": company_name,
            "slug": None,
            "url": None,
            "status": "erro_inesperado",
            "error": str(e)
        }


def main():
    companies = sys.argv[1:] if len(sys.argv) > 1 else [
        "Brahma",
        "Vivo",
        "Nike",
        "Mercado Livre"
    ]
    
    print("\n" + "="*60)
    print("DESCOBRIDOR DE SLUGS - ReclameAqui")
    print("="*60)
    print(f"Testando {len(companies)} empresas...\n")
    
    results = []
    for company in companies:
        result = discover_slug(company)
        results.append(result)
        time.sleep(2)  # Respeitar taxa de requisições
    
    # Gerar output
    print("\n" + "="*60)
    print("RESULTADO FINAL")
    print("="*60)
    print("\n📝 Copie esses slugs para o RECLAMEAQUI_SLUG_MAP:\n")
    print("```python")
    print("RECLAMEAQUI_SLUG_MAP = {")
    
    for result in results:
        if result["slug"]:
            company_key = result["company"].lower()
            print(f'    "{company_key}": "{result["slug"]}", ')
    
    print("}")
    print("```\n")
    
    # Resumo
    print("\n📊 Resumo:")
    for result in results:
        status = "✓" if result["status"] == "sucesso" else "❌"
        print(f"{status} {result['company']:20s} → {result.get('slug', 'NÃO ENCONTRADO')}")


if __name__ == "__main__":
    main()
