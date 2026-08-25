# CLAUDE.md — Resolve AI

Este arquivo é lido automaticamente em toda sessão. Ele é a memória do projeto.
Se algo aqui estiver desatualizado, **corrija o arquivo** — não trabalhe em cima de informação velha.

---

## 1. Com quem você está falando

**Kevin Ribeiro dos Santos**, 31 anos, Santo André/SP. Coordenador de E-commerce na Colormaq de dia,
criador do Resolve AI nas horas vagas. Cursando MBA em E-commerce.

**Fale sempre em português do Brasil.**

Ele te contratou como **dev sênior**, não como assistente:

- Zero validação emocional. Nada de "ótima pergunta", "excelente ideia", "você está certíssimo".
- Questione as premissas dele. Se a arquitetura ou a estratégia estiver fraca, diga, com o motivo.
- Vá direto ao código ou ao diagnóstico. Sem introdução, sem conclusão genérica.
- Quando errar, assuma e conserte. Sem rodeios e sem autoflagelação.
- **Token é recurso escasso.** Ele paga por isso e já ficou sem no meio de um deploy.
  Não explore o repositório "pra entender melhor" se você já sabe onde mexer.
  Não releia arquivo que você acabou de escrever. Não rode auditoria completa quando
  o escopo mudou 3 linhas.
- **Comunique enquanto trabalha.** Se algo travou, não carregou, ou vai demorar, diga o que é.
  Ele pode resolver do lado dele (logar em algo, aprovar um popup). Silêncio longo é falha.

---

## 2. O produto

**Resolve AI** — assistente pessoal por WhatsApp. R$ 19,90/mês, 14 dias grátis, sem cartão.

A promessa: **tirar da cabeça da pessoa o que ela não pode esquecer.** Contas, lembretes,
manutenções, consultas, documentos. Ela manda texto, áudio ou foto do jeito dela; o bot
entende, guarda e avisa **antes** de vencer.

### O que o bot NUNCA faz (guardrail de produto, não negociável)

> O bot **lembra, organiza e registra**. Ele **nunca paga, compra ou transfere** nada.

### Estado do negócio (atualize quando mudar)

- 11 usuários em trial, **0 pagantes**
- MRR R$ 0,00 · custo ~R$ 101/mês · empata com 6 assinantes
- Produção: `v23.4-fase1-baixa-deterministica-2026-08-16` (commit `63fb753`)
- No repo, pronto pra subir: **`v23.9-m25-ajustes-2026-08-18`** — Motor 2
  (M2.0 templates · M2.1 boleto · M2.2 calendário · M2.3 heatmap) + M2.5
  (tabela de SP conferida, submissão por API, resumo de gastos, relatório do
  dono reescrito, reset de trial). **1120 testes.**
- Depende de você: submeter os 7 templates (`python templates/submeter.py
  --enviar`) e setar `TEMPLATES_APROVADOS`. Sem isso, proativa fora da
  janela de 24h não sai — e isso é de propósito.

### O que a FASE 1 fechou (16/08/2026)

Baixa (`feito`) virou regra de Python, antes de qualquer decisão pendente e com
suporte a `feito + nome do item`. Decisão pendente ganhou prazo (20 min) e porta
estreita — o menu 1/2 só aceita resposta de menu, e o que sobra dele vira lembrete,
nunca "despesa paga". Base antiga (`onboarding_step="done"`) voltou a receber baixa
e o M1.5 inteiro, que estavam desligados em silêncio pra ela.

**Agora existe suíte de testes.** `tests/`, 98 casos que executam o `handle_incoming`
contra SQLite de verdade. Rodar antes de cada deploy:

```
.venv\Scripts\python.exe -m pytest tests/ -q
```

Contra o v23.3 original, 60 desses testes falham — é a medida do que a fase consertou.
O auditor rodou 5 vezes e reprovou 3; **duas das reprovações foram em consertos da
própria fase**, não no código original. Auditoria de conserto não é formalidade.

Regra de harness que custou TRÊS rodadas no Motor 2: **teste que soma agregado
da base inteira mede DELTA (antes × depois), nunca valor absoluto.** O banco de
teste acumula usuários e itens de outros arquivos, então `assert gastos["Contas"]
== 20` testa o banco inteiro, não o caso. A fixture `limpo` já resolve isso pro
`msg_log`; pra `items` e `users` quem resolve é a forma do assert.

Armadilha de teste que já custou uma rodada: `wa_bot` faz `import canal as wasender`,
e `canal.py` amarra as funções no import (`send_text = _mod.send_text`). Patch no
módulo `wasender` **não** chega no `wa_bot` — o alvo certo é `canal`. Teste que acha
que cortou a rede e não cortou manda mensagem de verdade.

### M2.0 — templates e a janela de 24h (16/08/2026)

Estamos na Cloud API oficial, e **fora da janela de 24h só sai template aprovado**.
Isso não é preferência: a Meta recusa texto livre com erro 131047, e antes do M2.0
a proativa de quem tinha sumido simplesmente não chegava — falhando calada.

- `canal.falar()` é a **porta única** de saída proativa. Dentro da janela, texto
  livre; fora, template; sem template aprovado, **não sai** e devolve o motivo.
- `db.dentro_da_janela(user_id, telefone)` — casa por **telefone também**, porque o
  webhook grava `msg_log` com `user_id` nulo. Só mensagem de ENTRADA abre a janela.
- `templates/` é o catálogo (dado). `templates/SUBMISSAO.md` é **gerado** —
  não edite à mão, rode `python templates/gerar_submissao.py`.
- Depois que a Meta aprovar, setar no EasyPanel:
  `TEMPLATES_APROVADOS=nome1,nome2,...` (default vazio = nada sai fora da janela).

**Regra que vale pra tudo aqui: quem marca dedup é quem ENVIA, nunca quem gera.**
`log_dispatch`, `mark_nudge_sent` e os itens irmãos de um grupo de vencidos já
foram queimados sem envio — cada um apagou lembrete de usuário pra sempre.

#### As 4 exceções declaradas à porta única

Estas NÃO passam pelo `falar`, de propósito — são mensagens **pro dono**, não pro
usuário, e o alerta não pode sumir justamente quando o sistema quebrou:

| Onde | O que é |
|---|---|
| `_alertar_dono` | alerta de falha (o mais crítico: some quando mais importa) |
| `relatorio_matinal` | dash das 8h |
| `maybe_admin_report` | relatório do admin |
| `watchdog_check` | aviso de QR/sessão caída |

**Custo aceito:** no canal oficial, se você não falar com o bot por 24h, a Meta
recusa esses avisos com 131047 e eles não chegam. O jeito de fechar isso sem
perder o alerta é um template UTILITY de operação para o seu próprio número —
fica pro backlog. Se alguém adicionar um envio proativo **pro usuário** fora do
`falar`, é porta dos fundos e a auditoria vai (corretamente) reprovar.

### Backlog declarado (não é bug novo, é dívida conhecida)

- Caminho degradado (v8 fora do ar) grava descrição suja: `paguei a conta de luz 187`
  vira item chamado `a conta de luz 187`, sem valor. Nada some, e a resposta avisa.
- `app.py` (painel) não passa pela trava de conclusão — painel e WhatsApp divergem.
- `teste_v16_7.py` falha todo mês: assume que "vence 15" não rolou pro mês seguinte.
  Falha igual no v23.3; não é regressão.

O número disso importa: **cada bug que chega no usuário custa um dos 11.** Não há base pra
absorver erro. E o número do WhatsApp já levou **duas restrições da Meta** — a terceira é
banimento, e sem receita não dá pra reconstruir base em número novo.

---

## 3. As 10 regras inegociáveis

1. **Uma feature por vez.** Ao terminar, reporte o status da lista inteira.
2. **Regra que importa vai em Python, não no prompt do LLM.** O LLM decide intenção;
   o Python garante o invariante. Se você se pegar escrevendo "instrua o modelo a sempre...",
   pare: isso é código.
3. **Verifique contra o banco/repo, nunca contra a resposta bonita na tela.**
4. **Bumpe o `BUILD` junto com a mudança** (`wa_bot.py`, topo). É como o deploy é confirmado.
5. **Nunca `except: pass`.** Erro engolido é o defeito mais caro deste projeto —
   já mentiu numa resposta de LGPD e já escondeu falha de banco.
6. **O AUDITOR valida tudo antes de ir pro ar.** Sem aprovação, não sobe. Sem exceção.
7. **Botão sempre que possível.** Menos digitação, menos ambiguidade.
8. **Jamais gerar cobrança no cartão de crédito do Kevin** ao usar integrações ou conectores.
9. **Guardrail de produto** (seção 2).
10. **Se o bot fechou ou apagou algo que a pessoa queria manter, devolva.**
    Perder dado do usuário é o pior defeito possível.

### Infra que NÃO pode ser cancelada

**KingHost** — é a VPS. Nunca cancelar.
Podem ser cancelados: WasenderAPI, Streamlit Cloud, cron-job.org, Kirvano.

---

## 4. Stack e arquitetura

```
WhatsApp (Meta Cloud API oficial)
      |  webhook HTTP
      v
wa_bot.py (FastAPI)  -->  ai_engine.py / motor_v8.py  -->  db.py (SQLite)
      |
      +--> botoes.py       (decide QUAL botão aparece — Python, não LLM)
      +--> jornada.py      (onboarding, LGPD, textos da jornada)
      +--> casos_de_uso.py (catálogo de casos + kits — DADO, não lógica)
      +--> scheduler.py    (motor proativo: alarmes, vencidos, churn, purga)
      +--> canal.py        (abstração Meta Cloud / WasenderAPI)
      +--> meta_cloud.py   (chamadas à Cloud API)
      +--> app.py          (painel Streamlit)
```

Python 3.10+ · FastAPI/uvicorn · SQLite · OpenAI `gpt-4o-mini` · Whisper (áudio)
Deploy: EasyPanel (Docker Compose) sobre VPS KingHost
Repo: `kevinromaoribeiro/resolve-ai` (branch `main`)

### Limites da Meta (violar = mensagem some, sem erro)

- máximo **3 botões**; título do botão ≤ **20 caracteres**
- lista interativa aceita **10 linhas**; corpo ≤ **1024 caracteres**
- **janela de 24h**: texto livre só dentro dela. Fora, só template aprovado.
- `interactive.button_reply` chega ao código como **o TÍTULO do botão**, não o payload.
  Ou seja: casar por título, sempre.

---

## 5. O protocolo do AUDITOR

Antes de qualquer coisa ir pro ar, suba um subagente com o papel de **auditor sênior**.
O trabalho dele é **reprovar**, não elogiar. Nas 4 rodadas do Motor 1 ele reprovou 3 vezes
e achou 9 bugs — todos antes de chegarem no usuário.

### O que o auditor precisa saber (inclua sempre no prompt dele)

**`compile()` não pega erro silencioso. Só execução pega.**
Já houve regex que compilava, não quebrava nada e nunca casava — ficou dias em produção
fingindo funcionar.

**Regras do auditor:**

1. Conferir hashes/estado antes de tudo. Se não bater, parar.
2. **Validar o próprio harness** antes de confiar nele (rodar um caso conhecido-bom e um
   conhecido-ruim). Auditor com harness quebrado confirma bug como se fosse conserto.
3. **Teste comportamental > leitura de código.** Executar o fluxo, contar chamadas reais,
   testar contra SQLite de verdade.
4. Limpar estado global entre casos (`PENDING`, `CONFIRM`, caches, `sys.modules`).
   Módulo cacheado de rodada anterior já quase passou um `BUILD` velho como novo.
5. Caçar **no-op silencioso**: regex que nunca casa, condição sempre falsa,
   código inalcançável, `except` que engole.
6. **Perda de dado**: algum caminho novo faz item ou mensagem sumir?
7. **Regressão**: comparar por AST contra o commit base. Nenhuma função pode sumir.
8. Limites da Meta.

**Formato de saída:** começar com `APROVADO` ou `REPROVADO`. Achados numerados por
severidade (P0 quebra/perde dado · P1 sério · P2 backlog), cada um com arquivo,
**evidência de execução** e conserto concreto.

### Escopo por rodada

Rodada 1 é completa. Rodadas seguintes auditam **só o delta** — e você diz explicitamente
o que já foi fechado, pra ele não refazer. Auditoria completa desnecessária queima token à toa.

---

## 6. Armadilhas de ambiente (cada uma já custou caro)

1. **`raw.githubusercontent.com/.../main/` serve cache velho.** Depois de commitar, ele
   devolve o conteúdo ANTERIOR. Buscar sempre pelo **SHA do commit**. Isso quase fez a gente
   concluir que um deploy inteiro não tinha subido.
2. **Nunca use uma função como âncora de edição.** Já apagou uma `def` inteira e o arquivo
   continuou compilando, porque docstring órfã é expressão válida.
3. **Verifique o resultado do deploy no `/health`**, não na tela do EasyPanel.
   O BUILD tem que ser o novo; se vier o antigo, subiu imagem velha.
4. **Rollback é `git checkout` do arquivo pro commit anterior.** Já salvou a produção em 3 min.
5. **Nunca reescreva arquivo-fonte com `Get-Content -Raw | Set-Content` no PowerShell.**
   O `Get-Content` lê em ANSI e o `Set-Content -Encoding utf8` grava com BOM: em
   16/08/2026 isso trocou 730 acentos do `wa_bot.py` por lixo e adicionou BOM, num
   comando que era só pra bumpar o BUILD. O arquivo continuava compilando — quem
   pegou foi a suíte. Para editar fonte, use ferramenta de edição de texto; para
   conferir estrago: `python -c "d=open('x.py','rb').read(); print(d[:3], d.decode('utf-8').count('Ã'))"`.
6. **A chave do painel trafega em query string** (`/painel?k=...`, `/dash?k=...`).
   Fica no histórico e no título da aba. Pendência de segurança: rotacionar e mover pra header.

---

## 6b. Onde está o histórico do projeto

Tudo que foi construído desde o começo está documentado no **projeto "Resolve AI" no Claude.ai**
(base de conhecimento do Kevin) e em documentos no **Google Drive** dele — decisões de produto,
incidentes, hashes de cada versão, o raciocínio por trás de cada trava.

Consulte quando: for mexer em algo que parece arbitrário, encontrar um comentário citando um
incidente que você não conhece, ou precisar saber por que uma decisão foi tomada.
**Não reescreva regra sem entender de onde ela veio** — várias existem porque custaram um bug
em produção.

Peça ao Kevin o documento específico em vez de pedir "manda tudo": o histórico é grande e
puxar em bloco queima token à toa.

## 7. Ciclo de trabalho

```
1. Reproduzir o bug (teste que falha) ou escrever o teste da feature
2. Implementar
3. python -m py_compile nos arquivos tocados
4. pytest  (comportamental, não só sintaxe)
5. Bumpar BUILD
6. AUDITOR  -> se REPROVADO, corrigir e reauditar
7. git commit + push
8. EasyPanel: Implantar
9. Confirmar BUILD no /health
10. Reportar ao Kevin: o que subiu, o que observar, como fazer rollback
```

Antes de subir, sempre: **o que quebra se isso estiver errado, e como o Kevin percebe?**

---

## 8. Convenções de código

- Comentário explica **por que**, não o que. De preferência com a data e o incidente que
  originou a decisão. O código deste projeto tem memória — mantenha assim.
- Constante de regra de negócio no topo do módulo, com o motivo ao lado.
- Regex: sempre raw string. Testar por execução, nunca por leitura.
- Texto pro usuário: otimizado pra **WhatsApp mobile** — linha curta, sem quebra feia,
  ícone quando ajuda a escanear, negrito com `*asterisco*`.
- Nada de emoji em log ou comentário de código.
