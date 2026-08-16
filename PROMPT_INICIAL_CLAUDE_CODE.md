# PROMPT INICIAL — cole isto na primeira mensagem do Claude Code

> Antes de colar: coloque o `CLAUDE.md` na raiz do repositório clonado.
> Ele carrega sozinho e evita reexplicar contexto toda sessão.

---

Você é meu **dev sênior** no Resolve AI. Leia o `CLAUDE.md` na raiz antes de qualquer coisa —
ele tem as regras, a arquitetura, as armadilhas de ambiente e o protocolo do auditor. Siga tudo.

Fale português. Direto, sem elogio, sem enrolação. Questione minhas premissas quando estiverem
fracas. **Não torre meus tokens** — e me avise quando algo travar em vez de ficar em silêncio.

## Contexto de arranque

Produção está em `v23.3-motor1-auditado-2026-08-15`, estável, Motor 1 completo no ar.
11 usuários em trial, 0 pagantes.

Até agora eu trabalhava com você por uma interface sem acesso a arquivo: cada deploy era
zipar código no navegador, baixar, colar no editor do GitHub e conferir hash. Dois incidentes
de produção nasceram desse encanamento — um `SyntaxError` que derrubou o bot e uma regex que
compilava mas nunca casava, e ficou dias no ar fingindo funcionar.

Agora você tem arquivo, git e terminal de verdade. **Use isso.** O padrão daqui pra frente é
teste que executa, diff antes de commit, e rollback por `git checkout`.

## FASE 0 — Se orientar (faça primeiro, e seja econômico)

1. `git log --oneline -15` e confirme que o HEAD é o v23.3.
2. Leia **só** o que precisar: `wa_bot.py` é grande (~3.680 linhas), não leia inteiro.
   Use `grep`/`rg` pra achar o que interessa.
3. Rode `python -m py_compile` nos 5 arquivos principais e me diga se algo já está quebrado.
4. **Não existe suíte de testes.** Isso é a maior dívida técnica do projeto e a causa raiz
   dos dois incidentes. Antes de escrever feature nova, crie `tests/` com pytest e cubra
   os caminhos que os bugs da FASE 1 revelam. Teste que não executa o fluxo não conta.

Me reporte a FASE 0 em no máximo 10 linhas antes de seguir.

## FASE 1 — Os bugs que peguei em produção (prioridade sobre o Motor 2)

Estes são de conversa real, minha, no WhatsApp. Alguns podem já ter sido resolvidos pelo
Motor 1 que acabou de subir — **reproduza antes de consertar**, não confie na minha descrição.
Escreva o teste que falha primeiro.

### P0-1 — "feito" não dá baixa. O bot pede a palavra e recusa a palavra.

O caso mais grave, porque quebra a promessa central do produto e aconteceu **3 vezes**:

```
[10:00 12/08] Bot:  chegou a hora: Estudar Product Manager
                    Responda feito que eu dou baixa, ou adiar 1h.
[10:02 12/08] Kevin: feito
[10:02 12/08] Bot:  Não entendi. Responda *1* (despesa paga), *2* (agendar lembrete)
```

Repetiu em 13/08 09:00 com "Feito" (maiúsculo) e em 14/08 08:00 com "feito".

**Hipótese a confirmar, não a assumir:** o bloco de decisão pendente (`PENDING`, menu 1/2)
em `wa_bot.py` roda **antes** do tratamento de conclusão, e sequestra a mensagem — manda ela
pro `ai_engine.converse(..., pending=...)`, que responde com o menu de desambiguação.
Confirme por execução onde a mensagem é interceptada.

Ao consertar: `feito`, `Feito`, `FEITO`, `ja fiz`, `já fiz`, `resolvi`, `pago`, `paguei`
devem dar baixa **em Python, deterministicamente**, com prioridade sobre qualquer `PENDING`.
E `adiar` / `adiar 1h` idem. Isso é a regra 2 do `CLAUDE.md`.

### P0-2 — Item classificado errado, dado corrompido

Sequência de 14/08: o item vencido era **"falar com o dentista"** — um lembrete.
Eu respondi `feito`, o bot não entendeu, ofereceu o menu, eu respondi `1`, e ele arquivou:

```
[08:01 14/08] Bot: Feito. Arquivado como *Despesa Paga*.
```

Um lembrete de dentista virou despesa paga no meu histórico. O menu `1/2` foi aplicado a um
item cuja natureza **já era conhecida**. O menu não deveria nem ter aparecido; e se aparecer,
não pode reclassificar um item existente. Investigue os dois lados: por que o menu apareceu,
e por que a opção `1` sobrescreveu o tipo do item.

### P0-3 — Motor devolve JSON sem `reply`, usuário recebe resposta genérica

```
[23:44 11/08] ALERTA: json sem reply/intent ::
  {"intent":"conversa","reply":"","itens":[],"atualizar":null,"concluir":null,"memoria":[]}
[23:44 11/08] Bot p/ usuário: Entendi, mas não ficou claro o que você gostaria de
              registrar ou resolver. Tem algo específico em mente?
```

O gatilho foi eu tocar em **"✅ Isso mesmo"** — que agora é tratado em Python (fix das regex do
v23.3), então o gatilho específico deve estar morto. **Mas o modo de falha continua vivo:**
quando o LLM devolve `reply` vazio, a pessoa recebe uma pergunta genérica que não faz sentido
no contexto. Precisa de fallback determinístico que use o que o Python já sabe do estado da
conversa, em vez de improvisar.

### P1-4 — Onboarding fora de ordem

Em 11/08 23:43 o bot **anotou um item** ("Estudar Product Manager amanhã às 10h") e logo depois
me mandou:

```
Perfeito, kevin! ✅
Vamos direto ao ponto: me manda uma coisa que você não pode esquecer.
```

Ele pediu a primeira demanda **depois** de já ter guardado uma. Além disso, o onboarding pediu
nome e interesses, e no meio disso já processou item. Verifique a ordem dos blocos no
`handle_incoming` e se o onboarding está sendo fechado quando deveria.

### P1-5 — Régua de saúde com leitura invertida

Do relatório diário:

```
12/08  🟢 ok      pico 4/min · 6 proativas em 24h
13/08  🔴 alto    pico 1/min · 9 proativas em 24h
15/08  🔴 alto    pico 2/min · 9 proativas em 24h
14/08  🟡 atenção pico 2/min · 5 proativas em 24h
```

Pico 4/min aparece como verde e pico 1/min como vermelho. O risco está sendo calculado pelas
proativas mas o "pico" é exibido colado no rótulo, o que faz a linha se ler como contraditória.
Ou a lógica está errada, ou a copy está. Descubra qual e conserte — eu leio isso todo dia e
preciso confiar.

### P1-6 — Métrica se contradiz na mesma mensagem

```
⚪ sem usuário real ainda
0.0 demandas por pessoa/dia (7d · 0 pessoa(s))
Base: 11 pessoa(s) · 11 em teste · 0 pagando
```

"0 pessoa(s)" e "11 pessoa(s)" na mesma tela. O denominador provavelmente exclui alguém
(dono? inativos?) sem dizer. Ou explicite o critério na copy, ou corrija o cálculo.

### P2-7 — Cobrança diária de item vencido

"venceu ontem e não vi a baixa" chegou em dias seguidos. O M1.5 (escalonamento após 3
adiamentos) já deve cobrir parte — **confirme que cobre** e que o item vencido não vira
cobrança infinita.

---

**Ordem de trabalho da FASE 1:** um bug por vez, teste que falha primeiro, conserto,
teste que passa. Ao terminar todos, bumpe o BUILD, chame o **auditor** com o escopo do delta,
e só suba com aprovação.

## FASE 2 — Motor 2 (só depois da FASE 1 no ar)

- **M2.1 — OCR de boletos e PDFs.** A pessoa manda foto do boleto; o bot extrai valor,
  vencimento e beneficiário, e guarda como despesa com data. É o caso de uso que mais aparece
  na base. Atenção ao guardrail: extrair e lembrar, **nunca pagar**.
- **M2.2 — Lembretes dinâmicos via APIs externas.** Datas que o bot pode saber sozinho
  (IPVA, licenciamento, feriados, prazos). Cada fonte externa precisa de fallback: se a API
  cair, o lembrete não pode sumir nem virar data errada.
- **M2.3 — Heatmap de constância + dash de gastos.** Visual de uso e de despesa por categoria.

Antes de codar qualquer um: me traga a proposta em até 15 linhas — o que entra, o que fica de
fora, e qual o invariante em Python. Eu aprovo, aí você implementa.

## Como quero o reporte

Ao terminar cada bloco: o que mudou, como você **provou** que funciona, o que observar em
produção nas primeiras horas, e como fazer rollback. Nessa ordem, curto.
