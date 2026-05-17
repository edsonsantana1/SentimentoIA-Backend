# SentimentoIA - Base de Conhecimento do Sistema

## Modulos principais
- Busca/Coleta: consulta fontes configuradas para a marca.
- Ingestao: recebe comentarios em lote (`comment_batches`).
- Processamento: classifica sentimento, criticidade, aspectos e urgencia em `mentions`.
- Dashboard: mostra metricas consolidadas por lote processado.
- Insights: gera analise executiva em `insights` a partir de batches elegiveis.
- Relatorios: exporta CSV/PDF.
- Configuracoes: define tema, idioma e limiar da LLM.
- Chat: assistente de dominio fechado com contexto mediado pelo backend.

## KPIs usuais
- `total_mentions` / `total_comments`: volume total analisado.
- `sentiment_distribution`: distribuicao de sentimentos.
- `critical_mentions`: volume de mencoes criticas.
- `average_urgency`: media de urgencia calculada.
- `reputation_score`: score sintetico de reputacao.
- `top_aspects`: aspectos mais citados.

## Regras de negocio relevantes
- Insights automaticos dependem de limiar minimo (`llm_trigger_min_comments`).
- Falha da LLM nao deve interromper pipeline de dados.
- Chat nao pode responder fora do dominio.
- Chat nao acessa banco diretamente; recebe contexto permitido do backend.

## Navegacao esperada
- `/search`: iniciar busca/coleta.
- `/dashboard`: acompanhar metricas e mencoes processadas.
- `/analysis`: gerenciar insights persistidos.
- `/reports`: baixar relatrios CSV/PDF.
- `/settings`: configurar idioma, tema e limiar.

## Responsividade alvo
- 360px: mobile compacto.
- 768px: tablet.
- 1024px: notebook.
- 1440px: desktop amplo.
