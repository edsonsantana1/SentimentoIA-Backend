# Prompts

Prompts de sistema e base de conhecimento do assistente restrito ao dominio.

Arquivos principais:
- `domain-closed-system-prompt.md`: regras obrigatorias do chat fechado.
- `domain-knowledge-base.md`: conhecimento funcional do SentimentoIA.

Uso esperado:
- O backend monta contexto autorizado do usuario.
- A LLM recebe apenas o prompt de dominio + base + contexto permitido.
- Perguntas fora de escopo devem ser recusadas de forma objetiva.
