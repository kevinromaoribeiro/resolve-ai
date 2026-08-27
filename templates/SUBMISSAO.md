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
- **Categoria:** UTILITY
- **Idioma:** pt_BR

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
- **Categoria:** UTILITY
- **Idioma:** pt_BR

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
- **Categoria:** UTILITY
- **Idioma:** pt_BR

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
- **Categoria:** UTILITY
- **Idioma:** pt_BR

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
- **Categoria:** MARKETING
- **Idioma:** pt_BR

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
- **Categoria:** UTILITY
- **Idioma:** pt_BR

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
