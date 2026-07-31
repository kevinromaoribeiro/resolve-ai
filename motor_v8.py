# -*- coding: utf-8 -*-
"""
motor_v8.py — Camada de entendimento natural ("mordomo").
==========================================================
POR QUE EXISTE:
O roteamento por regex (detect_intent) é rápido e grátis, mas rígido: quando
o usuário foge do script ("me dá uma ideia", "tô cansado", "e aí, como você
funciona mesmo?"), ele caía no default "registro" e tentava criar um item —
gerando respostas erradas ("Lembrete de cobrança em sem data") ou travando
num menu ("responda 1 ou 2").

O V8 é HÍBRIDO:
  1) Regras rápidas resolvem o óbvio (grátis, instantâneo) — quem já funciona
     continua pelo caminho de sempre.
  2) Quando a regra NÃO tem certeza (cairia em "registro"/"vago"), a IA
     classifica a intenção real e, se for conversa, responde como mordomo:
     cordial, ajuda, e traz de volta pro uso (contas/lembretes).

Nada de menu que prende: o mordomo entende linguagem natural sempre.

CONTRATO: expõe `route(user_id, user_name, text, db, ai_engine) -> dict|None`
  - Retorna um result pronto ({"reply","items","needs_decision",...}) quando o
    V8 tratou a mensagem.
  - Retorna None quando é melhor deixar o fluxo clássico seguir (a regra já
    resolve bem: saudação, conclusão, consulta, registro claro de item, etc).
"""
from __future__ import annotations

import json
import re
from typing import Optional

import tempo


# ── Intenções que o V8 assume quando o clássico ficaria perdido ──────────────
# (o clássico continua dono de: saudacao, agradecimento, conclusao, adiar,
#  remover, editar, consulta_gastos, consulta_agenda, registro CLARO de item)
_V8_SYSTEM = """Você é o cérebro de intenção do "Resolve AI", um mordomo pessoal no WhatsApp que tira da cabeça do usuário contas, lembretes, consultas e compras.

Classifique a ÚLTIMA mensagem do usuário em UMA intenção e gere a resposta do mordomo.

Intenções possíveis:
- "registro": o usuário quer que você lembre/registre algo concreto (uma conta, uma compra, uma tarefa, um compromisso). Tem uma AÇÃO ou ITEM claro.
- "conversa": bate-papo, desabafo, pergunta sobre você, pedido de ideia/ajuda genérica, algo sem item concreto pra registrar ("tô cansado", "me dá uma ideia", "como você funciona", "kkk", "e aí").
- "ambiguo": parece querer registrar algo mas falta informação essencial (o quê exatamente, ou quando).

REGRAS DO MORDOMO (tom):
- Cordial, leve, prestativo. Fala como gente, não como robô. Curto (1-3 frases).
- NUNCA use menu numerado ("responda 1 ou 2"). Entenda a resposta em linguagem natural.
- Em "conversa": responda de leve E traga de volta pro uso, com um exemplo concreto do que você faz. Ex.: se disser "tô cansado" → acolhe rápido e oferece tirar algo da cabeça dele.
- Em "ambiguo": registre o que der e pergunte SÓ o que falta, em uma frase natural. Nunca invente data. Nunca escreva "sem data" na resposta ao usuário.
- Nunca diga "Lembrete de cobrança" para algo que não é cobrança. Um lembrete comum é só "lembrete".

Responda SOMENTE um JSON:
{"intent":"registro|conversa|ambiguo","reply":"<mensagem do mordomo>","item":null | {"tipo":"lembrete|despesa","descricao":"...","valor_reais":null,"data_vencimento":null,"hora_alvo":null}}

Data de hoje: {today}. Hora agora: {now} (fuso Brasil).
Se houver "daqui X min/horas" ou "às HH:MM", calcule hora_alvo. Se houver data, preencha data_vencimento (YYYY-MM-DD). Caso contrário, deixe null."""


def _should_defer_to_classic(text: str, classic_intent: str) -> bool:
    """True = deixa o fluxo clássico tratar (ele já faz bem).
    False = o V8 assume (o clássico erraria)."""
    # o clássico é confiável nesses; não interfira
    confiaveis = {"saudacao", "agradecimento", "capacidades", "conclusao",
                  "adiar", "remover", "editar", "consulta_gastos",
                  "consulta_agenda"}
    if classic_intent in confiaveis:
        return True
    return False


def _llm_classify(text: str, user_name: str, ai_engine) -> Optional[dict]:
    """Chama o LLM para classificar intenção + gerar reply de mordomo.
    Reusa a stack de litellm do ai_engine. None se indisponível."""
    try:
        from litellm import completion
    except Exception:
        return None
    try:
        system = (_V8_SYSTEM
                  .replace("{today}", tempo.hoje().isoformat())
                  .replace("{now}", tempo.agora().strftime("%H:%M")))
        for attempt in range(2):
            resp = completion(
                model=getattr(ai_engine, "LLM_MODEL", "gpt-4o-mini"),
                max_tokens=400,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content":
                        f"Usuário {user_name} disse: {text!r}"},
                ] + ([{"role": "user", "content":
                       "Responda SOMENTE o JSON pedido."}] if attempt else []),
            )
            raw = resp.choices[0].message.content
            raw = re.sub(r"```(?:json)?|```", "", raw).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("reply") and data.get("intent"):
                return data
        return None
    except Exception:
        return None


def route(user_id, user_name, text, db, ai_engine) -> Optional[dict]:
    """Ponto de entrada do V8. Decide se assume a mensagem ou devolve ao
    fluxo clássico. Ver contrato no topo do arquivo."""
    text = (text or "").strip()
    if not text:
        return None

    classic_intent = ai_engine.detect_intent(text)

    # 1) Casos que o clássico trata bem: não interfere.
    if _should_defer_to_classic(text, classic_intent):
        return None

    # 2) Caiu em "registro"/"vago". Antes de deixar o clássico tentar criar
    #    item (e às vezes errar), pergunta pra IA o que é de verdade.
    data = _llm_classify(text, user_name, ai_engine)

    # 2a) Sem IA disponível (API off/sem chave): fallback seguro por regra.
    if data is None:
        # se claramente vago/conversa, responde como mordomo simples em vez de
        # tentar registrar lixo.
        if classic_intent == "vago" or _parece_conversa(text):
            return _mordomo_fallback(user_name)
        return None  # registro real: deixa o clássico/_build_item resolver

    intent = data.get("intent")
    reply = data.get("reply", "").strip()
    item = data.get("item")

    result = ai_engine._base_result(mode="v8")
    result["reply"] = reply

    # registro com item concreto: persiste
    if intent == "registro" and item:
        item.setdefault("tipo", "lembrete")
        item.setdefault("categoria", "Outros")
        item.setdefault("status", "pendente")
        item.setdefault("hora_alvo", None)
        item.setdefault("valor_reais", None)
        item.setdefault("data_vencimento", None)
        item.setdefault("link_afiliado",
                        ai_engine.affiliate_link_for(item.get("descricao", "")))
        result["items"].append(item)

    # conversa / ambiguo: só a reply do mordomo (nada a persistir)
    return result


def _parece_conversa(text: str) -> bool:
    low = text.lower()
    gatilhos = ("ideia", "idéia", "cansad", "triste", "ajuda", "não sei",
                "nao sei", "o que voc", "quem é voc", "quem e voc",
                "como vc", "como você", "e aí", "e ai", "tá bom", "obrigad")
    return any(g in low for g in gatilhos)


def _mordomo_fallback(user_name: str) -> dict:
    """Resposta de mordomo quando não há IA e a msg parece conversa.
    Acolhe e traz de volta pro uso — sem menu, sem 'não entendi'."""
    return {
        "reply": (f"Tô aqui, {user_name}. 🤝 Meu forte é tirar peso da sua "
                  f"cabeça: me manda uma conta pra eu lembrar, um gasto pra "
                  f"registrar, ou uma consulta pra eu te avisar. O que te "
                  f"ajuda agora?"),
        "items": [],
        "needs_decision": False,
        "mode": "v8_fallback",
    }
