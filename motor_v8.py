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
import re
from typing import Optional

import tempo


_V8_SYSTEM = """Você é o cérebro do "Resolve AI", um mordomo pessoal no WhatsApp que tira da cabeça do usuário contas, lembretes, manutenções e recompras.

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
0b. NÃO PERGUNTE VALOR DE COISA QUE NÃO TEM VALOR. Remédio, consulta médica, dentista, aniversário, reunião, troca de óleo: o que importa é DIA e HORA, não preço. Perguntar "qual o valor?" para "tomar losartana às 7h30" faz você parecer burro e é o tipo de coisa que faz o usuário desistir. Só pergunte valor de conta/boleto/fatura/compra.
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
    """2026-08-02 -> 02/08. O usuário não fala ISO."""
    try:
        a, m, d = str(data_iso).split("-")
        return f"{d}/{m}"
    except Exception:
        return str(data_iso)


def _fmt_itens(itens: list) -> str:
    if not itens:
        return "(nada anotado no momento)"
    linhas = []
    for it in itens:
        partes = [f"id={it.get('id')}", str(it.get("descricao") or "?")]
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


def _preparar_item(novo: dict, ai_engine) -> Optional[dict]:
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
    if not novo.get("categoria"):
        novo["categoria"] = "Outros"
    novo.setdefault("hora_alvo", None)
    novo.setdefault("valor_reais", None)
    novo.setdefault("data_vencimento", None)
    novo.setdefault("recorrencia", None)
    novo.setdefault("link_afiliado",
                    ai_engine.affiliate_link_for(novo.get("descricao", "")))
    # data no passado dispara o alarme na hora e assusta o usuário
    if novo.get("data_vencimento") and _data_passada(novo["data_vencimento"]):
        _registrar_falha(f"data no passado ({novo['data_vencimento']}) em "
                         f"'{novo.get('descricao')}' — descartada")
        novo["data_vencimento"] = None
    return novo


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
            pronto = _preparar_item(novo, ai_engine)
            if pronto:
                result["items"].append(pronto)

        n = _resgatar_valores(result["items"], result["reply"])
        if n:
            _registrar_falha(f"resgatei {n} valor(es) que o LLM deixou de fora "
                             f"do campo (estavam so no texto)")
        result["reply"] = _tirar_pergunta_redundante(result["reply"],
                                                     result["items"])

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
            for novo in corrigido["itens"][:10]:
                pronto = _preparar_item(novo, ai_engine)
                if pronto:
                    result["items"].append(pronto)
            if corrigido.get("reply"):
                result["reply"] = corrigido["reply"]
            intent = "registro"
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
        partes.append("· *" + _br(it["data_vencimento"]) + "*")
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
    ultima = pergunta.lower().lstrip("*_ ")
    # Só mexe em pergunta que PEDE dado ("qual o valor?", "quando vence?").
    # Oferta ("quer que eu te avise um dia antes?") é útil e fica.
    pedido = any(ultima.startswith(p) for p in
                 ("qual", "quais", "quanto", "quando", "me diz", "me informa",
                  "poderia me", "pode me dizer", "voce sabe", "você sabe"))
    if not pedido:
        return reply
    pede_valor = "valor" in ultima or "quanto" in ultima
    pede_data = ("data" in ultima or "vencimento" in ultima
                 or "quando" in ultima)
    tem_valores = all(i.get("valor_reais") is not None for i in itens)
    tem_datas = all(i.get("data_vencimento") for i in itens)
    redundante = ((pede_valor and tem_valores and not pede_data)
                  or (pede_data and tem_datas and not pede_valor)
                  or (pede_valor and pede_data and tem_valores and tem_datas))
    if not redundante:
        return reply
    limpo = corpo[:m.start(1)].rstrip()
    return limpo or reply


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
    "mes", "mês", "hora", "horas", "as", "às", "conta", "boleto", "fatura",
    "lembra", "lembre", "lembrar", "tomar", "pagar", "anota", "anote",
}


def _palavras(txt: str) -> set:
    limpo = re.sub(r"[^\wàáâãéêíóôõúç ]", " ", (txt or "").lower())
    return {p for p in limpo.split() if len(p) > 2 and p not in _STOPWORDS}


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
