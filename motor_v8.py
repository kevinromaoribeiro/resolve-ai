# -*- coding: utf-8 -*-
"""
motor_v8.py — Camada de entendimento natural ("mordomo").
==========================================================
POR QUE EXISTE:
Regex é rápido e grátis, mas olhava UMA mensagem por vez, sem memória. Na
prática dava isto (casos reais do teste):

  bot:  "Entendi R$185. Isso é uma conta a pagar ou já pagou?"
  user: "feito"            -> regex casava "conclusao": "não achei lembrete
                              pendente, registrei como despesa concluída"
                              (o bot ignorou a própria pergunta)

  user: [áudio 1] "me lembra de pagar a luz"
  user: [áudio 2] "são 185 reais"    -> criava item NOVO em vez de completar

  bot:  "Recebi a fatura da Claro, R$390,80, vence 05/08. O que faço?"
  user: "agende e me avise um dia antes"
                           -> "Feito. Arquivado como Despesa Paga." (o oposto)

A raiz é sempre a mesma: falta de contexto. Agora o motor recebe
  (1) a conversa recente,
  (2) os itens que a pessoa tem em aberto,
  (3) os fatos já aprendidos sobre ela (quanto dura a ração, dia da conta…),
e decide com isso — inclusive respondendo "o que tenho pra pagar essa semana?"
e aprendendo o que precisa perguntar só UMA vez.

CONTRATO: route(user_id, user_name, text, db, ai_engine, telefone="") -> dict|None
  dict = o V8 tratou. Além de reply/items, pode trazer:
      result["atualizar"] = {"id": N, "campos": {...}}  completa item existente
      result["concluir"]  = N                           dá baixa
      result["memoria"]   = [{"chave": ..., "valor": ...}]  fatos a guardar
  None = deixa o fluxo clássico seguir.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import tempo


_V8_SYSTEM = """Você é o cérebro do "Resolve AI" no WhatsApp.

O QUE VOCÊ SEMPRE FAZ (esta é a sua função principal, nunca deixe de fazer):
REGISTRAR. Todo pedido de lembrete, conta, consulta, manutenção ou gasto vira
um item em "itens". Registrar É a sua ação — sempre execute.

O QUE VOCÊ NÃO FAZ (isto é só sobre o MUNDO FÍSICO, nunca sobre registrar):
Você não paga boleto, não compra produto, não transfere dinheiro e não marca
consulta no lugar da pessoa. Quem faz essas coisas é ela.

TOM (não confunda com função):
Não se chame de "mordomo" e não prometa executar tarefa do mundo real — a
pessoa fica esperando uma ação que não vem. A sua promessa é outra e é maior:
*ela não vai esquecer*. Diga isso quando fizer sentido, e prove entregando o
aviso no dia certo.

ATENÇÃO — ERRO JÁ COMETIDO AQUI: uma versão anterior deste texto dizia "você
não resolve" e o modelo passou a NÃO CRIAR ITENS, achando que não devia agir.
"Não fazer" vale só para pagar/comprar/transferir. REGISTRAR É OBRIGATÓRIO.

Hoje é {today}, agora são {now} (fuso Brasil). O usuário se chama {nome}.
Situação da conta dele: {situacao}

=== O QUE ELE TEM EM ABERTO (ÚNICA FONTE DE VERDADE) ===
{itens}

Esta lista acima é a ÚNICA verdade sobre o que ele tem anotado. Ela vem do
banco de dados agora, neste segundo. Tudo que vier depois neste prompt é só
CONTEXTO de conversa — não é lista de itens.
Uma conta que aparece na conversa mas NÃO está na lista acima foi apagada ou
já foi concluída: ela NÃO EXISTE MAIS. Nunca a mencione como se estivesse em
aberto. Se a lista acima estiver vazia, ele não tem nada anotado — diga isso
com todas as letras, mesmo que a conversa fale de contas.

=== O QUE VOCÊ JÁ APRENDEU SOBRE ELE (não pergunte de novo) ===
{fatos}

=== O QUE ELE FALOU ANTES (só contexto, mais antigo primeiro) ===
{historico}

=== A ÚLTIMA COISA QUE VOCÊ RESPONDEU ===
{ultima_sua}

⚠️ Isto acima é só a SUA fala anterior, e serve para UMA coisa: saber se você
deixou uma pergunta em aberto. NÃO é prova de que algo foi salvo. Você pode
ter dito "anotado" e o registro ter falhado. A ÚNICA prova do que existe é a
lista de itens em aberto lá em cima. Se você disse que anotou algo e esse
algo NÃO está naquela lista, então NÃO está salvo — registre agora, não
responda que já está feito.

=== SUA TAREFA ===
Interprete a ÚLTIMA mensagem NO CONTEXTO acima e devolva UM JSON.

Intenções:
- "consulta": ele quer SABER algo do que já existe ("o que tenho pra pagar essa semana?", "já paguei a Claro?", "quanto gastei?"). Responda com base nas listas acima. Se não houver nada, diga isso.
- "registro": guardar algo NOVO e concreto.
- "complemento": ele está COMPLETANDO/CORRIGINDO **o mesmo item** que já está na lista — típico de mandar em partes ("são 185 reais", "é dia 20", "na verdade é 200"). Use "atualizar" com o id certo. NÃO crie item novo.
  ATENÇÃO — "atualizar" SÓ vale quando ele fala do MESMO item. Coisa parecida NÃO é a mesma coisa:
  · "vitamina D" NÃO é "losartana" — são dois remédios, dois lembretes.
  · "conta de água" NÃO é "conta de luz".
  · "dentista" NÃO é "médico".
  Se o nome é diferente do item da lista, é ITEM NOVO, ponto. Encaixar um pedido novo num item existente APAGA o pedido original: ele fica sem o lembrete que pediu e ainda perde o antigo. É o pior erro que você pode cometer — quando estiver na dúvida, CRIE NOVO.
- "resposta": ele está RESPONDENDO uma pergunta sua. Execute o que ele pediu.
- "conclusao": ele avisa que já resolveu/pagou. Use "concluir" com o id.
- "conversa": bate-papo, desabafo, dúvida sobre você. NÃO é para registrar.

=== REGRAS QUE NÃO PODEM SER QUEBRADAS ===
0. REGISTRE PRIMEIRO, PERGUNTE DEPOIS. Se ele pediu para lembrar/anotar algo, crie o item JÁ, mesmo sem valor e sem data — lembrete sem valor continua sendo lembrete. Só depois pergunte o que falta, deixando claro que já está guardado ("Anotado. Qual o valor?"). NUNCA responda só com pergunta sem ter criado nada: é isso que faz a resposta dele virar item duplicado em vez de completar o primeiro.
0a. LEMBRETE NÃO É CONTA. Olhe o campo "tipo" de cada item:
   · tipo=despesa (boleto, fatura, IPTU, cartão) -> pode falar em "pagar", "já pagou?", "contas a pagar".
   · tipo=lembrete (esquentar o almoço, comprar frutas, ir ao terreiro, tomar remédio, pegar o cartão) -> NUNCA chame de conta, NUNCA pergunte "já pagou?", NUNCA diga "contas pendentes". Diga "lembretes" ou "o que você tem pra hoje".
   Chamar "esquentar o almoço" de conta pendente e perguntar se ele pagou é o tipo de erro que faz a pessoa achar que você não entendeu nada — e ela tem razão.
0a2. NÃO cobre nem pergunte sobre item cuja DATA AINDA NÃO CHEGOU. Se vence dia 20 e hoje é dia 3, não pergunte se já resolveu: só está guardado, não está atrasado. Cobrança antes da hora irrita.
0b. O QUE PERGUNTAR DEPENDE DA COISA. Olhe o que ele falou e responda como gente:
   · Tem dinheiro envolvido ("*pagar* o terreiro", "*pagar* a diarista", boleto, fatura, mensalidade)? Pergunte o VALOR — mesmo que não seja boleto de banco. "Pagar" é pagamento.
   · Não tem dinheiro ("esquentar o almoço", "tomar losartana", "ligar pra minha mãe", "levar o cachorro")? NUNCA pergunte valor. O que importa é dia e hora, e você já tem.
   Perguntar "qual o valor?" para "esquentar o almoço" faz você parecer burro; não perguntar o valor do terreiro te faz inútil na hora de somar os gastos.

0d. NA HORA DE COBRAR/CONFIRMAR, FALE DA COISA, não do sistema. Use o que ele escreveu:
   · "esquentar o almoço" -> "Você já almoçou? Posso dar como feito?"
   · "pagar o terreiro" -> "Conseguiu pagar o terreiro?"
   · "comprar frutas" -> "Passou no mercado? Posso tirar da lista?"
   Nunca "item concluído", "deseja marcar como pago", nem menu. É conversa, não formulário.
0c. Se você já tem tudo que precisa, NÃO pergunte nada. Confirme e pare. Pergunta só quando falta informação que muda o que você vai fazer.
1. Se a sua última mensagem terminou com PERGUNTA, a mensagem do usuário é a RESPOSTA dela. Nunca trate como assunto novo.
1b. Mas se a mensagem dele ANUNCIA um fato novo ("marquei...", "troquei...", "comprei...", "paguei..."), é assunto NOVO mesmo vindo logo depois da sua pergunta. Crie o item novo e, se ainda faltar a resposta da pergunta anterior, deixe pra lá — não force.
2. "agendar", "me avisa", "me lembra" = criar/manter lembrete PENDENTE. É o OPOSTO de concluir. Nunca marque como pago quando ele pede para agendar.
3. Nem tudo é para registrar. Sem coisa concreta para guardar, é "conversa".
4. Só use "concluir" quando ele disser claramente que JÁ resolveu/pagou.
5. Nunca invente valor ou data. Nunca escreva "sem data", "valor não informado" nem data em formato ISO (2026-08-02) para o usuário — escreva "02/08". Se falta o valor, simplesmente não mencione valor.
6. Tom cordial e curto, como gente. NUNCA menu numerado ("responda 1 ou 2").

=== COMO ESCREVER (é WhatsApp NO CELULAR, tela estreita) ===
F0. A tela é de celular. Toda linha tem que caber SEM QUEBRAR: no máximo ~35 caracteres por linha de lista. Linha que quebra vira duas linhas tortas e a lista perde o alinhamento — fica feio e difícil de ler.
F1. Uma ideia por linha. Use quebra de linha de verdade (\\n). Bloco de texto corrido ninguém lê no celular.
F2. Negrito do WhatsApp é *asterisco simples*: *R$ 420,00*, *30/09*, *IPTU*. Use em: valores, datas e nomes de contas. Nunca use ** (aparece literal e fica feio).
F3. LISTA (2 itens ou mais): uma linha curta de abertura, linha em branco, um item por linha começando com "• ".
   Formato do item, nesta ordem e SEMPRE numa linha só:
   • *Nome curto* — *R$ 000,00* · *dd/mm*
   Regras da lista:
   - o nome vai abreviado para caber: "Seguro do carro" vira *Seguro*, "Conta de luz" vira *Luz*, "Internet/Net" vira *Net*.
   - NÃO escreva a palavra "vence" dentro do item; a data sozinha já diz isso. Se precisar deixar claro, ponha na linha de abertura ("Vencendo essa semana:").
   - o que você não sabe, some do item. Nada de "não informado".
   Exemplo bom (cabe no celular):
   Vencendo essa semana:

   • *IPTU* — *R$ 420,00* · *05/08*
   • *Luz* — *R$ 187,00* · *02/08*

   Quer que eu avise um dia antes?
F4. CONFIRMAÇÃO de 1 item só: no máximo 2 linhas. Uma do que ficou guardado, outra do próximo passo. Sem lista, sem bullet.
F5. Fale do ponto de vista dele, não do seu processo interno. Diga "*Te aviso em 30/09* pra recomprar", não "preciso criar um lembrete de recompra". Ele quer saber o que já está resolvido, não o que você vai fazer.
F6. Emoji: no máximo 1, e só quando couber. Zero é melhor que dois.
F7. Nunca passe de 6 linhas no total. Se a lista for maior que 5 itens, mostre os 5 mais próximos do vencimento e feche com "e mais N".

=== ANTECIPAR (é isto que te torna útil) ===
7. CONSUMÍVEL (ração, filtro, remédio, gás, fralda, café...): quando ele disser que COMPROU um item desses, é OBRIGATÓRIO:
   a) se a duração NÃO estiver nos fatos acima: sua "reply" TEM que terminar perguntando quanto costuma durar ("Anotado. Quanto tempo costuma durar essa quantidade?"). Não responda só "registrei" — sem essa pergunta você nunca vai conseguir avisar ele de recomprar, que é o motivo de existir.
   b) quando ele responder a duração: grave em "memoria" E crie o lembrete de recompra em "item", com "data_vencimento" = HOJE + a duração que ele disse (calcule a data real, formato YYYY-MM-DD) e "recorrencia":"dias:N". Diga a data na resposta.
   c) se a duração JÁ estiver nos fatos: não pergunte nada, só crie o lembrete com a data calculada.
8. MANUTENÇÃO DE CARRO (óleo, revisão, filtro, correia): o intervalo tem DOIS gatilhos — quilometragem E tempo — e vale **o que vier primeiro**. Óleo é o caso clássico: "10 mil km ou 6 meses, o que vier primeiro".
   a) Se você não sabe o intervalo dele, pergunte UMA vez aceitando os dois: "De quanto em quanto tempo você troca? (ex.: 10 mil km ou 6 meses, o que vier primeiro)".
   b) Se ele der só km e você NÃO souber quanto ele roda por mês, pergunte UMA vez: "Quantos km você roda por mês, mais ou menos?". Guarde em "memoria" como "carro:km_por_mes".
   c) Com os dois dados, calcule as duas datas e agende na MENOR:
      · data por km = HOJE + (intervalo_km ÷ km_por_mês) meses
      · data por tempo = HOJE + os meses que ele falou
      Ex.: 10.000 km, roda 1.000 km/mês, prazo 6 meses -> por km daria 10 meses, por tempo 6 meses -> vale *6 meses*.
   d) Guarde em "memoria": "oleo:intervalo_km", "oleo:intervalo_meses", "carro:km_por_mes". Nunca pergunte de novo.
   e) Na resposta, diga a data E o motivo, curto: "Te aviso em *01/02* — 6 meses vence antes dos 10 mil km."
   f) Ao avisar, lembre que é estimativa por tempo: se ele rodar mais que o normal, o km chega antes. Vale confirmar a quilometragem na hora do aviso.
9. RECORRÊNCIA — preencher SEMPRE que ele disser que repete, senão o lembrete toca uma vez só e some:
   - "todo dia", "diariamente", "toda manhã" -> "recorrencia":"diaria"
   - "todo dia 20", "todo mês dia 5" -> "recorrencia":"mensal:20"
   - "toda segunda" -> "recorrencia":"semanal:0" (0=segunda … 6=domingo)
   - consumível/manutenção por tempo -> "recorrencia":"dias:N"
   "me lembra de tomar losartana todo dia às 7h30" = hora_alvo "07:30" E recorrencia "diaria". Sem a recorrência, ele toma o remédio um dia só.
10. Em "memoria", grave fatos duráveis e reaproveitáveis, com chave curta e estável. Ex.: {"chave":"racao gatos:dura_dias","valor":"40"}, {"chave":"aluguel:dia_vencimento","valor":"05"}. Não grave conversa fiada.

=== PERGUNTA FORA DO ESCOPO ===
11. NUNCA responda "não sei" nem "não faço isso" e pare aí. Se ele perguntar qualquer coisa — receita, remédio, dúvida do dia a dia, como funciona algo — primeiro RESPONDA de verdade, com o que você sabe, de forma curta e útil.
12. Só DEPOIS de responder, se fizer sentido, puxe a ponte pro que você faz: "quer que eu te lembre disso?", "quer que eu anote esse gasto?". Ponte, não desvio — se não couber, não force.
13. Se ele pedir algo que exige informação que você não tem (preço agora, saldo do banco, resultado de jogo), diga claramente o que não dá e ofereça o caminho mais próximo que você RESOLVE. Nada de resposta vaga ou inventada.
14. Se ele estiver em TESTE GRÁTIS, aproveite para sugerir 1 uso concreto que ele consegue sentir DENTRO dos dias que faltam — algo que dá retorno rápido (conta que vence essa semana, remédio de hoje, consulta do mês). Não sugira coisa que só faz efeito daqui a 3 meses. Uma sugestão por vez, nunca lista.

=== VÁRIAS COISAS NUMA MENSAGEM SÓ ===
15. "itens" é uma LISTA. Se ele mandar 3 contas na mesma frase, devolva as 3 — separadas por vírgula, por "e", por linha, do jeito que ele escrever. Perder um item é o pior erro possível: ele confiou em você e vai descobrir no vencimento.
16. Em "descricao" ponha só o nome da coisa, limpo: "luz", "net", "seguro do carro". Nunca inclua o comando dele ("anota aí", "me lembra de") nem valor/data dentro da descrição.
17. "dia 12" sem mês = o próximo dia 12 a partir de hoje. Em "data_vencimento" sempre YYYY-MM-DD; no texto da resposta sempre dd/mm. Nunca escreva "vence dia 12" para o usuário — escreva "vence *12/08*".
18. TUDO que você escrever na resposta TEM que estar nos campos do JSON. Se você escreveu "R$ 187,00", então "valor_reais": 187.0 naquele item. Escrever o valor só no texto e deixar o campo null é perder a informação: o painel, os avisos e a soma de gastos ficam vazios.
19. NUNCA pergunte algo que você acabou de responder. Se você listou os valores, não pergunte "qual o valor?". Antes de mandar, releia sua própria "reply": a pergunta do final só entra se ela pedir algo que REALMENTE falta.

Formato (SOMENTE o JSON):
{"intent":"consulta|registro|complemento|resposta|conclusao|conversa",
  "reply":"<mensagem ao usuário>",
  "itens": [] | [{"tipo":"lembrete|despesa","descricao":"...","valor_reais":null,"data_vencimento":"YYYY-MM-DD"|null,"hora_alvo":"HH:MM"|null,"recorrencia":null|"mensal:DIA"|"dias:N"}],
  "atualizar": null | {"id":<id>,"campos":{"valor_reais":0,"data_vencimento":"YYYY-MM-DD","hora_alvo":"HH:MM","descricao":"...","recorrencia":"..."}},
  "concluir": null | <id>,
  "memoria": [] | [{"chave":"...","valor":"..."}]}"""


# Intenções em que a regra clássica é confiável E baratíssima (não gasta LLM).
_CLASSICO_CONFIAVEL = {"saudacao", "agradecimento", "capacidades"}


def _data_passada(data_iso: str) -> bool:
    try:
        a, m, d = (int(x) for x in str(data_iso).split("-"))
        import datetime
        return datetime.date(a, m, d) < tempo.hoje()
    except Exception:
        return False


def _br(data_iso: str) -> str:
    """2026-08-02 -> 02/08. Ano só aparece quando NÃO é o ano corrente.

    Teste ao vivo 03/08/2026: o usuário pediu troca de óleo "em 6 meses" e o
    bot confirmou "Te aviso em 02/02". Fevereiro de quando? A data no banco
    era 2027-02-02, certa — a mensagem é que era ambígua. Data futura sem ano
    faz o usuário achar que o lembrete é pra daqui a duas semanas, e o valor
    inteiro do produto está em ele confiar na data que eu digo.
    """
    try:
        a, m, d = str(data_iso).split("-")
        if int(a) != tempo.hoje().year:
            return f"{d}/{m}/{a}"
        return f"{d}/{m}"
    except Exception:
        return str(data_iso)


def _fmt_itens(itens: list) -> str:
    if not itens:
        return "(nada anotado no momento)"
    linhas = []
    for it in itens:
        # o tipo PRECISA aparecer: sem ele o mordomo chamou "esquentar o
        # almoço" de conta pendente e perguntou se o usuário já tinha pago.
        partes = [f"id={it.get('id')}",
                  f"tipo={it.get('tipo') or 'lembrete'}",
                  str(it.get("descricao") or "?")]
        # valor ausente: omitir. Se escrevermos "valor não informado" aqui, o
        # LLM repete isso para o usuário — foi o que aconteceu no teste.
        if it.get("valor_reais") is not None:
            partes.append(("R$ %.2f" % it["valor_reais"]).replace(".", ","))
        if it.get("data_vencimento"):
            partes.append(f"vence {_br(it['data_vencimento'])}")
        if it.get("hora_alvo"):
            partes.append(f"às {it['hora_alvo']}")
        if it.get("recorrencia"):
            partes.append(f"repete {it['recorrencia']}")
        if it.get("status"):
            partes.append(str(it["status"]))
        linhas.append(" | ".join(partes))
    return "\n".join(linhas)


def _fmt_fatos(fatos: list) -> str:
    if not fatos:
        return "(ainda não sei nada específico sobre ele)"
    return "\n".join(f"- {f.get('chave')}: {f.get('valor')}" for f in fatos)


def _fmt_historico(msgs: list) -> str:
    """SÓ o que o USUÁRIO falou.

    Antes as respostas do próprio bot entravam aqui, e ele passou a tratar a
    própria fala como estado: lia "já anotei a vitamina D" que ele mesmo
    tinha dito e concluía que estava salvo — mesmo com o banco vazio. Foi a
    causa raiz das contas fantasma, do nome errado na lista e do item que
    nunca era criado. A fala do assistente agora sai daqui; só a ÚLTIMA vai,
    separada, e apenas para saber se há pergunta em aberto.
    """
    ditas = [m for m in msgs if m.get("direcao") == "in"]
    if not ditas:
        return "(ele ainda não falou nada nesta conversa)"
    linhas = []
    for m in ditas[-8:]:
        txt = (m.get("preview") or "").strip() or f"[{m.get('tipo') or 'mídia'}]"
        linhas.append(f"- {txt}")
    return "\n".join(linhas)


def _ultima_fala_bot(msgs: list) -> str:
    for m in reversed(msgs):
        if m.get("direcao") == "out":
            return (m.get("preview") or "").strip()
    return ""


def _tem_pergunta_aberta(msgs: list) -> bool:
    """A última coisa que o BOT disse foi pergunta? Se sim, a mensagem do
    usuário é resposta — e o fluxo clássico não pode sequestrar ('feito')."""
    for m in reversed(msgs):
        if m.get("direcao") == "out":
            return "?" in (m.get("preview") or "")
    return False


ULTIMA_FALHA: str = ""


def _registrar_falha(motivo: str) -> None:
    """Guarda o último motivo de o mordomo ter desistido.

    Sem isto, quando o v8 devolve None a mensagem cai no fluxo antigo e o
    usuário vê uma resposta pior — sem nenhum rastro do porquê. Exposto em
    /health para diagnóstico em 1 request.
    """
    global ULTIMA_FALHA
    ULTIMA_FALHA = motivo[:600]
    try:
        import logging
        logging.getLogger("resolveai").warning("[v8] %s", ULTIMA_FALHA)
    except Exception:
        pass


def _situacao(situacao: str) -> str:
    """Texto curto sobre a conta, pra o mordomo calibrar a sugestão.
    Em trial ele deve propor um uso que dá retorno DENTRO dos dias que faltam."""
    return situacao or "assinante ativo"


def _llm(text, nome, itens, fatos, historico, ai_engine, situacao="",
         correcao="") -> Optional[dict]:
    try:
        from litellm import completion
    except Exception:
        return None
    try:
        system = (_V8_SYSTEM
                  .replace("{today}", tempo.hoje().isoformat())
                  .replace("{now}", tempo.agora().strftime("%H:%M"))
                  .replace("{nome}", nome or "usuário")
                  .replace("{itens}", _fmt_itens(itens))
                  .replace("{fatos}", _fmt_fatos(fatos))
                  .replace("{historico}", _fmt_historico(historico))
                  .replace("{ultima_sua}",
                           _ultima_fala_bot(historico) or "(você ainda não respondeu nada)")
                  .replace("{situacao}", _situacao(situacao)))
        for tentativa in range(2):
            resp = completion(
                model=getattr(ai_engine, "LLM_MODEL", "gpt-4o-mini"),
                # 700 era pouco: com resposta formatada + lista de itens o
                # JSON truncava, o parse falhava e o v8 devolvia None em
                # silêncio — a mensagem caía no regex antigo, que perde item.
                max_tokens=1600,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",
                     "content": f"Última mensagem do usuário: {text!r}"},
                ] + ([{"role": "user", "content": correcao}] if correcao else [])
                  + ([{"role": "user",
                       "content": "Responda SOMENTE o JSON pedido."}]
                     if tentativa else []),
            )
            bruto = resp.choices[0].message.content
            bruto = re.sub(r"```(?:json)?|```", "", bruto).strip()
            try:
                # strict=False aceita quebra de linha literal dentro das
                # strings. O prompt agora manda formatar a resposta com \n;
                # quando o modelo escreve a quebra crua, o parser estrito
                # estoura e o mordomo cai calado no regex antigo.
                data = json.loads(bruto, strict=False)
            except json.JSONDecodeError as e:
                # O modelo às vezes imita chave dupla ({{"intent"...) — foi
                # o que derrubou o v8 o dia inteiro, porque o schema do
                # prompt tinha {{ }} sobrando de um .format() antigo. O
                # prompt já foi corrigido; isto aqui é o cinto de segurança.
                remendo = bruto.replace("{{", "{").replace("}}", "}")
                try:
                    data = json.loads(remendo, strict=False)
                    _registrar_falha("json veio com chave dupla — remendado")
                except json.JSONDecodeError:
                    _registrar_falha(f"json invalido ({e}) :: {bruto[:400]}")
                    continue
            if data.get("reply") and data.get("intent"):
                return data
            _registrar_falha(f"json sem reply/intent :: {bruto[:400]}")
        return None
    except Exception as e:
        _registrar_falha(f"excecao no LLM: {e!r}")
        return None


def _preparar_item(novo: dict, ai_engine, texto_origem: str = "",
                   n_itens: int = 1) -> Optional[dict]:
    """Normaliza um item vindo do LLM para o formato que o banco aceita.

    Um único lugar faz isso: quando havia duas cópias dessa lógica, uma delas
    ficou para trás e o item preparado na reconsulta nunca chegava no banco.
    """
    if not (isinstance(novo, dict) and novo.get("descricao")):
        return None
    # COERÇÃO, não setdefault: o LLM inventa tipo ("medicamento") e o
    # db.add_item LANÇA ValueError — o item sumia em silêncio.
    if novo.get("tipo") not in ("lembrete", "despesa", "documento"):
        novo["tipo"] = ("despesa" if novo.get("valor_reais") is not None
                        else "lembrete")
    if novo.get("status") not in ("pendente", "concluido", "aglutinado",
                                  "vencido"):
        novo["status"] = "pendente"
    # CATEGORIA: função, não prompt.
    # O v8 confiava no LLM devolver `categoria` no JSON. Ele quase nunca
    # devolvia, e este `if` simplesmente carimbava "Outros". Resultado medido
    # em produção (03/08/2026): TODOS os itens do banco em "Outros" — inclusive
    # "Cartão de débito", que a `classify_category` do ai_engine acerta em
    # cheio, porque "cartao" está na lista de Contas desde sempre. A função
    # certa existia e ninguém chamava neste caminho.
    # Consequência: o painel de gastos por categoria vira uma barra só, e o
    # resumo semanal não consegue separar conta de recado.
    _CATS = ("Alimentação", "Pet", "Veículo", "Contas", "Saúde", "Casa",
             "Lazer", "Outros")
    cat = novo.get("categoria")
    if cat not in _CATS or cat == "Outros":
        # A DESCRIÇÃO MANDA. O texto original só entra como último recurso.
        #
        # Na v16.3 eu classificava "descrição + frase inteira" de uma vez. Teste
        # ao vivo em 03/08: "comprar ração da Nina sexta E trocar o óleo do
        # carro" gerou dois itens, e AMBOS receberam a frase inteira como
        # contexto — "ração" (Pet) venceu "óleo/carro" (Veículo) e a troca de
        # óleo foi parar em Pet. Um item não pode herdar o sinal do vizinho.
        desc = novo.get("descricao", "")
        adivinhado = ai_engine.classify_category(desc)
        if adivinhado == "Outros" and texto_origem:
            # descrição muda ("Cartão de débito" sem contexto) — aí sim olha a
            # frase, mas só se ela não estiver falando de várias coisas.
            # CATEGORIA é mais frágil que data: numa frase com "e" ligando
            # duas ações ("marca o veterinário E paga a luz"), a palavra-chave
            # pode ser de qualquer metade — chutar é 50/50. Aqui eu prefiro
            # "Outros" a errar. Data e hora não têm esse problema: expressão
            # de tempo numa frase de um item só pertence àquele item.
            if n_itens == 1 and not re.search(r"\be\b", texto_origem):
                adivinhado = ai_engine.classify_category(texto_origem)
        novo["categoria"] = adivinhado
    novo.setdefault("hora_alvo", None)
    novo.setdefault("valor_reais", None)
    novo.setdefault("data_vencimento", None)
    novo.setdefault("recorrencia", None)

    # DATA: QUANDO EU SEI CALCULAR, EU MANDO — mesmo contra o LLM.
    #
    # Caso real (03/08/2026, uma SEGUNDA): "me lembra sexta de ligar pra minha
    # mãe". Sexta era 07/08. O LLM devolveu 05/08 — uma QUARTA. O item foi pro
    # banco com a data errada e a confirmação repetiu a data errada com toda a
    # confiança do mundo. O usuário perde a ligação e a culpa é do produto.
    #
    # Modelo de linguagem é ruim em aritmética de calendário e não tem como
    # saber que hoje é segunda a não ser pelo que eu escrevo no prompt.
    # `extract_due_date` é determinística, testada e nunca erra dia da semana.
    # Então: se o texto tem uma expressão de data que eu SEI resolver
    # ("sexta", "amanhã", "em 8 meses", "dia 15"), a minha conta ganha.
    # O LLM continua dono da INTERPRETAÇÃO (o que é o item); a data é minha.
    #
    # Só olha a frase inteira quando ela trata de UMA coisa só; em frase
    # composta a data do vizinho não pode vazar pra cá.
    if texto_origem or novo.get("descricao"):
        calculada = ai_engine.extract_due_date(novo.get("descricao") or "")
        if not calculada and texto_origem and n_itens == 1:
            calculada = ai_engine.extract_due_date(texto_origem)
        atual = novo.get("data_vencimento")
        if calculada and not atual:
            novo["data_vencimento"] = calculada
            _registrar_falha("data relativa calculada em Python — o LLM "
                             "devolveu o item sem data")
        elif calculada and atual and calculada != atual:
            novo["data_vencimento"] = calculada
            _registrar_falha(
                f"data relativa calculada em Python: o LLM disse {atual} e a "
                f"conta dá {calculada} — vale a minha")

    # HORA: MESMA REGRA. E ESTA FALTAVA.
    #
    # Caso real (03/08, 15:16): "Preciso marcar médico, me lembra daqui 10min".
    # O bot respondeu "Anotado. Vou te lembrar em 10 minutos." e gravou
    # #76 "marcar médico" com data=NULL e hora=NULL. Ou seja: PROMETEU E NÃO
    # VAI TOCAR NUNCA. É o pecado capital deste produto — "anotado" sem gravar.
    #
    # Eu tinha posto o cálculo de DATA em Python e esqueci a HORA. O
    # `extract_due_time` já sabia resolver "daqui 10min" desde sempre; ninguém
    # o chamava neste caminho.
    if not novo.get("hora_alvo") and (texto_origem or novo.get("descricao")):
        h = ai_engine.extract_due_time(novo.get("descricao") or "")
        if not h and texto_origem and n_itens == 1:
            h = ai_engine.extract_due_time(texto_origem)
        if h:
            novo["hora_alvo"] = h
            _registrar_falha("hora calculada em Python — o LLM devolveu o "
                             "item sem hora")

    # HORA SEM DATA = HOJE (ou amanhã, se a hora já passou).
    # Item com hora e sem data não entra no alarme com segurança: o disparo
    # depende de `data_vencimento = hoje`. Sem data, o lembrete fica órfão.
    if novo.get("hora_alvo") and not novo.get("data_vencimento"):
        try:
            hh, mm = str(novo["hora_alvo"]).split(":")[:2]
            agora = tempo.agora()
            alvo = agora.replace(hour=int(hh), minute=int(mm),
                                 second=0, microsecond=0)
            dia = agora.date() if alvo >= agora else (
                agora + __import__("datetime").timedelta(days=1)).date()
            novo["data_vencimento"] = dia.isoformat()
            _registrar_falha("item tinha hora sem data — ancorei em "
                             f"{dia.isoformat()} pra o alarme poder tocar")
        except Exception:
            pass
    novo.setdefault("link_afiliado",
                    ai_engine.affiliate_link_for(novo.get("descricao", "")))
    # data no passado dispara o alarme na hora e assusta o usuário
    if novo.get("data_vencimento") and _data_passada(novo["data_vencimento"]):
        _registrar_falha(f"data no passado ({novo['data_vencimento']}) em "
                         f"'{novo.get('descricao')}' — descartada")
        novo["data_vencimento"] = None

    # (3) HORA QUE JÁ PASSOU HOJE começa AMANHÃ. Sem isto, "me lembra de
    # tomar vitamina D todo dia às 9h" pedido às 18h tocava na mesma hora —
    # o usuário leva um susto e acha que o bot é doido.
    if novo.get("hora_alvo") and not novo.get("data_vencimento"):
        try:
            import datetime
            h, mi = (int(x) for x in str(novo["hora_alvo"]).split(":")[:2])
            agora = tempo.agora()
            if (h, mi) <= (agora.hour, agora.minute):
                novo["data_vencimento"] = (tempo.hoje() +
                                           datetime.timedelta(days=1)).isoformat()
            else:
                novo["data_vencimento"] = tempo.hoje().isoformat()
        except Exception:
            pass
    return novo


def _pos_processar(result: dict, text: str, fatos: list, itens: list) -> None:
    """Passada ÚNICA sobre os itens criados, venha de onde vier.

    Havia dois caminhos de criação (o normal e o da reconsulta) e a regra de
    manutenção só existia num deles: o item da troca de óleo nasceu pela
    reconsulta e saiu sem data. Regra que mora em um só caminho é regra que
    não vale — agora tudo passa por aqui, no fim.
    """
    if not result.get("items"):
        return
    manut = _extrair_manutencao(text, fatos)
    finais = []
    for pronto in result["items"]:
        # MANUTENÇÃO: a data vem de conta em Python, nunca do modelo.
        if _e_manutencao(pronto.get("descricao", "")) and not pronto.get("data_vencimento"):
            calc = _data_dois_gatilhos(manut)
            if calc:
                pronto["data_vencimento"], motivo = calc
                if manut.get("meses"):
                    pronto["recorrencia"] = "dias:%d" % int(manut["meses"] * 30.44)
                result["reply"] = (
                    f"Anotado a troca de hoje.\n\n"
                    f"*Te aviso em {_br(pronto['data_vencimento'])}* — {motivo}.\n\n"
                    f"Se você rodar mais que o normal, me avisa que eu antecipo.")
        # DEDUPE: item praticamente igual já aberto vira atualização.
        gemeo = _ja_existe(pronto.get("descricao", ""), itens)
        if gemeo:
            campos = {k: v for k, v in pronto.items()
                      if k in ("valor_reais", "data_vencimento", "hora_alvo",
                               "recorrencia") and v is not None}
            if campos and not result.get("atualizar"):
                result["atualizar"] = {"id": gemeo["id"], "campos": campos}
            _registrar_falha(f"'{pronto.get('descricao')}' ja existia como "
                             f"'{gemeo.get('descricao')}' — atualizei em vez de duplicar")
            continue
        finais.append(pronto)
    result["items"] = finais


def route(user_id, user_name, text, db, ai_engine, telefone: str = "",
          situacao: str = "") -> Optional[dict]:
    """Ponto de entrada do V8. Ver contrato no topo do arquivo."""
    text = (text or "").strip()
    if not text:
        return None

    try:
        historico = db.conversa_recente(telefone, limite=10) if telefone else []
    except Exception:
        historico = []
    try:
        itens = db.itens_abertos(user_id, limite=20)
    except Exception:
        itens = []
    try:
        fatos = db.fatos(user_id, limite=40)
    except Exception:
        fatos = []

    # ANTES de tudo: pergunta sobre fato do mundo que muda (placar, campeão,
    # eleição, cotação, notícia) não passa pelo modelo. Ele tem data de corte
    # e responde com convicção coisa errada — foi assim que disse que a
    # última Copa foi 2022 da França e que a de 2026 "ainda não aconteceu".
    # Errar o básico faz o usuário duvidar de TODO o resto que a gente diz.
    if _pergunta_fato_do_mundo(text):
        buscado = _responder_com_busca(text, user_name)
        if buscado:
            return {"reply": buscado, "items": [], "needs_decision": False,
                    "mode": "v8_busca_web"}
        # busca fora do ar: assume que não sabe. Nunca chuta.
        return _resposta_nao_sei_do_mundo(user_name)

    pergunta_aberta = _tem_pergunta_aberta(historico)
    classic_intent = ai_engine.detect_intent(text)

    # Saudação/agradecimento: a regra resolve e não gasta LLM. MAS há duas
    # exceções em que ela NÃO pode passar direto:
    #  - pergunta aberta: o que vem depois de uma pergunta é a resposta dela;
    #  - o usuário perguntou algo: pergunta sempre merece resposta de verdade,
    #    mesmo fora do escopo. O fluxo clássico devolveria genérico.
    if (not pergunta_aberta and not _e_pergunta(text)
            and classic_intent in _CLASSICO_CONFIAVEL):
        return None

    data = _llm(text, user_name, itens, fatos, historico, ai_engine, situacao)

    # O pedido dele NUNCA pode virar alteração de outro pedido. Se o modelo
    # mandou "atualizar" um item que a mensagem nem cita, devolvemos a
    # pergunta com a correção explícita — em vez de aceitar e destruir dois
    # lembretes de uma vez (o novo, que nunca nasce, e o antigo, que muda).
    if isinstance(data, dict) and isinstance(data.get("atualizar"), dict):
        alvo_id = data["atualizar"].get("id")
        alvo = next((i for i in itens if i.get("id") == alvo_id), None)
        if alvo and not _atualizacao_plausivel(text, alvo, pergunta_aberta):
            _registrar_falha(
                f"LLM tentou alterar '{alvo.get('descricao')}' com uma "
                f"mensagem que fala de outra coisa — reconsultando")
            corrigido = _llm(
                text, user_name, itens, fatos, historico, ai_engine, situacao,
                correcao=(f"CORREÇÃO OBRIGATÓRIA: a mensagem NÃO fala do item "
                          f"'{alvo.get('descricao')}'. É coisa DIFERENTE. "
                          f"Proibido usar \"atualizar\" nessa resposta: "
                          f"devolva o pedido em \"itens\" como item NOVO, "
                          f"preservando o item existente intacto."))
            if isinstance(corrigido, dict):
                corrigido["atualizar"] = None
                data = corrigido
            else:
                data["atualizar"] = None

    if data is None:
        # LLM indisponível/falhou. O fluxo clássico quebra a frase por
        # vírgula: "luz 187 dia 12, net 129 dia 15 E o seguro 340 dia 20"
        # vira 2 itens e o terceiro some calado. Perder conta do usuário é
        # inaceitável, então aqui a gente prefere admitir e devolver a bola.
        if _multi_item(text):
            import logging
            logging.getLogger("resolveai").warning(
                "[v8] LLM indisponivel em mensagem multi-item — nao deixei "
                "cair no regex (risco de perder item)")
            return {"reply": ("Recebi, mas vieram várias coisas juntas e eu "
                              "não quero registrar errado.\n\n"
                              "Me manda *uma por linha* que eu anoto todas "
                              "certinho."),
                    "items": [], "needs_decision": False, "mode": "v8_multi_seguro"}
        if classic_intent == "vago" or _e_pergunta(text) or _parece_conversa(text):
            return _mordomo_fallback(user_name)
        return None

    intent = data.get("intent")
    result = ai_engine._base_result(mode="v8")
    result["reply"] = (data.get("reply") or "").strip()

    ids_validos = {it.get("id") for it in itens}

    # CONSULTA é resposta sobre dinheiro do usuário: confere contra o banco
    # antes de sair. Só vale para consulta pura — em registro os valores da
    # resposta são justamente os que estão sendo criados agora.
    if intent == "consulta":
        substituto = _consulta_confere(result["reply"], itens)
        if substituto:
            _registrar_falha("consulta citou conta inexistente (lida do "
                             "historico) — resposta trocada pelo real")
            result["reply"] = substituto
            return result
        # valores conferem, mas o NOME pode ter vindo do histórico:
        # a lista é sempre reconstruída a partir do banco.
        result["reply"] = _reescrever_consulta(result["reply"], itens)

    # fatos aprendidos (perguntar só uma vez)
    memoria = data.get("memoria")
    if isinstance(memoria, list):
        guardar = [m for m in memoria
                   if isinstance(m, dict) and m.get("chave") and m.get("valor")]
        if guardar:
            result["memoria"] = guardar[:5]

    # completa/corrige item existente (informação mandada em partes)
    atualizar = data.get("atualizar")
    if isinstance(atualizar, dict) and atualizar.get("id") in ids_validos:
        campos = atualizar.get("campos")
        if isinstance(campos, dict) and campos:
            result["atualizar"] = {"id": atualizar["id"], "campos": campos}

    # baixa em item existente
    if data.get("concluir") in ids_validos:
        result["concluir"] = data["concluir"]

    # SINÔNIMOS DE "FEITO" E DE "ADIAR", EM PYTHON.
    # O usuário não fala o comando do manual. Ele diz "resolvi", "já fiz",
    # "paguei", "deixa pra amanhã". Isso estava só no prompt e oscilava: às
    # vezes o LLM dava baixa, às vezes criava um item novo "resolvi" — e o
    # lembrete original continuava vivo, cobrando o cara de uma coisa que ele
    # já tinha feito. Aqui a leitura é determinística e ganha do modelo.
    if not result.get("concluir") and _e_conclusao_explicita(text):
        ult = None
        try:
            ult = db.ultimo_item(user_id)
        except Exception:
            ult = None
        if ult and ult.get("id") in ids_validos and ult.get("status") == "pendente":
            result["concluir"] = ult["id"]
            _registrar_falha(f"'{text.strip()[:30]}' lido como CONCLUSÃO em "
                             f"Python — dei baixa no #{ult['id']}")
            data["itens"] = []          # não cria item novo de "resolvi"
            data.pop("item", None)

    # aceita "itens" (lista, formato atual) e "item" (singular, tolerância a
    # LLM que ignora o schema). Normaliza tudo para lista.
    novos = data.get("itens")
    if not isinstance(novos, list):
        novos = []
    if isinstance(data.get("item"), dict):
        novos.append(data["item"])
    item = novos[0] if len(novos) == 1 else None

    # REDE DE SEGURANÇA (o prompt sozinho não garante):
    # o bot perguntou algo, o usuário respondeu um fragmento curto ("são 340
    # reais", "dia 20") e o LLM devolveu item NOVO em vez de completar o que
    # já existe. Fragmento curto depois de pergunta é resposta, não assunto
    # novo — então convertemos em atualização do item mais recente.
    # EXCEÇÃO: se o LLM devolveu "memoria", ele está no fluxo de aprender
    # (perguntei quanto dura -> ele respondeu -> agora crio o lembrete de
    # recompra). Aí o item NOVO é legítimo e a rede não pode sequestrá-lo.
    # Sem esta exceção, "uns 2 meses" (resposta sobre água sanitária) foi
    # colada no item da ração — erro real observado em produção.
    if (pergunta_aberta and isinstance(item, dict) and itens
            and not result.get("atualizar") and not result.get("concluir")
            and not result.get("memoria")
            and len(text.split()) <= 4
            and not _anuncia_assunto_novo(text)):
        campos = {k: item.get(k) for k in
                  ("valor_reais", "data_vencimento", "hora_alvo", "recorrencia")
                  if item.get(k) is not None}
        if campos and item is not None:
            # o alvo é o item MAIS RECENTE (o assunto da pergunta), não o de
            # vencimento mais próximo — por isso ultimo_item, não itens[-1].
            try:
                ult = db.ultimo_item(user_id) or {}
            except Exception:
                ult = {}
            alvo = ult.get("id") if ult.get("id") in ids_validos else None
            if alvo:
                result["atualizar"] = {"id": alvo, "campos": campos}
                item, novos = None, []   # não cria duplicado

    if (intent in ("registro", "resposta") and novos
            and not result.get("atualizar")):
        for novo in novos[:10]:
            pronto = _preparar_item(novo, ai_engine, texto_origem=text,
                                    n_itens=len(novos[:10]))
            if pronto:
                result["items"].append(pronto)

        n = _resgatar_valores(result["items"], result["reply"])
        if n:
            _registrar_falha(f"resgatei {n} valor(es) que o LLM deixou de fora "
                             f"do campo (estavam so no texto)")
        result["reply"] = _polir_resposta(
            result["reply"], result["items"], _ultima_fala_bot(historico),
            text, _assunto_da_vez(result, itens), itens)
        _pos_processar(result, text, fatos, itens)

    # INVARIANTE: prometeu guardar -> tem que ter guardado alguma coisa.
    # O modelo lia no histórico que já havia dito "vou te lembrar da vitamina
    # D" e concluía que estava feito — respondia bonito e não criava nada.
    # Uma reconsulta com a correção explícita resolve; se ainda assim não
    # vier item, a gente avisa em vez de mentir.
    if (_promete_guardar(result["reply"]) and not result["items"]
            and not result.get("atualizar") and not result.get("concluir")
            and intent not in ("consulta", "conversa")):
        _registrar_falha("prometeu guardar sem criar item — reconsultando")
        corrigido = _llm(
            text, user_name, itens, fatos, historico, ai_engine, situacao,
            correcao=("CORREÇÃO OBRIGATÓRIA: você prometeu guardar mas não "
                      "devolveu nada em \"itens\". O que está na CONVERSA "
                      "RECENTE não está salvo — só vale o que estiver na "
                      "lista de itens em aberto. Devolva o pedido dele em "
                      "\"itens\" agora, como item NOVO."))
        if isinstance(corrigido, dict) and isinstance(corrigido.get("itens"), list) \
                and corrigido["itens"]:
            _n = len(corrigido["itens"][:10])   # a lista DESTE ramo, não `novos`
            for novo in corrigido["itens"][:10]:
                pronto = _preparar_item(novo, ai_engine, texto_origem=text,
                                        n_itens=_n)
                if pronto:
                    result["items"].append(pronto)
            if corrigido.get("reply"):
                result["reply"] = corrigido["reply"]
            intent = "registro"
            # o item que nasce aqui passa pelas MESMAS regras do caminho
            # normal — foi por não passar que a troca de óleo saiu sem data.
            #
            # v16.8: o comentário acima já dizia isso, mas só o
            # _pos_processar era chamado. A resposta continuava crua.
            # Teste ao vivo 11:04: "me lembra do pediatra em 8 meses" gravou
            # #67 com data 03/04/2027 (certo) e respondeu "Anotado. Qual a
            # data do pediatra?" — a mesma pergunta que o corte de pergunta
            # redundante mata em 1 linha, só que ele nunca rodava neste ramo.
            # Agora a faxina mora numa função só e os DOIS caminhos chamam.
            result["reply"] = _polir_resposta(
                result["reply"], result["items"], _ultima_fala_bot(historico),
                text, _assunto_da_vez(result, itens), itens)
            _pos_processar(result, text, fatos, itens)
        else:
            _registrar_falha("reconsulta tambem nao devolveu item")
            result["reply"] = ("Não consegui guardar isso direito aqui. 😕\n\n"
                               "Me manda de novo em uma frase, tipo "
                               "_\"vitamina D todo dia às 9h\"_?")
            return result


    return result


_INTERROGATIVAS = ("qual", "quais", "quando", "quanto", "quantos", "quanta",
                   "como", "onde", "por que", "porque", "pq", "o que", "oq",
                   "quem", "sera que", "será que", "da pra", "dá pra",
                   "consigo", "voce sabe", "você sabe", "vc sabe",
                   "me explica", "explica", "pode me dizer", "ja paguei",
                   "já paguei", "tenho que", "preciso")


# ---------------------------------------------------------------------------
# ENCHIMENTO DE LINGUIÇA
# ---------------------------------------------------------------------------
# Medido no WhatsApp em 03/08: depois de mandar o resumo semanal, o bot emendou
# "Precisando de ajuda com algo?" e, na mensagem seguinte, "Concluído. Posso
# ajudar com mais alguma coisa?". Nenhuma das duas carrega informação.
#
# No WhatsApp isso não é educação, é NOTIFICAÇÃO. Cada frase vazia vibra o
# celular da pessoa por nada, e é assim que o usuário arquiva a conversa. O
# modelo escreve isso porque foi treinado em atendimento; nenhum prompt tira,
# porque ele acha que está sendo gentil. Some por função.
_ENCHIMENTO = (
    r"precisando de ajuda com (?:algo|alguma coisa)",
    r"posso (?:te )?ajudar (?:com|em) mais (?:alguma coisa|algo)",
    r"posso (?:te )?ajudar em algo mais",
    r"(?:tem )?mais alguma coisa\??",
    r"em que (?:mais )?posso (?:te )?ajudar",
    r"como posso (?:te )?ajudar (?:hoje)?",
    r"estou (?:aqui )?(?:à|a) (?:sua )?disposi[çc][ãa]o",
    r"fico (?:à|a) disposi[çc][ãa]o",
    r"qualquer coisa (?:é )?s[óo] (?:me )?chamar",
    r"espero ter ajudado",
    r"precisa de mais alguma coisa",
)
_RE_ENCHIMENTO = re.compile(
    r"(?im)^\s*[*_]{0,2}(?:" + "|".join(_ENCHIMENTO) + r")[*_]{0,2}\s*[.!?]*\s*$")
# a mesma frase colada no fim de um parágrafo
_RE_ENCHIMENTO_FIM = re.compile(
    r"(?i)(?:^|(?<=[.!?…]))\s*[*_]{0,2}(?:" + "|".join(_ENCHIMENTO)
    + r")[*_]{0,2}\s*[.!?]*\s*$")


def tirar_enchimento(reply: str) -> str:
    """Corta frase de atendimento que não carrega informação nenhuma."""
    if not reply:
        return reply
    t = _RE_ENCHIMENTO.sub("", reply)
    t = _RE_ENCHIMENTO_FIM.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    # se sobrou só pontuação/emoji solto, devolve o original (nunca some com
    # a resposta inteira por causa de uma regra de faxina)
    return t if re.search(r"[A-Za-zÀ-ÿ0-9]", t) else reply


_RE_HOJE = re.compile(r"(?i)\b(?:de\s+)?hoje\b")


def _corrigir_hoje_falso(reply: str, items: list) -> str:
    """Tira o "hoje" quando NENHUM item salvo é de hoje.

    Caso real 03/08: "trocar o óleo do carro em 5000 km ou 6 meses" virou
    "*Anotado a troca de hoje.*" — o usuário nunca disse que trocou hoje, e o
    item ficou gravado para 02/02/2027. Um mordomo que confirma um fato que
    você não falou é um mordomo em quem você para de confiar, mesmo quando
    ele acerta o resto.
    """
    if not reply or not items or not _RE_HOJE.search(reply):
        return reply
    hoje = tempo.hoje().isoformat()
    if any((it.get("data_vencimento") or "") == hoje for it in items):
        return reply          # tem item de hoje: o "hoje" é legítimo
    _registrar_falha("resposta dizia 'hoje' sem nenhum item de hoje — tirei")
    t = _RE_HOJE.sub("", reply)
    # [ \t] e NÃO \s: \s engole \n e amassava a resposta inteira numa linha só.
    # Com tudo numa linha, a faxina de enchimento (que é ancorada por linha)
    # deixava de achar "Precisando de ajuda com algo?" — um remendo quebrando
    # o outro, em silêncio.
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"[ \t]+([.,!?])", r"\1", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return t.strip()


def _completar_ano_nas_datas(reply: str, items: list) -> str:
    """"Te aviso em 02/02" -> "Te aviso em 02/02/2027".

    O `_br` já devolve o ano nas linhas que EU monto. Só que esta frase quem
    escreveu foi o modelo, em texto livre, e nenhuma formatação minha passa
    por ali. Então eu comparo o dd/mm que ele escreveu com a data que está
    no item salvo: se o ano for outro, eu completo. Não invento data — só
    desambiguo a que já existe no banco.
    """
    if not reply or not items:
        return reply
    ano_atual = tempo.hoje().year
    alvos = {}
    for it in items:
        iso = it.get("data_vencimento") or ""
        try:
            a, m, d = iso.split("-")
        except ValueError:
            continue
        if int(a) != ano_atual:
            alvos[f"{d}/{m}"] = a
    if not alvos:
        return reply

    def _troca(m):
        ddmm = f"{int(m.group(1)):02d}/{int(m.group(2)):02d}"
        ano = alvos.get(ddmm)
        return f"{ddmm}/{ano}" if ano else m.group(0)

    # dd/mm que ainda NÃO tem ano colado
    return re.sub(r"\b(\d{1,2})/(\d{1,2})\b(?!/\d)", _troca, reply)


def _confirmacao_seca(items: list) -> str:
    """Confirmação mínima e honesta: o quê, quando. Sem pergunta nenhuma."""
    if not items:
        return "Anotado. ✅"
    if len(items) == 1:
        return f"Anotado ✅ — {_item_linha(items[0])}"
    linhas = "\n".join(f"• {_item_linha(i)}" for i in items[:5])
    return f"Anotado ✅ — {len(items)} itens:\n{linhas}"


def _mesma_pergunta(a: str, b: str) -> bool:
    """As duas mensagens são, na prática, a mesma pergunta?"""
    def _norm(s):
        s = _sem_acento(s or "").lower()
        return re.sub(r"[^a-z0-9 ]", "", s).strip()
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or (len(na) > 12 and (na in nb or nb in na))


def _quebrar_loop(reply: str, items: list, ultima_bot: str) -> str:
    """Nunca faça DUAS VEZES a mesma pergunta.

    Caso real (03/08, 14:08→14:09):
        bot:    "Anotado. Qual o valor da encomenda?"
        Kevin:  "Nao tem valor, é um lembrete apenas"
        bot:    "Anotado. Qual o valor da encomenda?"   <- idêntico

    O item já estava salvo e certo no banco. Ainda assim o bot repetiu, e do
    lado de lá isso não parece um bug pontual — parece que o produto não
    escuta. Uma pergunta repetida destrói mais confiança do que uma resposta
    errada, porque a pessoa conclui que ninguém está lendo o que ela escreve.
    Se eu ia repetir, eu paro e confirmo o que tenho.
    """
    if not reply or not ultima_bot:
        return reply
    if not _mesma_pergunta(reply, ultima_bot):
        return reply
    _registrar_falha("ia repetir a mesma pergunta — troquei por confirmação")
    return _confirmacao_seca(items)


# ---------------------------------------------------------------------------
# COMO AS PESSOAS REALMENTE FALAM
# ---------------------------------------------------------------------------
# Ninguém decora comando. A pessoa não escreve "feito", escreve "resolvi",
# "já paguei", "tá pago". Não escreve "adiar", escreve "deixa pra amanhã".
# Enquanto isso viveu só no prompt, oscilava — e o pior caso não é o bot não
# entender: é ele criar um item novo chamado "resolvi" e deixar o original
# pendente, cobrando a pessoa de algo que ela já fez.

_CONCLUSAO_RE = re.compile(
    r"(?i)^\s*(?:ja\s+)?(?:"
    r"feito|feita|pronto|prontinho|resolvi|resolvido|resolvida|"
    r"paguei|pago|paga|quitei|quitado|"
    r"comprei|comprado|peguei|busquei|fui|liguei|marquei|mandei|entreguei|"
    r"fiz|conclui|conclu[íi]|finalizei|terminei|acabei|encerrei|"
    r"dei\s+baixa|ta\s+feito|t[áa]\s+feito|ta\s+pago|t[áa]\s+pago|"
    r"ja\s+foi|j[áa]\s+foi|ja\s+era|j[áa]\s+era|ja\s+fiz|j[áa]\s+fiz|"
    r"ja\s+resolvi|j[áa]\s+resolvi|ja\s+paguei|j[áa]\s+paguei"
    r")\b[\s.!,]*$")

_ADIAMENTO_RE = re.compile(
    r"(?i)\b(?:"
    r"deixa\s+(?:pra|para)\s+(?:amanh[ãa]|depois|semana|outro\s+dia|segunda)|"
    r"fica\s+(?:pra|para)\s+(?:amanh[ãa]|depois|semana|outro\s+dia)|"
    r"empurra\s+(?:pra|para|um)|joga\s+(?:pra|para)\s+(?:amanh[ãa]|depois)|"
    r"passa\s+(?:pra|para)\s+(?:amanh[ãa]|semana|depois)|"
    r"mais\s+tarde|outro\s+dia|semana\s+que\s+vem|"
    r"n[ãa]o\s+vai\s+dar\s+hoje|hoje\s+n[ãa]o\s+vai\s+dar"
    r")\b")


def _e_conclusao_explicita(text: str) -> bool:
    """A mensagem é, sozinha, um 'já resolvi'? (frase curta e fechada)"""
    t = (text or "").strip()
    if len(t) > 40:          # frase longa carrega assunto novo junto
        return False
    return bool(_CONCLUSAO_RE.match(t))


def _e_adiamento_explicito(text: str) -> bool:
    """'deixa pra amanhã' é adiar, mesmo sem a palavra 'adiar'."""
    return bool(_ADIAMENTO_RE.search(text or ""))


# Faltou a hora? Não trave a conversa nem invente: OFEREÇA.
HORA_MANHA = os.environ.get("HORA_PADRAO_MANHA", "08:00")
HORA_NOITE = os.environ.get("HORA_PADRAO_NOITE", "20:00")


def _oferecer_horario(reply: str, items: list) -> str:
    """Item com DIA e sem HORA: pergunta manhã ou noite, com padrão explícito.

    Pedido do Kevin: "se não mandar o horário, ele poderia dizer 'te lembro de
    manhã ou à noite'". É melhor que as duas alternativas ruins: travar o
    cadastro exigindo hora, ou escolher uma hora em silêncio e o aviso chegar
    numa hora que não serve.
    """
    if not reply or not items:
        return reply
    sem_hora = [i for i in items
                if i.get("data_vencimento") and not i.get("hora_alvo")]
    if not sem_hora or len(sem_hora) != len(items):
        return reply
    if "?" in reply:                 # já tem pergunta; não empilha outra
        return reply
    return (f"{reply.rstrip()}\n\n"
            f"Que horas te aviso? Responde *manhã* ({HORA_MANHA}) ou "
            f"*noite* ({HORA_NOITE}) — ou me diz a hora exata.")


def _assunto_da_vez(result: dict, itens_abertos: list) -> str:
    """Qual item ESTA mensagem está tratando? Só devolve quando é inequívoco.

    Um item criado agora, ou um item sendo atualizado: esse é o assunto.
    Se houver mais de um, devolve vazio — a guarda de assunto não age no
    escuro (melhor não agir do que reancorar no item errado).
    """
    itens_novos = result.get("items") or []
    if len(itens_novos) == 1:
        return (itens_novos[0].get("descricao") or "").strip()
    if itens_novos:
        return ""
    alvo = (result.get("atualizar") or {}).get("id")
    if alvo:
        for it in (itens_abertos or []):
            if it.get("id") == alvo:
                return (it.get("descricao") or "").strip()
    return ""


def _radical_em(desc: str, alvo_baixo: str) -> bool:
    """Alguma palavra de conteúdo da descrição aparece no texto?"""
    for p in _palavras(_sem_acento(desc or "").lower()):
        if len(p) >= 4 and p[:5] in alvo_baixo:
            return True
    return False


def _nao_trocar_de_assunto(reply: str, alvo_desc: str,
                           itens_abertos: list) -> str:
    """A resposta está falando de OUTRO lembrete que não o da conversa.

    Caso real (03/08, 15:48):
        Kevin: "Amanhã preciso ir no mercado me lembra"
        bot:   "Guardei: ir no mercado · 04/08. Que horas?"
        Kevin: "Manhã"
        bot:   "Te aviso às 08:00 pra *comprar frutas*."   <- item #62, antigo

    E daí em diante o bot conduziu um diálogo inteiro sobre mamão, em cima do
    item errado. O LLM recebe a lista de itens abertos no prompt e, quando a
    resposta do usuário é curta ("Manhã"), ele reancora no item mais parecido
    em vez do item da conversa.

    Regra: se a resposta cita a descrição de OUTRO item aberto e NÃO cita a do
    item que está em jogo, ela está fora do assunto. Confirmo o item certo e
    saio — errar de item é pior que responder pouco, porque o usuário só
    descobre no dia em que o aviso não vem.
    """
    if not reply or not alvo_desc:
        return reply
    baixo = _sem_acento(reply).lower()
    if _radical_em(alvo_desc, baixo):
        return reply                      # citou o item certo: ok
    alvo_norm = _sem_acento(alvo_desc).strip().lower()
    for it in (itens_abertos or []):
        d = (it.get("descricao") or "").strip()
        if not d or _sem_acento(d).lower() == alvo_norm:
            continue
        if _radical_em(d, baixo):
            _registrar_falha(
                f"resposta falava de '{d[:30]}' mas o assunto era "
                f"'{alvo_desc[:30]}' — reancorei no item certo")
            return f"Anotado ✅ — *{alvo_desc}*."
    return reply


# A resposta PROMETE um horário/dia que o item não tem?
_PROMESSA_HORA_RE = re.compile(
    r"(?i)(?:te\s+lembro|vou\s+te\s+lembrar|te\s+aviso|aviso\s+voc[êe])"
    r"[^.!?\n]{0,60}?(?:\b[àa]s\s*\d{1,2}|\bem\s+\d+\s*(?:min|hora)|"
    r"\bdaqui\s+a?\s*\d+|\bamanh[ãa]\b|\bhoje\b)")


def _nao_prometer_o_que_nao_gravei(reply: str, items: list) -> str:
    """Se eu disse 'te lembro às X' e o item não tem quando, eu confesso.

    Caso real (03/08, 15:16): "me lembra daqui 10min" -> resposta "Anotado.
    Vou te lembrar em 10 minutos." e item gravado com data=NULL, hora=NULL.
    O alarme nunca ia tocar e a pessoa só descobriria depois de perder a
    consulta. Prometer o que não está no banco é a única falha deste produto
    que não tem conserto depois: quebra a confiança exatamente no momento em
    que ela era necessária.
    """
    if not reply or not items:
        return reply
    if not _PROMESSA_HORA_RE.search(reply):
        return reply
    # basta UM item com quando definido pra promessa ter lastro
    if any(i.get("hora_alvo") or i.get("data_vencimento") for i in items):
        return reply
    _registrar_falha("resposta prometeu horário e nenhum item tem quando — "
                     "troquei a promessa por um pedido de horário")
    desc = (items[0].get("descricao") or "isso").strip()
    return (f"Guardei *{desc}* ✅ — mas ainda não consegui marcar a hora, "
            f"então eu *não* vou conseguir te avisar.\n\n"
            f"Me diz o quando: _\"hoje às 18h\"_, _\"amanhã de manhã\"_ ou "
            f"_\"daqui 2 horas\"_.")


def _polir_resposta(reply: str, items: list, ultima_bot: str = "",
                    texto_usuario: str = "", alvo_desc: str = "",
                    itens_abertos: list = None) -> str:
    """Toda a faxina de resposta, num lugar só, na ordem que importa.

    Existir em UM lugar é o ponto. Enquanto essa sequência estava copiada
    dentro do ramo de registro, o ramo da reconsulta gravava o item certo e
    mandava a resposta crua — e ninguém percebia, porque o banco estava OK.
    Quem confere só o banco não vê; quem confere só a tela não confia.
    """
    if not reply:
        return reply
    # PRIMEIRO de tudo: a resposta é sobre o item CERTO?
    # Tem que vir antes do bloco "Guardei", senão ele anexa a descrição certa,
    # a resposta passa a citar o item certo E o errado, e a guarda deixa
    # passar achando que está tudo bem. Ordem aqui não é estilo, é correção.
    reply = _nao_trocar_de_assunto(reply, alvo_desc, itens_abertos or [])
    reply = _tirar_pergunta_redundante(reply, items)
    reply = _corrigir_hoje_falso(reply, items)
    reply = _completar_ano_nas_datas(reply, items)
    # enchimento ANTES de anexar "Guardei também": depois que o bloco entra,
    # a frase de enchimento deixa de estar no fim e escapa da regra.
    reply = tirar_enchimento(reply)
    reply = _confirmar_todos_os_itens(reply, items)
    # nunca prometer aviso que o banco não sustenta
    reply = _nao_prometer_o_que_nao_gravei(reply, items)
    # tem dia mas não tem hora? oferece manhã/noite em vez de escolher sozinho
    reply = _oferecer_horario(reply, items)
    # a pessoa disse "não tem valor"? então não pergunta de novo.
    reply = _nao_insistir(reply, items, texto_usuario)
    # por último: se mesmo assim eu ia repetir a última fala, corta o loop.
    reply = _quebrar_loop(reply, items, ultima_bot)
    return reply


def _confirmar_todos_os_itens(reply: str, items: list) -> str:
    """Se gravou 2 e só falou de 1, a resposta completa o que faltou.

    Teste ao vivo 03/08: "comprar ração da Nina sexta E trocar o óleo do carro
    em 5000km ou 6 meses" gravou os DOIS itens no banco, e o bot respondeu só
    "Anotado a troca de hoje". A ração estava salva — o usuário não tinha como
    saber. Isso é pior que perder o item: ele manda de novo e vira duplicata,
    ou deixa de confiar e volta a anotar no papel.

    A promessa do produto é "não perde item". Não perder inclui AVISAR.
    """
    if not items:
        return reply
    # v16.8: antes só agia com 2+ itens. Mas depois que o corte de pergunta
    # redundante transforma "Anotado. Qual a data do pediatra?" em "Anotado.",
    # sobra uma confirmação que não diz O QUÊ nem QUANDO. Item único também
    # precisa aparecer.
    baixo = _sem_acento(reply or "").lower()

    def _citado(desc: str) -> bool:
        # Compara pelo RADICAL (5 primeiras letras): a resposta escreve
        # "troca" e o item é "trocar" — sem isso o item era listado de novo,
        # e confirmação em dobro faz o usuário achar que duplicou.
        palavras = [p for p in _palavras(_sem_acento(desc or "").lower())
                    if len(p) >= 4]
        return any(p[:5] in baixo for p in palavras) if palavras else True

    faltando = [it for it in items if not _citado(it.get("descricao", ""))]
    if not faltando:
        return reply
    citados = len(items) - len(faltando)
    _registrar_falha(f"resposta citou {citados} de {len(items)} itens — "
                     f"completei na mão")
    linhas = [f"• {_item_linha(it)}" for it in faltando]
    # "também" só faz sentido se algo já foi citado antes.
    cabecalho = "Guardei também:" if citados else "Guardei:"
    return f"{reply.rstrip()}\n\n{cabecalho}\n" + "\n".join(linhas)


def _e_pergunta(text: str) -> bool:
    """Pergunta merece resposta de verdade, mesmo fora do escopo — nunca o
    genérico do fluxo clássico. Detecta pelo '?' e por abertura interrogativa
    (muita gente não digita '?' no WhatsApp)."""
    low = text.strip().lower()
    if "?" in low:
        return True
    return any(low.startswith(p) or f" {p} " in f" {low} "
               for p in _INTERROGATIVAS)


def _resgatar_valores(itens: list, reply: str) -> int:
    """Recupera valores que o LLM escreveu na resposta mas esqueceu no campo.

    Aconteceu de verdade: a resposta listava "Luz — R$ 187,00" e o item ia
    pro banco com valor_reais=None. O usuário lê certo e o dado nasce errado.
    Aqui, para cada item sem valor, procuramos na linha da resposta que cita
    a descrição dele um "R$ X" e usamos esse número. Só age quando é
    inequívoco (uma linha, um valor).
    """
    if not reply:
        return 0
    linhas = [l for l in reply.split("\n") if "R$" in l]
    resgatados = 0
    for it in itens:
        if it.get("valor_reais") is not None:
            continue
        desc = (it.get("descricao") or "").strip().lower()
        if not desc:
            continue
        for linha in linhas:
            if desc not in linha.lower():
                continue
            achados = re.findall(r"R\$\s*([\d.]+,\d{2}|[\d.]+)", linha)
            if len(achados) != 1:
                continue
            bruto = achados[0].replace(".", "").replace(",", ".")
            try:
                it["valor_reais"] = float(bruto)
                resgatados += 1
            except ValueError:
                pass
            break
    return resgatados


def _valores_citados(texto: str) -> set:
    """Todo 'R$ 187,00' / 'R$ 187' que aparece no texto, normalizado."""
    achados = set()
    for bruto in re.findall(r"R\$\s*([\d.]+,\d{2}|[\d.]+)", texto or ""):
        try:
            achados.add(round(float(bruto.replace(".", "").replace(",", ".")), 2))
        except ValueError:
            pass
    return achados


def _consulta_confere(reply: str, itens: list) -> Optional[str]:
    """Barra consulta que cita conta que NÃO existe no banco.

    Bug real: o banco estava vazio e o mordomo respondeu "Vencendo essa
    semana: Luz R$187, Net R$129, Seguro R$340" — tudo lido da conversa
    antiga, tudo já apagado. Inventar conta é o pior defeito possível num
    produto que existe para o usuário confiar de olhos fechados.

    Devolve None se a resposta está OK, ou um texto substituto se mentiu.
    """
    citados = _valores_citados(reply)
    if not citados:
        return None  # não citou dinheiro: nada a conferir aqui
    reais = {round(float(i["valor_reais"]), 2) for i in itens
             if i.get("valor_reais") is not None}
    fantasmas = citados - reais
    if not fantasmas:
        return None
    if not itens:
        return ("Olhei aqui e você *não tem nada anotado* no momento.\n\n"
                "Me manda uma conta ou lembrete que eu guardo.")
    return ("Deixa eu te dar o que está *realmente* guardado agora:\n\n"
            + "\n".join(f"• {_item_linha(i)}" for i in itens[:5]))


def _moeda(v) -> str:
    """1350.0 -> 'R$ 1.350,00'. Sem o ponto de milhar, valor alto vira
    'R$ 1350,00' e fica ruim de bater o olho."""
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    except (TypeError, ValueError):
        return ""


def _item_linha(it: dict) -> str:
    partes = [f"*{(it.get('descricao') or 'item').strip()}*"]
    if it.get("valor_reais") is not None:
        partes.append("— *" + _moeda(it["valor_reais"]) + "*")
    if it.get("data_vencimento"):
        quando = _br(it["data_vencimento"])
        # A HORA TEM QUE APARECER.
        # "pegar encomenda na farmácia · 03/08" não serve: o usuário pediu
        # 14:30 e a confirmação escondia justamente o dado que ele deu. Item
        # com hora marcada é alarme — e alarme sem hora visível não tranquiliza
        # ninguém, que é o único motivo de existir a confirmação.
        if it.get("hora_alvo"):
            quando += f" às {it['hora_alvo']}"
        partes.append("· *" + quando + "*")
    elif it.get("hora_alvo"):
        partes.append("· *" + str(it["hora_alvo"]) + "*")
    return " ".join(partes)


def _reescrever_consulta(reply: str, itens: list) -> str:
    """Em consulta, a LISTA sai do banco — nunca do texto do LLM.

    Bug real: o item era "fatura do cartão" e a resposta saiu "Luz — R$
    1350,00", com o nome puxado de uma conversa antiga. Valor certo, nome
    errado, e o usuário paga a conta errada. Aqui a gente mantém a fala do
    mordomo (abertura e fecho) mas troca os bullets pelos itens reais.
    """
    if not itens:
        return reply
    linhas = [l for l in (reply or "").split("\n")]
    def e_bullet(l):
        return l.strip().startswith(("•", "·", "-", "*ph")) or bool(
            re.match(r"^\s*\d+[.)]\s", l))
    if not any(e_bullet(l) for l in linhas):
        return reply  # não é resposta em lista: deixa como está
    antes = [l for l in linhas[:next(i for i, l in enumerate(linhas) if e_bullet(l))]
             if l.strip()]
    depois = [l for l in linhas[max(i for i, l in enumerate(linhas) if e_bullet(l)) + 1:]
              if l.strip()]
    bloco = [f"• {_item_linha(i)}" for i in itens[:5]]
    if len(itens) > 5:
        bloco.append(f"• _e mais {len(itens) - 5}_")
    partes = []
    if antes:
        partes.append(antes[0])
        partes.append("")
    partes.extend(bloco)
    if depois:
        partes.append("")
        partes.append(depois[-1])
    return "\n".join(partes)


def _tirar_pergunta_redundante(reply: str, itens: list) -> str:
    """Remove a pergunta final quando ela pede algo que já está guardado.

    O bot escrevia a lista com os valores e emendava "Qual o valor de cada
    um?". Pra quem lê, é sinal de que ele não entendeu — mata a confiança
    mais rápido do que um erro de verdade.
    """
    if not reply or not itens:
        return reply
    # A pergunta pode vir na MESMA linha da confirmação ("Anotado. Qual o
    # valor?"). Por isso olhamos a última FRASE, não a última linha — foi
    # assim que "Qual o valor do plano?" passou batido tendo o valor.
    corpo = reply.rstrip()
    m = re.search(r"([^.!?\n]*\?)\s*$", corpo)
    if not m:
        return reply
    pergunta = m.group(1).strip()
    ultima = _sem_acento(pergunta).lower().lstrip("*_ ")
    # "E o valor da encomenda?" começava com "e" e escapava do filtro de
    # pedido, que só olhava "qual/quanto/quando". Conector no início não muda
    # o que a frase pede.
    ultima = re.sub(r"^(?:e|mas|ah|so|entao|ai|agora|ok|certo)\s+", "", ultima)
    # Só mexe em pergunta que PEDE dado ("qual o valor?", "quando vence?").
    # Oferta ("quer que eu te avise um dia antes?") é útil e fica.
    pedido = (any(ultima.startswith(p) for p in
                  ("qual", "quais", "quanto", "quando", "me diz", "me informa",
                   "poderia me", "pode me dizer", "voce sabe", "que horas",
                   "o valor", "o preco", "a data"))
              # "...da encomenda, qual o valor?" — o pedido não precisa estar
              # no começo pra ser um pedido.
              or re.search(r"\b(qual|quanto|quando|que horas)\b", ultima))
    if not pedido:
        return reply
    # `ultima` já vem sem acento (_sem_acento acima), então "preco", não "preço".
    pede_valor = any(p in ultima for p in
                     ("valor", "quanto", "preco", "custou", "custa", "custo"))
    pede_data = ("data" in ultima or "vencimento" in ultima
                 or "quando" in ultima or "que horas" in ultima)
    tem_valores = all(i.get("valor_reais") is not None for i in itens)
    tem_datas = all(i.get("data_vencimento") for i in itens)

    # LEMBRETE NÃO TEM PREÇO.
    # Caso real (03/08, 14:08): "me lembra hoje às 14:30 que preciso pegar
    # minha encomenda na farmácia" -> "Anotado. Qual o valor da encomenda?".
    # O item foi salvo certo (lembrete, hoje, 14:30). A pergunta é pura
    # invenção: pegar encomenda não custa nada, e mesmo que custasse o
    # usuário não pediu controle de gasto — pediu pra ser lembrado.
    # Perguntar preço de um lembrete faz o bot parecer um formulário de
    # cobrança, e é o tipo de atrito que faz a pessoa desistir no dia 1.
    # Valor só é pergunta legítima em DESPESA (aí sim o número é o ponto).
    so_lembretes = all(i.get("tipo") == "lembrete" for i in itens)
    if pede_valor and so_lembretes and not pede_data:
        return _limpar_sobra(corpo[:m.start(1)])

    redundante = ((pede_valor and tem_valores and not pede_data)
                  or (pede_data and tem_datas and not pede_valor)
                  or (pede_valor and pede_data and tem_valores and tem_datas))
    if not redundante:
        return reply
    return _limpar_sobra(corpo[:m.start(1)]) or reply


def _limpar_sobra(t: str) -> str:
    """Depois de cortar a pergunta, não pode sobrar conector órfão.

    "Anotado. E o valor?" -> cortar a pergunta deixava "Anotado. E" pendurado.
    """
    t = (t or "").rstrip()
    t = re.sub(r"[\s,;:—–-]*\b(e|mas|então|entao|só|so|ah|aí|ai)\s*$", "",
               t, flags=re.IGNORECASE)
    return t.rstrip(" ,;:—–-\n")


# "NÃO TEM" É UMA RESPOSTA, NÃO UMA NÃO-RESPOSTA.
#
# Kevin, 03/08 14:09: o bot perguntou o valor, ele respondeu "Nao tem valor,
# é um lembrete apenas" — e o bot perguntou de novo, igual. Para o LLM aquilo
# não pareceu resposta porque não veio número. Para qualquer pessoa, é uma
# resposta clara e definitiva: *esse campo não existe pra esse item*.
#
# Isso vira função e não instrução de prompt porque prompt oscila: hoje ele
# entende, amanhã pergunta de novo. E perguntar duas vezes a mesma coisa é o
# defeito que mais rápido convence o usuário de que o produto não escuta.
_RECUSA_CAMPO_RE = re.compile(
    r"(?i)\b(?:nao|n[aã]o)\s+(?:tem|possui|h[aá]|precisa|sei|informei)\b"
    r"|\bsem\s+(?:valor|pre[çc]o|custo|data|hora|hor[aá]rio)\b"
    r"|\b(?:e|eh|é)\s+(?:s[oó]\s+)?(?:um\s+)?lembrete\b"
    r"|\bn[aã]o\s+[eé]\s+(?:uma\s+)?despesa\b"
    r"|\bdeixa\s+(?:sem|em\s+branco|assim|vazio)\b"
    r"|\bnao\s+se\s+aplica\b|\btanto\s+faz\b|\bqualquer\s+(?:um|hora)\b")


def usuario_recusou_campo(text: str) -> bool:
    """A pessoa disse que o campo não existe ('não tem valor', 'é só um
    lembrete', 'sem data'). Isso ENCERRA a pergunta — não a reabre."""
    return bool(_RECUSA_CAMPO_RE.search(_sem_acento(text or "")))


def _nao_insistir(reply: str, items: list, texto_usuario: str) -> str:
    """Se a pessoa acabou de dizer 'não tem', a resposta não pode perguntar
    de novo. Confirma o que existe e sai do caminho."""
    if not reply or not usuario_recusou_campo(texto_usuario):
        return reply
    if "?" not in reply:
        return reply
    _registrar_falha("usuario disse que o campo nao existe — parei de perguntar")
    seca = _confirmacao_seca(items)
    return f"{seca}\n\nSe quiser completar depois, é só me mandar."


_VERBOS_ASSUNTO_NOVO = (
    "marquei", "agendei", "comprei", "troquei", "paguei", "fiz", "peguei",
    "contratei", "assinei", "recebi", "chegou", "vence", "tem que", "tem q",
    "preciso", "me lembra", "lembra de", "anota", "anote", "guarda",
)


def _anuncia_assunto_novo(text: str) -> bool:
    """A frase ANUNCIA um fato novo em vez de responder a pergunta?

    "marquei dentista dia 18 as 14h" tem 6 palavras e veio logo depois de uma
    pergunta minha — a rede de segurança engoliu como se fosse complemento e
    o dentista NUNCA foi salvo. Verbo de anúncio ("marquei", "troquei",
    "comprei") é assunto novo, não resposta.
    """
    low = " " + (text or "").strip().lower() + " "
    return any(f" {v} " in low or low.startswith(f" {v}")
               for v in _VERBOS_ASSUNTO_NOVO)


_STOPWORDS = {
    "de", "da", "do", "das", "dos", "a", "o", "as", "os", "e", "em", "no",
    "na", "nos", "nas", "um", "uma", "para", "pra", "por", "com", "que",
    "me", "meu", "minha", "todo", "toda", "todos", "todas", "dia", "dias",
    "mes", "mês", "hora", "horas", "as", "às",
    "lembra", "lembre", "lembrar", "tomar", "pagar", "anota", "anote",
}
# "conta", "boleto" e "fatura" NÃO entram: sem elas "fatura do cartão" fica
# só com "cartão" e a comparação de duplicata perde precisão.


def _tira_acento_simples(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


_STOPWORDS_SEM_ACENTO = {_tira_acento_simples(p) for p in _STOPWORDS}


def _sem_acento(txt: str) -> str:
    """No WhatsApp metade escreve 'oleo' e a outra 'óleo'. Sem normalizar,
    'Óleo do carro' e 'oleo do carro' viravam dois itens diferentes."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", txt or "")
                   if unicodedata.category(c) != "Mn")


def _palavras(txt: str) -> set:
    limpo = re.sub(r"[^\w ]", " ", _sem_acento(txt).lower())
    return {p for p in limpo.split()
            if len(p) > 2 and p not in _STOPWORDS_SEM_ACENTO}


def _atualizacao_plausivel(text: str, alvo: dict, pergunta_aberta: bool) -> bool:
    """O usuário está mesmo falando DESTE item, ou o LLM encaixou no item errado?

    Caso real: com "losartana" salva, o usuário pediu "vitamina D todo dia às
    9h" e o modelo ATUALIZOU a losartana. A vitamina D nunca existiu e o
    lembrete original foi alterado — o usuário perde os dois de uma vez.

    Regra: ou é um fragmento curto respondendo a uma pergunta minha
    ("são 340 reais"), ou a mensagem tem que citar algo do item alvo.
    """
    if not alvo:
        return False
    palavras_texto = _palavras(text)
    if pergunta_aberta and len(text.split()) <= 4 and not palavras_texto:
        return True  # "187", "dia 20" — resposta pura
    palavras_alvo = _palavras(alvo.get("descricao", ""))
    if palavras_texto & palavras_alvo:
        return True  # citou o nome do item: é ele mesmo
    if pergunta_aberta and len(text.split()) <= 4:
        return True  # fragmento curto logo após pergunta
    return False


_PROMESSAS = (
    "anotado", "anotei", "guardei", "vou te lembrar", "vou lembrar",
    "te aviso", "te lembro", "deixei agendado", "agendado", "registrei",
    "está guardado", "esta guardado", "pode deixar comigo", "tá anotado",
    "ta anotado", "marcado para", "vou avisar",
)


def _promete_guardar(reply: str) -> bool:
    """A resposta PROMETE que algo ficou guardado?

    Invariante do produto: prometer sem gravar é a única falha que destrói a
    confiança de vez — o usuário para de conferir justamente porque confiou.
    Se prometeu e não persistiu nada, é mentira e tem que ser corrigido.
    """
    low = (reply or "").lower()
    return any(p in low for p in _PROMESSAS)


_MANUTENCAO = ("oleo", "óleo", "revisao", "revisão", "filtro", "correia",
               "pneu", "alinhamento", "balanceamento", "velas")


def _e_manutencao(desc: str) -> bool:
    low = (desc or "").lower()
    return any(p in low for p in _MANUTENCAO)


def _num(txt: str) -> Optional[float]:
    try:
        return float(txt.replace(".", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _extrair_manutencao(text: str, fatos: list) -> dict:
    """Tira do texto (e do que já foi aprendido) os números da manutenção.

    Conta não pode ficar no prompt: o modelo entendeu "10 mil km ou 6 meses,
    rodo 800 km/mês" e mesmo assim não agendou nada. Número a gente extrai e
    calcula em Python — igual fizemos com valor e data, que só pararam de
    errar quando saíram do texto.
    """
    dados = {"km": None, "meses": None, "km_mes": None}
    baixo = " " + (text or "").lower().replace("mil", "000") + " "
    baixo = re.sub(r"(\d)\s+000", r"\g<1>000", baixo)

    m = re.search(r"(\d[\d.,]*)\s*km\s*(?:por|/|a cada)\s*(?:m[eê]s|mes)", baixo)
    if m:
        dados["km_mes"] = _num(m.group(1))
    m = re.search(r"rodo\s+(?:uns?\s+)?(\d[\d.,]*)", baixo)
    if m and dados["km_mes"] is None:
        dados["km_mes"] = _num(m.group(1))
    for m in re.finditer(r"(\d[\d.,]*)\s*km", baixo):
        v = _num(m.group(1))
        if v and v >= 1000 and dados["km"] is None:
            dados["km"] = v          # intervalo (10.000), não a quilometragem/mês
    m = re.search(r"(\d[\d.,]*)\s*(?:mes|m[eê]s|meses)", baixo)
    if m:
        dados["meses"] = _num(m.group(1))

    for f in (fatos or []):
        chave, valor = str(f.get("chave", "")).lower(), _num(str(f.get("valor", "")))
        if valor is None:
            continue
        if dados["km"] is None and "km" in chave and "mes" not in chave:
            dados["km"] = valor
        if dados["km_mes"] is None and "km_por_mes" in chave:
            dados["km_mes"] = valor
        if dados["meses"] is None and ("mes" in chave and "km" not in chave):
            dados["meses"] = valor
    return dados


def _data_dois_gatilhos(dados: dict) -> Optional[tuple]:
    """Devolve (data_iso, motivo) do que vencer PRIMEIRO — km ou tempo."""
    import datetime
    hoje = tempo.hoje()
    candidatos = []
    if dados.get("km") and dados.get("km_mes"):
        meses_km = dados["km"] / max(dados["km_mes"], 1)
        candidatos.append((_soma_meses(hoje, meses_km),
                           f"{int(dados['km']):,}".replace(",", ".") + " km"))
    if dados.get("meses"):
        candidatos.append((_soma_meses(hoje, dados["meses"]),
                           f"{int(dados['meses'])} meses"))
    if not candidatos:
        return None
    data, motivo = min(candidatos, key=lambda c: c[0])
    outro = [c for c in candidatos if c[1] != motivo]
    if outro:
        motivo = f"{motivo} vence antes de {outro[0][1]}"
    return data.isoformat(), motivo


def _soma_meses(data, meses: float):
    import datetime
    dias = int(round(meses * 30.44))
    return data + datetime.timedelta(days=dias)


def _ja_existe(descricao: str, itens: list) -> Optional[dict]:
    """Item praticamente igual já aberto? Evita 'Óleo do carro' duplicado.

    Aconteceu no teste: duas linhas idênticas, as duas sem data. Duas linhas
    para a mesma coisa é ruído — o usuário não sabe qual vale.
    """
    novas = _palavras(descricao)
    if not novas:
        return None
    for it in itens:
        antigas = _palavras(it.get("descricao", ""))
        if not antigas:
            continue
        comuns = novas & antigas
        if comuns and len(comuns) >= min(len(novas), len(antigas)):
            return it   # todas as palavras significativas batem
    return None


_FATO_QUE_MUDA = (
    "copa do mundo", "campeonato", "brasileirao", "brasileirão", "libertadores",
    "champions", "quem ganhou", "quem venceu", "quem foi campeao",
    "quem foi campeão", "campeao", "campeão", "placar", "resultado do jogo",
    "que horas joga", "eleicao", "eleição", "presidente do brasil",
    "quem e o presidente", "quem é o presidente", "cotacao", "cotação",
    "dolar hoje", "dólar hoje", "bitcoin", "selic", "noticia", "notícia",
    "ultimas noticias", "últimas notícias", "quanto custa hoje",
    "preco de hoje", "preço de hoje", "bolsa hoje", "ibovespa",
)


def _pergunta_fato_do_mundo(text: str) -> bool:
    """Pergunta sobre fato do mundo que MUDA com o tempo?

    Caso real e vergonhoso: perguntado quem ganhou a última Copa, respondeu
    "2022, a França" — errado duas vezes (foi a Argentina, e já houve a Copa
    de 2026). Depois afirmou que a Copa de 2026 "ainda não aconteceu",
    estando em agosto de 2026.

    O modelo tem data de corte: ele sabe o dia de hoje porque eu escrevo no
    prompt, mas o conhecimento de mundo dele parou no passado. Resultado,
    placar, eleição, cotação e notícia ele NÃO tem como saber — e chutar
    sobre isso destrói a confiança em tudo o mais que ele diz.
    """
    low = _sem_acento(text or "").lower()
    return any(_sem_acento(p) in low for p in _FATO_QUE_MUDA)


_MODELO_BUSCA = os.environ.get("MODELO_BUSCA", "gpt-4o-search-preview")


def _responder_com_busca(text: str, nome: str) -> Optional[str]:
    """Vai na web e responde de verdade.

    Uma A.I. tem que saber — e saber não é chutar de memória. O modelo normal
    tem data de corte: perguntado sobre a Copa, respondeu Argentina, depois
    França, depois Brasil. Aqui a pergunta vai para um modelo COM busca, que
    olha a informação atual antes de falar.

    Se a busca falhar, devolve None e o chamador assume o caminho honesto —
    nunca voltamos a chutar.
    """
    try:
        from litellm import completion
    except Exception:
        return None
    # DUAS TENTATIVAS. Medido em produção (03/08/2026): 08:46 a mesma pergunta
    # caiu no "não acesso notícia" e 08:55 respondeu certo. Não era o gatilho —
    # era a chamada de busca falhando de forma intermitente e o usuário levando
    # uma desculpa permanente por uma falha temporária. Isso é pior que não
    # responder: ensina o usuário que o bot não sabe, quando ele sabe.
    ultimo_erro = None
    for tentativa in range(2):
        try:
            resp = completion(
                model=_MODELO_BUSCA,
                max_tokens=500,
                messages=[
                    {"role": "system", "content": (
                        "Você é o Resolve AI no WhatsApp: você garante que a "
                        "pessoa não esqueça das coisas. "
                        "Responda a pergunta do usuário com informação ATUAL, "
                        "buscando na web. Em português do Brasil.\n"
                        "REGRAS: responda em no máximo 3 linhas curtas, direto "
                        "ao ponto, sem citar fontes nem colar link. Negrito do "
                        "WhatsApp é *asterisco simples*. NUNCA use títulos "
                        "markdown (##), listas com hífen nem colchetes. Se a "
                        "busca não trouxer certeza, diga que não achou — nunca "
                        "invente.\n"
                        "Depois da resposta, pule uma linha e puxe UMA frase "
                        "curta para o que você faz: não deixar ele esquecer "
                        "conta, remédio, consulta e recompra.")},
                    {"role": "user", "content": text},
                ],
            )
            txt = (resp.choices[0].message.content or "").strip()
            txt = _limpar_saida_busca(txt)
            if txt:
                return txt
            ultimo_erro = "resposta vazia"
        except Exception as e:
            ultimo_erro = repr(e)
    _registrar_falha(f"busca web falhou 2x ({_MODELO_BUSCA}): {ultimo_erro}")
    return None


# Lixo que o modelo de busca cola na resposta mesmo mandado não colar.
# Instrução em prompt não segura formatação — isso é trabalho de função.
_RE_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:https?|www)[^)]*\)")
_RE_URL_CRU = re.compile(r"https?://\S+|\bwww\.\S+")
_RE_UTM = re.compile(r"[?&]utm_[^\s)]*")
_RE_BULLET_MD = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
_RE_NEGRITO_MD = re.compile(r"\*\*([^*]+)\*\*")
_RE_LINHAS_VAZIAS = re.compile(r"\n{3,}")
# frases que só existem para segurar um link
_RE_CHAMADA_LINK = re.compile(
    r"(?im)^\s*(?:veja\s+mais|saiba\s+mais|leia\s+mais|mais\s+em|fonte|fontes|"
    r"refer[êe]ncias?|link|dispon[íi]vel)\s*(?:em|:)?\s*(?=https?://|www\.|$).*$")
# títulos de seção que o modelo de busca cola por conta própria
_RE_CABECALHO_LIXO = re.compile(
    r"(?im)^\s*(?:highlights?|sources?|citations?|refer[êe]ncias?|fontes?|"
    r"resumo|summary|key\s+points?)\s*:?\s*$\n?")


def _limpar_saida_busca(txt: str) -> str:
    """Tira markdown e link da resposta de busca antes de ir pro WhatsApp.

    O WhatsApp não renderiza markdown: "## Highlights" chega literalmente
    como "## Highlights", e link cru vira um paredão azul de 120 caracteres
    que ocupa metade da tela do celular. Foi exatamente o que o usuário 23
    recebeu em 03/08.
    """
    if not txt:
        return ""
    # 1) LINHA QUE SÓ EXISTIA PRA SEGURAR LINK MORRE INTEIRA.
    # Tirar só a URL deixava o cadáver: "• ¡¡España, campeona del mundo!!
    # 'Ha ganado el fútbol', Publicado en Sunday, July 19" — uma citação em
    # espanhol pendurada no fim da resposta. Foi o que chegou no zap às 10:42.
    linhas = []
    for linha in txt.split("\n"):
        tem_link = bool(_RE_MD_LINK.search(linha) or _RE_URL_CRU.search(linha))
        if tem_link:
            e_bullet = bool(re.match(r"^\s*[-*+•]\s", linha))
            se_tirar = _RE_URL_CRU.sub("", _RE_MD_LINK.sub("", linha)).strip()
            # bullet com link = citação. Sobra curta = a linha era o link.
            if e_bullet or len(se_tirar) < 25:
                continue
        linhas.append(linha)
    t = "\n".join(linhas)

    t = _RE_MD_LINK.sub(r"\1", t)        # [texto](url) -> texto
    t = _RE_UTM.sub("", t)
    # "Veja mais em <url>" sem a url vira "Veja mais em" pendurado. Mata a
    # frase inteira, não só o link.
    t = _RE_CHAMADA_LINK.sub("", t)
    t = _RE_URL_CRU.sub("", t)           # url solta some
    t = _RE_MD_HEADER.sub("", t)         # ## Titulo -> Titulo
    # depois de tirar o "##": a linha vira "Highlights" pelada. Só agora dá
    # pra reconhecer e matar o cabeçalho.
    t = _RE_CABECALHO_LIXO.sub("", t)    # "Highlights", "Sources", "Fontes"
    t = _RE_NEGRITO_MD.sub(r"*\1*", t)   # **x** -> *x* (negrito do zap)
    t = _RE_BULLET_MD.sub("• ", t)       # - item -> • item
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\(\s*\)|\[\s*\]", "", t)   # parênteses/colchete órfãos
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = _RE_LINHAS_VAZIAS.sub("\n\n", t)
    t = t.strip(" \n·-—")

    # 2) TAMANHO É FUNÇÃO, NÃO PEDIDO.
    # O prompt manda "máximo 3 linhas curtas" desde sempre. Em 03/08 o modelo
    # devolveu três parágrafos sobre Bola de Ouro, premiação em dólar e
    # artilheiro. No celular isso é uma parede de texto — e um mordomo que
    # responde como enciclopédia deixa de parecer mordomo.
    return _cortar_resposta_busca(t)


LIMITE_RESPOSTA_BUSCA = 420     # ~4 linhas no celular
_GANCHO_BUSCA = ("\n\nPosso te ajudar com o que eu faço melhor: guardar "
                 "conta, consulta e lembrete. Quer anotar algo?")


def _cortar_resposta_busca(t: str) -> str:
    """Corta em fronteira de frase, nunca no meio da palavra, e devolve o
    gancho do produto — que o modelo esquece quando se empolga."""
    if not t:
        return ""
    corpo = t
    if len(corpo) > LIMITE_RESPOSTA_BUSCA:
        # tenta cortar no fim da última frase que cabe
        pedaco = corpo[:LIMITE_RESPOSTA_BUSCA]
        corte = max(pedaco.rfind(". "), pedaco.rfind(".\n"),
                    pedaco.rfind("! "), pedaco.rfind("? "))
        corpo = (pedaco[:corte + 1] if corte > 80
                 else pedaco.rsplit(" ", 1)[0] + "…")
    corpo = corpo.strip()
    baixo = corpo.lower()
    if not any(p in baixo for p in ("lembr", "anotar", "anoto", "guardar",
                                    "guardo", "conta", "consulta")):
        corpo += _GANCHO_BUSCA
    return corpo


def _resposta_nao_sei_do_mundo(nome: str) -> dict:
    """Chamado quando a BUSCA FALHOU — não quando o bot não sabe buscar.

    A mensagem antiga dizia "eu não acesso notícia em tempo real". Isso é
    mentira desde o v15: ele acessa. Quando a busca cai por 30 segundos, o
    usuário recebia uma limitação permanente inventada e nunca mais tentava.
    Falha temporária tem que soar temporária.
    """
    return {
        "reply": (f"Minha busca falhou agora, {nome} — e eu prefiro te dizer "
                  f"isso a chutar um resultado errado. Manda de novo daqui a "
                  f"pouco que eu procuro.\n\n"
                  f"Enquanto isso: quer que eu guarde alguma conta, consulta "
                  f"ou lembrete pra você?"),
        "items": [], "needs_decision": False, "mode": "v8_busca_falhou",
    }


def _multi_item(text: str) -> bool:
    """A mensagem parece ter mais de uma coisa pra anotar?
    Heurística por quantidade de números "de conta" (valor ou dia): duas ou
    mais já é risco de o regex clássico perder alguma."""
    numeros = re.findall(r"\d[\d.,]*", text)
    return len(numeros) >= 3 or ("\n" in text.strip() and len(numeros) >= 2)


def _parece_conversa(text: str) -> bool:
    low = text.lower()
    gatilhos = ("ideia", "idéia", "cansad", "triste", "ajuda", "não sei",
                "nao sei", "o que voc", "quem é voc", "quem e voc",
                "como vc", "como você", "e aí", "e ai", "tá bom", "obrigad")
    return any(g in low for g in gatilhos)


def _mordomo_fallback(user_name: str) -> dict:
    return {
        "reply": (f"Tô aqui, {user_name}. 🤝 Meu forte é tirar peso da sua "
                  f"cabeça: me manda uma conta pra eu lembrar, um gasto pra "
                  f"registrar, ou uma consulta pra eu te avisar. O que te "
                  f"ajuda agora?"),
        "items": [],
        "needs_decision": False,
        "mode": "v8_fallback",
    }
