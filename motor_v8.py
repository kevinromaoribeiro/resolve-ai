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
1. Se a sua última mensagem terminou com PERGUNTA, a mensagem do usuário é a RESPOSTA dela. Nunca trate como assunto novo.
2. "agendar", "me avisa", "me lembra" = criar/manter lembrete PENDENTE. É o OPOSTO de concluir. Nunca marque como pago quando ele pede para agendar.
3. Nem tudo é para registrar. Sem coisa concreta para guardar, é "conversa".
4. Só use "concluir" quando ele disser claramente que JÁ resolveu/pagou.
5. Nunca invente valor ou data. Nunca escreva "sem data" para o usuário.
6. Tom cordial e curto (1-3 frases), como gente. NUNCA menu numerado ("responda 1 ou 2").

=== ANTECIPAR (é isto que te torna útil) ===
7. CONSUMÍVEL (ração, filtro, remédio, gás, fralda...): se ele comprou algo que acaba e você AINDA NÃO SABE quanto dura, pergunte UMA única vez ("quanto tempo costuma durar?") e guarde em "memoria". Se você JÁ SABE (está nos fatos), não pergunte: calcule a próxima compra em "data_vencimento" e registre o lembrete.
8. MANUTENÇÃO (óleo, revisão, filtro do carro): mesma lógica — pergunte o intervalo uma vez, guarde, use depois.
9. CONTA QUE REPETE TODO MÊS: use "recorrencia":"mensal:DIA" (ex.: "mensal:20"). Para consumível/manutenção por tempo, use "recorrencia":"dias:N".
10. Em "memoria", grave fatos duráveis e reaproveitáveis, com chave curta e estável. Ex.: {{"chave":"racao gatos:dura_dias","valor":"40"}}, {{"chave":"aluguel:dia_vencimento","valor":"05"}}. Não grave conversa fiada.

=== PERGUNTA FORA DO ESCOPO ===
11. NUNCA responda "não sei" nem "não faço isso" e pare aí. Se ele perguntar qualquer coisa — receita, remédio, dúvida do dia a dia, como funciona algo — primeiro RESPONDA de verdade, com o que você sabe, de forma curta e útil.
12. Só DEPOIS de responder, se fizer sentido, puxe a ponte pro que você faz: "quer que eu te lembre disso?", "quer que eu anote esse gasto?". Ponte, não desvio — se não couber, não force.
13. Se ele pedir algo que exige informação que você não tem (preço agora, saldo do banco, resultado de jogo), diga claramente o que não dá e ofereça o caminho mais próximo que você RESOLVE. Nada de resposta vaga ou inventada.
14. Se ele estiver em TESTE GRÁTIS, aproveite para sugerir 1 uso concreto que ele consegue sentir DENTRO dos dias que faltam — algo que dá retorno rápido (conta que vence essa semana, remédio de hoje, consulta do mês). Não sugira coisa que só faz efeito daqui a 3 meses. Uma sugestão por vez, nunca lista.

Formato (SOMENTE o JSON):
{{"intent":"consulta|registro|complemento|resposta|conclusao|conversa",
  "reply":"<mensagem ao usuário>",
  "item": null | {{"tipo":"lembrete|despesa","descricao":"...","valor_reais":null,"data_vencimento":"YYYY-MM-DD"|null,"hora_alvo":"HH:MM"|null,"recorrencia":null|"mensal:DIA"|"dias:N"}},
  "atualizar": null | {{"id":<id>,"campos":{{"valor_reais":0,"data_vencimento":"YYYY-MM-DD","hora_alvo":"HH:MM","descricao":"...","recorrencia":"..."}}}},
  "concluir": null | <id>,
  "memoria": [] | [{{"chave":"...","valor":"..."}}]}}"""


# Intenções em que a regra clássica é confiável E baratíssima (não gasta LLM).
_CLASSICO_CONFIAVEL = {"saudacao", "agradecimento", "capacidades"}


def _fmt_itens(itens: list) -> str:
    if not itens:
        return "(nada anotado no momento)"
    linhas = []
    for it in itens:
        partes = [f"id={it.get('id')}", str(it.get("descricao") or "?")]
        if it.get("valor_reais") is not None:
            partes.append(("R$ %.2f" % it["valor_reais"]).replace(".", ","))
        else:
            partes.append("valor não informado")
        if it.get("data_vencimento"):
            partes.append(f"vence {it['data_vencimento']}")
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
                max_tokens=700,
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
                data = json.loads(bruto)
            except json.JSONDecodeError:
                continue
            if data.get("reply") and data.get("intent"):
                return data
        return None
    except Exception:
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
        # Sem LLM: só assume o que é claramente conversa, pra não estragar o
        # registro que a regra faz bem.
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

    # item novo — só em registro de verdade e sem atualização no mesmo turno
    item = data.get("item")
    if (intent in ("registro", "resposta") and isinstance(item, dict)
            and item.get("descricao") and not result.get("atualizar")):
        item.setdefault("tipo", "lembrete")
        item.setdefault("categoria", "Outros")
        item.setdefault("status", "pendente")
        item.setdefault("hora_alvo", None)
        item.setdefault("valor_reais", None)
        item.setdefault("data_vencimento", None)
        item.setdefault("recorrencia", None)
        item.setdefault("link_afiliado",
                        ai_engine.affiliate_link_for(item.get("descricao", "")))
        result["items"].append(item)

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
