# Templates para submeter no Business Manager

GERADO POR `templates/gerar_submissao.py` — não edite à mão. Mude o catálogo
em `templates/__init__.py` e rode o script de novo.

Para cada template abaixo, no Business Manager:
WhatsApp Manager > Modelos de mensagem > Criar modelo

- **Categoria:** Utilidade (UTILITY)
- **Idioma:** Português (BR) — `pt_BR`
- **Cabeçalho:** nenhum
- **Rodapé:** nenhum
- **Botões:** nenhum nesta primeira leva (ver DECISOES.md)

ARMADILHA DA INTERFACE, e ela custou meia hora: no passo "Configurar seu
modelo", clicar na aba **Utilidade** NÃO fixa a categoria. É preciso clicar
na aba **e depois marcar o rádio "Padrão"** logo abaixo dela. Se pular o
rádio, o passo seguinte aparece com "Marketing · Padrão" no cabeçalho e o
template inteiro vai para a categoria errada — e categoria errada na Meta é
preço errado e regra de opt-out errada.

**Sempre confira o cabeçalho da tela de edição antes de preencher.** Ele diz
a categoria em letra pequena embaixo do nome.

Depois que a Meta aprovar, configure no EasyPanel:

```
TEMPLATES_APROVADOS=<nomes aprovados, separados por vírgula>
```

Enquanto um template não estiver nessa lista, o bot NÃO envia nada por ele
fora da janela de 24h — a mensagem fica registrada como não entregue, com o
motivo, em vez de sumir.

---

## `resolveai_lembrete_hora`

- **Nome:** `resolveai_lembrete_hora`
- **O que faz:** Avisa na hora marcada de um compromisso
- **Categoria:** UTILITY
- **Idioma:** pt_BR

**Botões:** nenhum

**Corpo:**

```
Chegou a hora: *{{1}}*.

Responda *feito* que eu dou baixa, ou *adiar 1h* se precisar de mais tempo.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `levar o carro na revisão`

**Justificativa (cole no campo de descrição, se pedido):**

> Lembrete de hora marcada que o próprio usuário cadastrou no assistente, com data e hora escolhidas por ele. Disparado uma única vez, no horário que ele pediu. O usuário responde 'feito' ou 'adiar' na mesma conversa.

---

## `resolveai_item_vencido`

- **Nome:** `resolveai_item_vencido`
- **O que faz:** Cobra um item que venceu e nao teve baixa
- **Categoria:** UTILITY
- **Idioma:** pt_BR

**Botões:** nenhum

**Corpo:**

```
Oi {{1}}, *{{2}}* venceu {{3}} e eu não registrei a baixa.

Responda *feito* se já resolveu, ou *adiar* que eu remarco.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `Kevin`
  - `{{2}}` -> `conta de luz`
  - `{{3}}` -> `ontem`

**Justificativa (cole no campo de descrição, se pedido):**

> Aviso de vencimento de um compromisso que o usuário cadastrou (conta, consulta, prazo). Enviado no máximo uma vez por item, e o usuário pode encerrar ou remarcar respondendo na conversa.

---

## `resolveai_resumo_do_dia`

- **Nome:** `resolveai_resumo_do_dia`
- **O que faz:** Resumo dos compromissos dos proximos dias
- **Categoria:** UTILITY
- **Idioma:** pt_BR

**Botões:** nenhum

**Corpo:**

```
Oi {{1}}, você tem *{{2}}* compromisso(s) guardado(s) para os próximos dias.

O mais próximo é *{{3}}*.

Responda *ver tudo* para a lista completa.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `Kevin`
  - `{{2}}` -> `3`
  - `{{3}}` -> `IPVA (vence 20/08)`

**Justificativa (cole no campo de descrição, se pedido):**

> Resumo dos compromissos que o próprio usuário cadastrou, no dia da semana que ele escolheu ao se cadastrar. Conteúdo é exclusivamente a agenda dele; não há oferta nem divulgação.

---

## `resolveai_reengajamento_pendentes`

- **Nome:** `resolveai_reengajamento_pendentes`
- **O que faz:** Lembra de um item parado ha dias na lista
- **Categoria:** UTILITY
- **Idioma:** pt_BR

**Botões:** nenhum

**Corpo:**

```
Oi {{1}}, *{{2}}* continua na sua lista desde {{3}} e eu não registrei a baixa.

Responda *ver tudo* para revisar seus itens.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `Kevin`
  - `{{2}}` -> `trocar o óleo do carro`
  - `{{3}}` -> `12/08`

**Justificativa (cole no campo de descrição, se pedido):**

> Aviso sobre um compromisso específico que o próprio usuário cadastrou no assistente e que segue em aberto, com a data em que ele foi criado. O conteúdo é o dado dele, e a ação oferecida é encerrar ou remarcar esse item na mesma conversa.

---

## `resolveai_fim_de_trial_aviso`

- **Nome:** `resolveai_fim_de_trial_aviso`
- **O que faz:** Avisa que o teste esta acabando e oferece a assinatura
- **Categoria:** MARKETING
- **Idioma:** pt_BR

**Botões:** nenhum

**Corpo:**

```
Oi {{1}}, seu teste grátis acaba em *{{2}}* dia(s).

Nesse tempo eu guardei *{{3}}* compromisso(s) seu(s) e te avisei antes de cada um vencer. Depois que acabar, tudo continua guardado aqui — mas eu paro de te avisar.

São R$ 19,90 por mês pra seguir. Responda *assinar* que eu te mando o link.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `Kevin`
  - `{{2}}` -> `2`
  - `{{3}}` -> `7`

**Justificativa (cole no campo de descrição, se pedido):**

> Aviso ao usuário de que o período de teste da conta dele está acabando, com o número de compromissos que ele cadastrou, e oferta de continuidade do serviço. O usuário responde na mesma conversa para receber o link de pagamento.

---

## `resolveai_conta_a_vencer`

- **Nome:** `resolveai_conta_a_vencer`
- **O que faz:** Avisa que uma conta vence em breve
- **Categoria:** UTILITY
- **Idioma:** pt_BR

**Botões:** nenhum

**Corpo:**

```
Oi {{1}}, *{{2}}* vence em *{{3}}*.

Responda *feito* quando resolver, ou *adiar* que eu remarco.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `Kevin`
  - `{{2}}` -> `conta de luz`
  - `{{3}}` -> `20/08`

**Justificativa (cole no campo de descrição, se pedido):**

> Aviso de vencimento de um compromisso financeiro que o próprio usuário cadastrou no assistente, com a data que ele informou. Enviado uma vez por item por dia de aviso, e o usuário pode encerrar ou remarcar respondendo na mesma conversa.

---

## `resolveai_trial_estendido`

- **Nome:** `resolveai_trial_estendido`
- **O que faz:** Conta que voce liberou mais dias de teste
- **Categoria:** UTILITY
- **Idioma:** pt_BR

**Botões — tipo `Resposta rápida`, um por linha:**

  - `Ver tudo`

**Corpo:**

```
Oi {{1}}, liberei mais *{{2}}* dia(s) de teste pra você.

Seu acesso vale até *{{3}}*. Continuo te avisando dos seus compromissos até lá.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `Ana`
  - `{{2}}` -> `7`
  - `{{3}}` -> `12/09/2026`

**Justificativa (cole no campo de descrição, se pedido):**

> Confirmação de mudança no prazo da conta do próprio usuário. O administrador estendeu o período de teste e o usuário é informado da nova data de validade do acesso que ele já contratou. Não contém oferta, preço ou link de compra. Enviada uma vez a cada extensão, apenas para o usuário afetado.

---

## `resolveai_cobranca_link`

- **Nome:** `resolveai_cobranca_link`
- **O que faz:** Cobra quem pediu o link e nao pagou
- **Categoria:** MARKETING
- **Idioma:** pt_BR

**Botões — tipo `Resposta rápida`, um por linha:**

  - `Já paguei`
  - `Assinar`

**Corpo:**

```
Oi {{1}}, você pediu o link do Resolve AI há *{{2}}* dia(s) e eu ainda não vi o pagamento entrar.

Se já pagou, me avisa que eu libero na hora. Se preferir, posso te mandar o link de novo.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `Ana`
  - `{{2}}` -> `3`

**Justificativa (cole no campo de descrição, se pedido):**

> Acompanhamento de uma assinatura que o próprio usuário solicitou. Ele pediu o link de pagamento no assistente e a cobrança ainda não foi confirmada. A mensagem só é enviada para quem pediu o link, uma vez por ciclo de cobrança, e sempre por ação manual do administrador.

---

## `reativar_boas_vindas`

- **Nome:** `reativar_boas_vindas`
- **O que faz:** Pede desculpa pelo apagão e ensina a usar (14 dias valendo)
- **Categoria:** MARKETING
- **Idioma:** pt_BR

**Botões — tipo `Resposta rápida`, um por linha:**

  - `Quero comecar`

**Corpo:**

```
Oi, {{1}}! Aqui e o Resolve AI. 👋

Voce se cadastrou pra testar e a gente falhou: nosso sistema ficou fora do ar e voce nao recebeu resposta. Foi erro nosso, e pedimos desculpa.

Ja esta tudo funcionando, num numero novo e oficial. E seus 14 dias gratis estao intactos, valendo a partir de agora.

Pra comecar, me manda uma coisa que voce nao pode esquecer:

"luz 187 vence dia 20"
"dentista dia 15 as 14h"

Eu te aviso antes, sozinho, aqui no Zap. E se nao quiser mais, e so responder parar que eu nao te incomodo de novo.
```

**Variáveis (exemplo para a submissão):**

  - `{{1}}` -> `Leonardo`

**Justificativa (cole no campo de descrição, se pedido):**

> Retomada de contato com usuários que se cadastraram no assistente e ficaram sem resposta por uma falha de infraestrutura nossa. A mensagem reconhece a falha, informa que o período de teste que eles contrataram segue válido, e oferece opt-out explícito já na primeira interação.

---
