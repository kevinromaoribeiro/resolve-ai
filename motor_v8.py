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

=== O QUE ELE TEM EM ABERTO ===
{itens}

=== O QUE VOCÊ JÁ APRENDEU SOBRE ELE (não pergunte de novo) ===
{fatos}

=== CONVERSA RECENTE (mais antiga primeiro) ===
{historico}

=== SUA TAREFA ===
Interprete a ÚLTIMA mensagem NO CONTEXTO acima e devolva UM JSON.

Intenções:
- "consulta": ele quer SABER algo do que já existe ("o que tenho pra pagar essa semana?", "já paguei a Claro?", "quanto gastei?"). Responda com base nas listas acima. Se não houver nada, diga isso.
- "registro": guardar algo NOVO e concreto.
- "complemento": ele está COMPLETANDO/CORRIGINDO algo que já está na lista — típico de mandar em partes ("são 185 reais", "é dia 20", "na verdade é 200"). Use "atualizar" com o id certo. NÃO crie item novo.
- "resposta": ele está RESPONDENDO uma pergunta sua. Execute o que ele pediu.
- "conclusao": ele avisa que já resolveu/pagou. Use "concluir" com o id.
- "conversa": bate-papo, desabafo, dúvida sobre você. NÃO é para registrar.

=== REGRAS QUE NÃO PODEM SER QUEBRADAS ===
0. REGISTRE PRIMEIRO, PERGUNTE DEPOIS. Se ele pediu para lembrar/anotar algo, crie o item JÁ, mesmo sem valor e sem data — lembrete sem valor continua sendo lembrete. Só depois pergunte o que falta, deixando claro que já está guardado ("Anotado. Qual o valor?"). NUNCA responda só com pergunta sem ter criado nada: é isso que faz a resposta dele virar item duplicado em vez de completar o primeiro.
1. Se a sua última mensagem terminou com PERGUNTA, a mensagem do usuário é a RESPOSTA dela. Nunca trate como assunto novo.
2. "agendar", "me avisa", "me lembra" = criar/manter lembrete PENDENTE. É o OPOSTO de concluir. Nunca marque como pago quando ele pede para agendar.
3. Nem tudo é para registrar. Sem coisa concreta para guardar, é "conversa".
4. Só use "concluir" quando ele disser claramente que JÁ resolveu/pagou.
5. Nunca invente valor ou data. Nunca escreva "sem data", "valor não informado" nem data em formato ISO (2026-08-02) para o usuário — escreva "02/08". Se falta o valor, simplesmente não mencione valor.
6. Tom cordial e curto, como gente. NUNCA menu numerado ("responda 1 ou 2").

=== COMO ESCREVER (é WhatsApp, não e-mail) ===
F1. Uma ideia por linha. Use quebra de linha de verdade (\\n). Bloco de texto corrido cansa e ninguém lê.
F2. Negrito do WhatsApp é *asterisco simples*: *R$ 420,00*, *30/09*, *IPTU*. Use em: valores, datas, nomes de contas e a conclusão da frase. Nunca use ** (isso aparece literal e fica feio).
F3. LISTA (2 itens ou mais): uma linha curta de abertura, linha em branco, e um item por linha começando com "• ". Formato do item: *Nome* — *valor* · vence *dd/mm*. Omita o que não souber, sem escrever "não informado".
   Exemplo:
   Essa semana você tem:

   • *IPTU* — *R$ 420,00* · vence *05/08*
   • *Luz* — vence *02/08*

   Quer que eu te avise um dia antes?
F4. CONFIRMAÇÃO de algo guardado: 1 linha do que foi guardado (com negrito no essencial) + 1 linha do próximo passo ou pergunta. Nunca mais de 3 linhas.
F5. Fale do ponto de vista dele, não do seu processo interno. Diga "*Te aviso em 30/09* pra recomprar", não "preciso criar um lembrete de recompra". Ele não quer saber o que você precisa fazer — quer saber o que já está resolvido.
F6. Emoji: no máximo 1, e só quando couber. Zero emoji é melhor que dois.

=== ANTECIPAR (é isto que te torna útil) ===
7. CONSUMÍVEL (ração, filtro, remédio, gás, fralda, café...): quando ele disser que COMPROU um item desses, é OBRIGATÓRIO:
   a) se a duração NÃO estiver nos fatos acima: sua "reply" TEM que terminar perguntando quanto costuma durar ("Anotado. Quanto tempo costuma durar essa quantidade?"). Não responda só "registrei" — sem essa pergunta você nunca vai conseguir avisar ele de recomprar, que é o motivo de existir.
   b) quando ele responder a duração: grave em "memoria" E crie o lembrete de recompra em "item", com "data_vencimento" = HOJE + a duração que ele disse (calcule a data real, formato YYYY-MM-DD) e "recorrencia":"dias:N". Diga a data na resposta.
   c) se a duração JÁ estiver nos fatos: não pergunte nada, só crie o lembrete com a data calculada.
8. MANUTENÇÃO (óleo, revisão, filtro do carro): mesma lógica do item 7 — pergunte o intervalo uma vez, guarde, e depois use para agendar sozinho.
9. CONTA QUE REPETE TODO MÊS: use "recorrencia":"mensal:DIA" (ex.: "mensal:20"). Para consumível/manutenção por tempo, use "recorrencia":"dias:N".
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
        if it.get("status"):
            partes.append(str(it["status"]))
        linhas.append(" | ".join(partes))
    return "\n".join(linhas)


def _fmt_fatos(fatos: list) -> str:
    if not fatos:
        return "(ainda não sei nada específico sobre ele)"
    return "\n".join(f"- {f.get('chave')}: {f.get('valor')}" for f in fatos)


def _fmt_historico(msgs: list) -> str:
    if not msgs:
        return "(sem conversa anterior)"
    linhas = []
    for m in msgs:
        quem = "usuário" if m.get("direcao") == "in" else "você (assistente)"
        txt = (m.get("preview") or "").strip() or f"[{m.get('tipo') or 'mídia'}]"
        linhas.append(f"{quem}: {txt}")
    return "\n".join(linhas)


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


def _llm(text, nome, itens, fatos, historico, ai_engine, situacao="") -> Optional[dict]:
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
                ] + ([{"role": "user",
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
            and len(text.split()) <= 6):
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

    # itens novos — só em registro de verdade e sem atualização no mesmo turno.
    # É LISTA: "luz 187 dia 12, net 129 dia 15 e seguro 340 dia 20" são 3
    # itens. Quando isto era campo único, o 3º sumia calado.
    if (intent in ("registro", "resposta") and novos
            and not result.get("atualizar")):
        for novo in novos[:10]:
            if not (isinstance(novo, dict) and novo.get("descricao")):
                continue
            novo.setdefault("tipo", "lembrete")
            novo.setdefault("categoria", "Outros")
            novo.setdefault("status", "pendente")
            novo.setdefault("hora_alvo", None)
            novo.setdefault("valor_reais", None)
            novo.setdefault("data_vencimento", None)
            novo.setdefault("recorrencia", None)
            novo.setdefault("link_afiliado",
                            ai_engine.affiliate_link_for(novo.get("descricao", "")))
            result["items"].append(novo)

        n = _resgatar_valores(result["items"], result["reply"])
        if n:
            _registrar_falha(f"resgatei {n} valor(es) que o LLM deixou de fora "
                             f"do campo (estavam so no texto)")

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
