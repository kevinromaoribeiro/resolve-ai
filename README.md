# RESOLVE AI — MVP local

## Como rodar
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Modos de operação
- **Mock AI (default):** sem chave de API, o motor usa Regex + regras
  determinísticas. Tudo funciona offline.
- **LLM real:** crie um arquivo `.env` na raiz com
  `OPENAI_API_KEY=sk-...` (usa gpt-4o-mini) ou
  `ANTHROPIC_API_KEY=sk-ant-...` (usa claude-3-haiku).

## Estrutura
- `db.py` — SQLite (users, items) + consultas do dashboard/scheduler
- `ai_engine.py` — motor de ingestão (LLM + fallback Mock)
- `scheduler.py` — cronjob proativo (vencimentos D+3 / anti-churn D+10)
- `app.py` — interface Streamlit (LP, Onboarding, Simulador, Dashboard)

## Fluxo de teste sugerido
1. Onboarding → criar usuário → "Popular dados de exemplo"
2. Simulador → testar os 5 tipos de entrada (texto, áudio, imagem+texto,
   imagem silenciosa, vídeo)
3. Sidebar → "⚡ Executar Motor de Disparo Proativo (Simular Dia Seguinte)"
4. Onboarding → "Simular 11 dias de inatividade" → rodar motor de novo
   para ver o gatilho anti-churn
5. Dashboard → KPIs, donut chart e export CSV
