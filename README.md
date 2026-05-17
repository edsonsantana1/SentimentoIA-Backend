# backend-api-python - SentimentoIA (FastAPI + Worker + Prompts)

## Objetivo arquitetural

Este backend e a camada unica de integracao da plataforma para:

- IA (Ollama Cloud via backend)
- scraping (Reclame Aqui, Reddit e Web)
- insights e exportacoes (Markdown/PDF)
- persistencia (MongoDB Atlas)
- chat user-scoped por usuario autenticado

Nao existe dependencia de LLM direta no frontend.

## Estrutura

- API: `apps/api/app`
- Testes API: `apps/api/tests`
- Worker: `apps/worker/app/worker.py`
- Prompts de dominio: `packages/prompts`
- Env examples:
   - `apps/api/.env.example`
   - `apps/worker/.env.example`
   - `.env.example`

## Atualizacao do .env real (apps/api/.env)

O arquivo `apps/api/.env` foi atualizado no mesmo padrao aplicado no frontend:

- valores antigos relevantes foram comentados
- valores ativos ficaram explicitos para deploy
- variaveis existentes foram preservadas (sem remover blocos)

Principais ajustes realizados:

- `ENV` e `DEBUG` com perfil de producao ativo
- `APP_URL` e `FRONTEND_URL` com formato publico
- `CORS_ORIGINS_CSV` adicionado para controle de origens
- `OLLAMA_BASE_URL` atualizado para `https://ollama.com/api` com valor anterior comentado
- `SCRAPER_DEFAULT_SOURCES` alinhado para `reclameaqui,reddit,web`
- variaveis operacionais adicionadas: `WORKER_POLL_INTERVAL_SECONDS`, `WORKER_BATCH_SIZE`, `LOG_LEVEL`

Observacao importante:

- Segredos atuais (`SECRET_KEY`, `MONGODB_URI`, `OLLAMA_API_KEY`, SMTP) foram mantidos.
- Nao commitar credenciais reais em repositorio publico.

## Requisitos

- Python 3.11+
- MongoDB Atlas
- OLLAMA_API_KEY valido

## Execucao local

### 1) API

```powershell
cd repos-separados-20260506/backend-api-python/apps/api
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir .
```

### 2) Worker (terminal separado)

```powershell
cd repos-separados-20260506/backend-api-python
.\apps\api\.venv\Scripts\Activate.ps1
python -m apps.worker.app.worker
```

O worker utiliza `apps/worker/.env` e `apps/api/.env`.

## Variaveis obrigatorias de ambiente

### Base/API

- `ENV`
- `DEBUG`
- `APP_URL`
- `FRONTEND_URL`
- `CORS_ORIGINS_CSV`
- `SECRET_KEY`
- `ALGORITHM`
- `ACCESS_TOKEN_EXPIRE_MINUTES`

### MongoDB

- `MONGODB_URI`
- `DATABASE_NAME`

### IA (Ollama)

- `LLM_PROVIDER=ollama`
- `OLLAMA_BASE_URL` (default recomendado: `https://ollama.com/api`)
- `OLLAMA_API_KEY`
- `OLLAMA_MODEL`
- `OLLAMA_TIMEOUT_SECONDS`

### Scraping

- `SCRAPER_DEFAULT_SOURCES` (recomendado: `reclameaqui,reddit,web`)
- `SCRAPER_DEFAULT_LIMIT`
- `SCRAPER_TIMEOUT_SECONDS`
- `SCRAPER_RETRY_ATTEMPTS`
- `SCRAPER_RETRY_BACKOFF_SECONDS`
- `SCRAPER_RECLAMEAQUI_URL`
- `SCRAPER_RECLAMEAQUI_SEARCH_URL`
- `SCRAPER_REDDIT_URL`
- `SCRAPER_WEB_SEARCH_URL`

### Worker

- `WORKER_POLL_INTERVAL_SECONDS`
- `WORKER_BATCH_SIZE`
- `LLM_TRIGGER_MIN_COMMENTS`
- `LLM_MAX_SAMPLE_MENTIONS`
- `LOG_LEVEL`

## Contrato principal da API

### Chat

- `GET /api/chat/threads`
- `POST /api/chat/threads`
- `GET /api/chat/threads/{thread_id}/messages`
- `POST /api/chat/threads/{thread_id}/messages`
- `DELETE /api/chat/threads/{thread_id}`
- `DELETE /api/chat/threads/{thread_id}/messages/{message_id}`
- `DELETE /api/chat/threads`

### Insights

- `GET /api/insights?priority=&resolution=&batch_id=&include_archived=&limit=`
- `POST /api/insights/generate`
- `POST /api/insights/{insight_id}/regenerate`
- `POST /api/insights/{insight_id}/archive`
- `DELETE /api/insights/{insight_id}`
- `GET /api/insights/export/markdown?priority=&resolution=&limit=`
- `GET /api/insights/export/pdf?priority=&resolution=&limit=`

### Busca/Scraping

- `POST /api/scrape`
- `POST /api/search`
- `GET /api/dashboard`
- `GET /api/mentions`

### Status

- `GET /health`
- `GET /api/status/integrations`

Todos os dados de chat, dashboard, insights e exportacao sao filtrados por `user_id` autenticado.

## Guia de testes

### Unitario/integracao rapida

```powershell
cd repos-separados-20260506/backend-api-python/apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_main_flow.py tests/test_scrape.py -q
```

### Suite backend completa

```powershell
cd repos-separados-20260506/backend-api-python/apps/api
.\.venv\Scripts\python.exe -m pytest tests -q
```

### Validacao manual minima

1. Registrar/login de usuario (JWT + MFA, se habilitado)
2. Executar `/api/scrape` e `/api/search`
3. Validar dashboard/mencoes por usuario
4. Gerar insight e validar filtros `priority` e `resolution`
5. Exportar insights markdown/pdf
6. Criar thread de chat, enviar mensagem e deletar mensagem/thread

## Deploy (HostingGuru ou equivalente)

### Start commands

- API:
   - `uvicorn app.main:app --host 0.0.0.0 --port $PORT --app-dir apps/api`
- Worker:
   - `python -m apps.worker.app.worker`

### Checklist de producao

- `ENV=production`
- `DEBUG=false`
- `APP_URL` publico
- `FRONTEND_URL` publico
- `CORS_ORIGINS_CSV` sem localhost
- `MONGODB_URI` (Atlas)
- `OLLAMA_BASE_URL=https://ollama.com/api`
- `OLLAMA_API_KEY` via secret manager

## Troubleshooting rapido

- Erro de CORS: revisar `FRONTEND_URL` e `CORS_ORIGINS_CSV`
- Timeout de IA: ajustar `OLLAMA_TIMEOUT_SECONDS`
- Sem resultados de scraping: validar `SCRAPER_DEFAULT_SOURCES` e limites
- Falha em exportacao: verificar autenticacao e dados por usuario
