"""
ai_engine.py — Motor de Ingestão AI do RESOLVE AI.

Dois modos de operação:
1. MODO LLM: se houver OPENAI_API_KEY ou ANTHROPIC_API_KEY no .env / ambiente,
   usa gpt-4o-mini ou claude-3-haiku via litellm para extrair entidades em JSON.
2. MODO MOCK (Simulação Inteligente): sem chave de API, usa Regex e regras
   determinísticas. A interface funciona 100% sem travar.

Contrato de saída (dict):
{
    "reply": str,              # mensagem do bot para o chat
    "items": [                 # 0..N itens estruturados p/ salvar no SQLite
        {"tipo": ..., "categoria": ..., "descricao": ...,
         "valor_reais": ..., "data_vencimento": ..., "status": ...,
         "link_afiliado": ...}
    ],
    "needs_decision": bool,    # True quando devolvemos menu numerado (regra de ouro)
    "pending_payload": dict|None,  # item aguardando decisão do usuário (1/2)
    "mode": "llm" | "mock",
}
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
import tempo
from typing import Optional

# ---------------------------------------------------------------------------
# Configuração de modo
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Loader minimalista de .env (evita dependência extra)."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_AVAILABLE = bool(OPENAI_KEY or ANTHROPIC_KEY)
LLM_MODEL = "gpt-4o-mini" if OPENAI_KEY else "claude-3-haiku-20240307"

AFFILIATE_TAG = "resolveai-20"

# Palavras que indicam item físico de reposição -> aciona 1-Click Buy
REPLENISHABLE_KEYWORDS = {
    "ração": "racao",
    "racao": "racao",
    "areia": "areia+gato",
    "óleo": "oleo+motor",
    "oleo": "oleo+motor",
    "filtro": "filtro",
    "refil": "refil+filtro",
    "shampoo": "shampoo",
    "fralda": "fralda",
    "café": "cafe",
    "cafe": "cafe",
    "gás": "botijao+gas",
    "gas": "botijao+gas",
}

CATEGORY_KEYWORDS = {
    "Saúde": ["farmácia", "farmacia", "remédio", "remedio", "consulta",
              "médico", "medico", "dentista", "exame", "vacina humana",
              "psicólogo", "psicologo", "academia", "plano de saúde",
              "plano de saude", "hospital", "dermato", "cardiologista"],
    "Casa": ["gás", "gas", "botijão", "botijao", "faxina", "diarista",
             "encanador", "eletricista", "reforma", "móvel", "movel",
             "manutenção casa", "conserto", "lâmpada", "lampada",
             "filtro de água", "filtro de agua", "purificador"],
    "Pet": ["ração", "racao", "areia", "petz", "veterinário", "veterinario",
            "vacina", "gato", "cachorro", "pet", "antipulgas"],
    "Veículo": ["óleo", "oleo", "carro", "km", "revisão", "revisao", "pneu",
                "ipva", "licenciamento", "mecânico", "mecanico", "gasolina",
                "combustível", "combustivel", "troca de óleo", "alinhamento"],
    "Contas": ["luz", "energia", "água", "agua", "internet", "boleto",
               "fatura", "conta", "aluguel", "condomínio", "condominio",
               "cartão", "cartao", "iptu", "telefone", "celular"],
    "Alimentação": ["mercado", "supermercado", "feira", "comida", "almoço",
                    "almoco", "jantar", "ifood", "padaria", "café", "cafe",
                    "restaurante", "pizza", "lanche"],
    "Lazer": ["cinema", "show", "viagem", "streaming", "netflix", "spotify",
              "jogo", "playstation", "xbox", "bar", "parque"],
}

MONTHS_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

FILLER_RE = re.compile(
    r"\b(ééé+|eee+|hmm+|hum+|né|ne\b|tipo assim|então,?|entao,?|aí,?|ai,?|"
    r"bom,?|olha só|olha so|deixa eu ver)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Utilitários de extração (Mock)
# ---------------------------------------------------------------------------

def clean_audio_transcript(text: str) -> str:
    """Remove cacoetes de fala de uma transcrição informal."""
    cleaned = FILLER_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,.")


# ---------------------------------------------------------------------------
# CAMADA DE COMPREENSÃO BR (v6.2)
# Normaliza abreviações e gírias brasileiras ANTES de qualquer parsing.
# Beneficia o Mock (regex acerta mais) e o LLM (texto mais limpo, menos token).
# ---------------------------------------------------------------------------

_BR_ABBREV = {
    # internetês / abreviações (só as inequívocas, casadas por palavra inteira)
    "vc": "você", "vcs": "vocês", "tb": "também", "tbm": "também",
    "hj": "hoje", "amn": "amanhã", "qdo": "quando", "qnd": "quando",
    "dps": "depois", "agr": "agora", "pq": "porque", "q": "que",
    "n": "não", "nao": "não", "msm": "mesmo", "mto": "muito", "mt": "muito",
    "td": "tudo", "nd": "nada", "ngm": "ninguém", "cmg": "comigo",
    "obg": "obrigado", "pfv": "por favor", "pfvr": "por favor",
    "fds": "fim de semana", "sla": "sei lá", "vdd": "verdade",
    "blz": "beleza", "vlw": "valeu", "flw": "falou",
    "amanha": "amanhã", "sabado": "sábado", "proximo": "próximo",
    "proxima": "próxima", "tds": "todos",
}

_BR_MONEY_SLANG_RE = re.compile(
    r"\b(\d+(?:[.,]\d{1,2})?)\s*(?:conto|contos|pila|pilas|mango|mangos|"
    r"pau|paus|prata|pratas|real|reais|dinheiros?)\b", re.IGNORECASE)

_BR_ABBREV_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _BR_ABBREV) + r")\b",
    re.IGNORECASE)


_NUM_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
    "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20,
    "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60,
    "setenta": 70, "oitenta": 80, "noventa": 90,
    "cem": 100, "cento": 100, "duzentos": 200, "trezentos": 300,
    "quatrocentos": 400, "quinhentos": 500, "seiscentos": 600,
    "setecentos": 700, "oitocentos": 800, "novecentos": 900, "mil": 1000,
}
_NUM_SEQ_RE = re.compile(
    r"\b((?:" + "|".join(_NUM_WORDS) + r")(?:\s+(?:e\s+)?(?:" +
    "|".join(_NUM_WORDS) + r"))*)\b", re.IGNORECASE)
_MONEY_CTX_RE = re.compile(
    r"(paguei|gastei|custou|custa|devo|recebi|foi|deu|é de|no valor|"
    r"parcela|aluguel|reais|conto|pila|mango)", re.IGNORECASE)


def _extenso_para_num(seq: str) -> int:
    total, atual = 0, 0
    for w in re.split(r"\s+e\s+|\s+", seq.lower()):
        v = _NUM_WORDS.get(w)
        if v is None:
            continue
        if v == 1000:
            atual = (atual or 1) * 1000
            total += atual
            atual = 0
        else:
            atual += v
    return total + atual


def normalize_br(text: str) -> str:
    """Expande abreviações, gírias e números por extenso do português BR.
    '50 conto' -> 'R$ 50' | 'mil e duzentos' -> 'R$ 1200' (em contexto de $)."""
    t = _BR_MONEY_SLANG_RE.sub(lambda m: f"R$ {m.group(1)}", text)
    t = _BR_ABBREV_RE.sub(lambda m: _BR_ABBREV[m.group(1).lower()], t)
    if _MONEY_CTX_RE.search(t):
        def _conv(m):
            n = _extenso_para_num(m.group(1))
            return f"R$ {n}" if n >= 10 else m.group(1)  # "uma consulta" fica
        t = _NUM_SEQ_RE.sub(_conv, t)
        t = re.sub(r"R\$\s*(\d+)\s*(?:reais|conto|pila)", r"R$ \1", t)
    return t


def extract_money(text: str) -> Optional[float]:
    """Extrai o primeiro valor monetário (R$ 145,00 | 145 reais | 89.90)."""
    patterns = [
        r"r\$\s*([\d.]+,\d{2})",          # R$ 1.234,56
        r"r\$\s*(\d+(?:[.,]\d{1,2})?)",   # R$ 145 / R$ 145,5
        r"(\d+(?:[.,]\d{1,2})?)\s*(?:reais|conto|pila)",
        # "paguei 250 no mercado" — número logo após verbo de pagamento
        r"(?:paguei|gastei|comprei\s+por|custou|foi|deu)\s+(\d+(?:[.,]\d{1,2})?)\b",
        # "luz 187 vence dia 12" — número imediatamente antes de vence/venc
        r"\b(\d{1,6}(?:[.,]\d{2})?)\s+(?:vence|venc\b)",
        # "50 de luz" / "30 na farmácia" — número + preposição + substantivo,
        # exceto datas ("dia 20 de julho") e horários ("02:54", "14h30")
        r"(?<!dia\s)(?<!:)(?<![0-9])(?<!h)\b(\d+(?:[.,]\d{1,2})?)\s+(?:de|do|da|em|no|na)\s+"
        r"(?!janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|"
        r"setembro|outubro|novembro|dezembro)[a-zà-ú]",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            if "," in raw and "." in raw:      # 1.234,56
                raw = raw.replace(".", "").replace(",", ".")
            elif "," in raw:                   # 145,00
                raw = raw.replace(",", ".")
            try:
                return round(float(raw), 2)
            except ValueError:
                continue
    return None


_WEEKDAYS = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3,
             "sexta": 4, "sabado": 5, "domingo": 6}


def extract_due_date(text: str, ref: Optional[date] = None) -> Optional[str]:
    """Extrai data de vencimento explícita ou implícita. Retorna ISO ou None."""
    ref = ref or date.today()
    low = text.lower()

    # dd/mm/yyyy ou dd/mm
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", low)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = m.group(3)
        year = int(y) + 2000 if y and len(y) == 2 else int(y) if y else ref.year
        try:
            parsed = date(year, mo, d)
            if not y and parsed < ref:  # sem ano e já passou -> próximo ano
                parsed = date(year + 1, mo, d)
            return parsed.isoformat()
        except ValueError:
            pass

    # "dia 20 de julho" / "dia 20" — mas NÃO "dia 7h" (hora) nem "todo dia 5"
    m = re.search(r"(?<!todo\s)(?<!toda\s)dia\s+(\d{1,2})(?!\s*h\b)(?!\d)"
                  r"(?:\s+de\s+([a-zç]+))?", low)
    if m:
        d = int(m.group(1))
        mo = MONTHS_PT.get(m.group(2) or "", ref.month)
        try:
            parsed = date(ref.year, mo, d)
            if parsed < ref:
                parsed = date(ref.year + (1 if mo == 12 else 0),
                              mo % 12 + 1 if not m.group(2) else mo, d) \
                    if not m.group(2) else date(ref.year + 1, mo, d)
            return parsed.isoformat()
        except ValueError:
            pass

    # Dias da semana: "sexta", "próxima terça", "quinta que vem"
    m = re.search(r"\b(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|"
                  r"domingo)(?:-feira)?\b", low)
    if m:
        alvo = _WEEKDAYS[m.group(1).replace("ç", "c").replace("á", "a")]
        delta = (alvo - ref.weekday()) % 7 or 7
        return (ref + timedelta(days=delta)).isoformat()

    # Relativos por DIA
    if "depois de amanhã" in low or "depois de amanha" in low:
        return (ref + timedelta(days=2)).isoformat()
    if "amanhã" in low or "amanha" in low:
        return (ref + timedelta(days=1)).isoformat()
    m = re.search(r"(?:em|daqui(?:\s+a)?)\s+(\d+)\s+dias?", low)
    if m:
        return (ref + timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(?:em|daqui(?:\s+a)?)\s+(\d+)\s+semanas?", low)
    if m:
        return (ref + timedelta(weeks=int(m.group(1)))).isoformat()
    if "semana que vem" in low or "próxima semana" in low or "proxima semana" in low:
        return (ref + timedelta(days=7)).isoformat()
    if "mês que vem" in low or "mes que vem" in low:
        return (ref + timedelta(days=30)).isoformat()

    # Relativos por HORA/MINUTO ou "hoje" -> a data é HOJE
    if re.search(r"(?:daqui(?:\s+a)?|em)\s+\d+\s*(?:min|minuto|h\b|hora)", low) \
            or re.search(r"\bhoje\b", low) or re.search(r"\bagora\b", low) \
            or re.search(r"[àa]s?\s+\d{1,2}[:h]", low) \
            or re.search(r"\b\d{1,2}h(?:\d{2})?\b", low):
        return ref.isoformat()
    return None


def extract_due_time(text: str, ref: Optional[datetime] = None) -> Optional[str]:
    """Extrai HORÁRIO-alvo (HH:MM) de expressões relativas/absolutas.
    Só reconhece hora quando há marcador explícito (min/hora/h/às) —
    nunca infere hora a partir de um número solto (evita confundir 'dia 15')."""
    ref = ref or tempo.agora()
    low = text.lower()

    # relativo: "daqui 30 min", "daqui a 30min", "em 45 minutos"
    m = re.search(r"(?:daqui(?:\s+a)?|em)\s+(\d+)\s*min(?:uto)?s?\b", low)
    if m:
        return (ref + timedelta(minutes=int(m.group(1)))).strftime("%H:%M")
    # relativo: "daqui 2 horas", "em 3h"
    m = re.search(r"(?:daqui(?:\s+a)?|em)\s+(\d+)\s*(?:horas?|h)\b", low)
    if m:
        return (ref + timedelta(hours=int(m.group(1)))).strftime("%H:%M")
    # absoluto com "às": "às 14h", "às 14:30", "as 9"
    m = re.search(r"[àa]s\s+(\d{1,2})(?:[:h](\d{2}))?", low)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    # absoluto "14h", "14h30" — exige o 'h' colado (não casa datas dd/mm)
    m = re.search(r"\b(\d{1,2})h(\d{2})?\b", low)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    return None


def classify_category(text: str) -> str:
    low = text.lower()
    scores = {cat: sum(1 for kw in kws
                       if re.search(rf"\b{re.escape(kw)}\b", low))
              for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else "Outros"


def affiliate_link_for(text: str) -> Optional[str]:
    low = text.lower()
    for kw, query in REPLENISHABLE_KEYWORDS.items():
        if kw in low:
            return (f"https://www.mercadolivre.com.br/jm/search?"
                    f"as_word={query}&tag={AFFILIATE_TAG}")
    return None


def infer_tipo(text: str, has_value: bool, has_date: bool) -> str:
    low = text.lower()
    paid_markers = ["paguei", "comprei", "gastei", "pago"]
    remind_markers = ["lembra", "lembrar", "lembrete", "agendar", "agenda",
                      "vence", "vencimento", "não esquecer", "nao esquecer",
                      "marcar", "avisa", "avise"]
    if any(w in low for w in remind_markers) or (has_date and not any(w in low for w in paid_markers)):
        return "lembrete"
    if any(w in low for w in paid_markers) or has_value:
        return "despesa"
    return "lembrete"


def _fmt_br(iso: Optional[str]) -> str:
    if not iso:
        return "sem data"
    y, m, d = iso.split("-")
    return f"{d}/{m}"


def _is_today(iso: Optional[str]) -> bool:
    return bool(iso) and iso == date.today().isoformat()


def _reminder_reply(item: dict, user_name: str) -> str:
    """Resposta de lembrete: limpa, com horário quando houver, e honesta
    sobre o limite atual (disparo fino intraday ainda não existe)."""
    desc = item.get("descricao") or "isso"
    hora = item.get("hora_alvo")
    venc = item.get("data_vencimento")

    if not venc and not hora:
        # Sem quando: registra mas pede a data em vez de mentir "sem data".
        return (f"Anotei: *{desc}*. Só me diz *quando* que eu programo "
                f"o lembrete — ex.: \"hoje 18h\", \"amanhã\", \"dia 20\".")

    if hora and _is_today(venc):
        return (f"Anotado, {user_name}: *{desc}* hoje às {hora}. ⏰ "
                f"Te aviso na hora — responda *feito* quando resolver, "
                f"ou *adiar 1h* se precisar.")

    quando = _fmt_br(venc) + (f" às {hora}" if hora else "")
    return f"Anotado, {user_name}. Vou te lembrar de *{desc}* em {quando}. ✅"


_CMD_PREFIX_RE = re.compile(
    r"^(?:(?:você|voce|tu)\s+)?(?:me\s+)?(?:lembr[ae](?:r|-me)?\s+(?:de\s+|da\s+|do\s+|que\s+)?|lembret[ei]\s+(?:de\s+|da\s+|do\s+)?|"
    r"avis[ae](?:r|-me)?\s+(?:que\s+|de\s+|pra\s+)?|"
    r"agenda(?:r)?\s+|marca(?:r)?\s+(?:que\s+|de\s+)?|anota(?:r)?\s+(?:que\s+|a[íi]\s+)?|n[ãa]o\s+(?:me\s+)?deixa\s+esquecer\s+(?:de\s+|da\s+|do\s+)?)",
    re.IGNORECASE,
)
_DATE_PHRASE_RE = re.compile(
    r"\s*(?:no\s+|em\s+|para\s+o?\s*)?"
    r"(?:dia\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?"      # dia 10/11[/2026]
    r"|dia\s+\d{1,2}(?:\s+de\s+[a-zç]+)?"          # dia 20 [de julho]
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"               # 10/11[/2026]
    r"|amanhã|amanha|hoje|agora|semana\s+que\s+vem|m[eê]s\s+que\s+vem"
    r"|(?:daqui(?:\s+a)?|em)\s+\d+\s*(?:dias?|semanas?|min(?:uto)?s?|h(?:ora)?s?)"
    r"|[àa]s\s+\d{1,2}(?:[:h]\d{0,2})?"            # às 14h / às 14:30 (exige 's')
    r"|\b(?:segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)(?:-feira)?"
    r"|\d{1,2}h\d{2}?)\s*",                        # 14h20
    re.IGNORECASE,
)


_MONEY_PHRASE_RE = re.compile(
    r"\s*(?:uns|umas|por)?\s*(?:r\$\s*[\d.,]+|\d+(?:[.,]\d{1,2})?\s*"
    r"(?:reais|conto|pila|mango))\s*",
    re.IGNORECASE,
)
_DANGLING_RE = re.compile(
    r"(?:^\s*(?:que|e|a[ií])\s+)|(?:\b(?:vence(?:ndo)?|vencimento|venc|uns|"
    r"umas)\b\s*$)", re.IGNORECASE)


_PAY_PREFIX_RE = re.compile(
    r"^(?:paguei|gastei|comprei|quitei)\s+(?:r\$\s*)?\d*(?:[.,]\d{1,2})?"
    r"\s*(?:de|do|da|em|no|na)?\s*",
    re.IGNORECASE,
)


def _summarize(text: str, limit: int = 60) -> str:
    """Limpa verbos de comando/pagamento, datas e valores da descrição."""
    t = _CMD_PREFIX_RE.sub("", text.strip())
    t = _PAY_PREFIX_RE.sub("", t)
    t = _DATE_PHRASE_RE.sub(" ", t)
    t = _MONEY_PHRASE_RE.sub(" ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,.-?!")
    t = _DANGLING_RE.sub("", t).strip(" ,.-?!")
    t = re.sub(r"^(?:d[aeo]s?|n[ao]s?)\s+", "", t)
    if not t:
        t = text.strip().rstrip(".")
    return t if len(t) <= limit else t[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Handlers por tipo de entrada (MOCK)
# ---------------------------------------------------------------------------

def _base_result(mode: str = "mock") -> dict:
    return {"reply": "", "items": [], "needs_decision": False,
            "pending_payload": None, "mode": mode}


def _build_item(text: str) -> dict:
    valor = extract_money(text)
    venc = extract_due_date(text)
    hora = extract_due_time(text)
    categoria = classify_category(text)
    tipo = infer_tipo(text, valor is not None, venc is not None or hora is not None)
    status = "concluido" if (tipo == "despesa" and re.search(
        r"\b(paguei|pago|comprei|gastei)\b", text, re.IGNORECASE)) else "pendente"
    desc_limpa = _summarize(text)
    if valor is not None:
        v_int = str(int(valor))
        v_dec = f"{valor:.2f}".replace(".", ",")
        desc_limpa = re.sub(rf"\b(?:{re.escape(v_int)}|{re.escape(v_dec)})\b",
                            "", desc_limpa)
        desc_limpa = re.sub(r"\s{2,}", " ", desc_limpa).strip(" ,.-") or _summarize(text)
    # Se há hora mas o parser de data não marcou nada, o alvo é hoje.
    if hora and not venc:
        venc = date.today().isoformat()
    return {
        "tipo": tipo,
        "categoria": categoria,
        "descricao": desc_limpa,
        "valor_reais": valor,
        "data_vencimento": venc,
        "hora_alvo": hora,
        "recorrencia": None,
        "status": status,
        "link_afiliado": affiliate_link_for(text),
    }


_SPAM_RE = re.compile(
    r"https?://|www\.|\bclique\b|imperd[ií]vel|\bpromoç|voc[eê] foi selecionad|"
    r"pr[eê]mio|^fwd:|% ?off\b", re.IGNORECASE)
_INTENT_VERB_RE = re.compile(
    r"\b(pag(ar|uei|o)|lembr|avis|anot|compr|gast|vence|venc|consulta|reuni[aã]o|"
    r"parcela|boleto|conta|fatura|aluguel|mensalidade|exame|prova|anivers|"
    r"buscar|levar|ligar|renovar|entrega|receb|dev(o|endo)|marcar|agendar)",
    re.IGNORECASE)
_RECUR_RE = re.compile(
    r"tod[oa]s?\s+(?:o\s+|a\s+)?(dia|m[eê]s|semana|segunda|ter[çc]a|quarta|"
    r"quinta|sexta|s[áa]bado|domingo)|de\s+\d+\s+em\s+\d+\s+horas?|"
    r"\d+x\s+por\s+(semana|m[eê]s)|por\s+m[eê]s\b", re.IGNORECASE)
_RECUR_NOTE = ("\n_Esse se repete, né? Por enquanto eu anoto a próxima vez e, "
               "quando ela passar, você me manda de novo — recorrência "
               "automática já tá no forno._ 🔁")


def _next_monthday(d: int, ref: Optional[date] = None) -> str:
    ref = ref or date.today()
    try:
        cand = date(ref.year, ref.month, d)
    except ValueError:
        cand = date(ref.year + (ref.month == 12), ref.month % 12 + 1, min(d, 28))
    if cand <= ref:
        y, m2 = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
        try:
            cand = date(y, m2, d)
        except ValueError:
            cand = date(y, m2, 28)
    return cand.isoformat()


def handle_text(text: str, user_name: str = "Kevin") -> dict:
    res = _base_result()

    # --- filtro anti-spam (v6.4): encaminhado/promoção não vira lembrete ---
    if _SPAM_RE.search(text):
        res["reply"] = ("Isso parece mensagem encaminhada ou promoção, então "
                        "não anotei. 😉 Se for algo seu mesmo, me manda "
                        "começando com *anota:* que eu guardo.")
        return res

    item = _build_item(text)

    # pronome solto como descrição ("tudo vence dia 15") -> pergunta qual
    if item["descricao"].lower().strip() in ("tudo", "isso", "esse", "essa",
                                             "todos", "todas", "ele", "ela"):
        res["reply"] = ("Qual deles exatamente? Me manda item por item "
                        "(ex.: _\"luz 180 vence dia 15\"_) que eu anoto "
                        "cada um certinho.")
        return res

    # --- filtro de plausibilidade (v6.4): sem NENHUM sinal, não grava lixo --
    tem_sinal = (item["valor_reais"] is not None or item["data_vencimento"]
                 or item.get("hora_alvo") or item["categoria"] != "Outros"
                 or _INTENT_VERB_RE.search(text))
    if not tem_sinal:
        res["reply"] = (f"Hmm, não identifiquei conta, data nem valor em "
                        f"_\"{text.strip()[:60]}\"_. Se for algo pra anotar, "
                        f"me manda com um pouco mais de detalhe — ou começa "
                        f"com *anota:* que eu guardo do jeito que vier.")
        return res

    # --- recorrência (v6.5): agora é DE VERDADE — o item rola sozinho -------
    recur = _RECUR_RE.search(text)
    nota_recur = ""
    if recur:
        low = text.lower()
        m = re.search(r"tod[oa]s?\s+(?:o\s+)?dia\s+(\d{1,2})\b(?!\s*h)", low)
        mh = re.search(r"de\s+(\d+)\s+em\s+\d+\s+horas?", low)
        mw = re.search(r"tod[oa]\s+(segunda|ter[çc]a|quarta|quinta|sexta|"
                       r"s[áa]bado|domingo)", low)
        if m:      # "todo dia 10" -> mensal
            item["recorrencia"] = f"mensal:{int(m.group(1))}"
            item["data_vencimento"] = _next_monthday(int(m.group(1)))
            nota_recur = f"\n🔁 _Repito todo mês no dia {int(m.group(1))}._"
        elif mh:   # "de 8 em 8 horas"
            item["recorrencia"] = f"horas:{int(mh.group(1))}"
            prox = tempo.agora() + timedelta(hours=int(mh.group(1)))
            item["hora_alvo"] = prox.strftime("%H:%M")
            item["data_vencimento"] = prox.date().isoformat()
            nota_recur = f"\n🔁 _Repito de {mh.group(1)} em {mh.group(1)} horas._"
        elif mw:   # "toda terça"
            wd = _WEEKDAYS[mw.group(1).replace("ç", "c").replace("á", "a")]
            item["recorrencia"] = f"semanal:{wd}"
            nota_recur = f"\n🔁 _Repito toda {mw.group(1)}._"
        elif item.get("hora_alvo"):  # "todo dia 21h"
            item["recorrencia"] = "diaria"
            hoje_ainda = item["hora_alvo"] > tempo.agora().strftime("%H:%M")
            item["data_vencimento"] = (date.today() if hoje_ainda
                                       else date.today() + timedelta(days=1)
                                       ).isoformat()
            nota_recur = f"\n🔁 _Repito todo dia às {item['hora_alvo']}._"
        elif re.search(r"todo\s+m[eê]s|por\s+m[eê]s", low) and item["data_vencimento"]:
            d = int(item["data_vencimento"][8:10])
            item["recorrencia"] = f"mensal:{d}"
            nota_recur = f"\n🔁 _Repito todo mês no dia {d}._"
        else:
            nota_recur = _RECUR_NOTE  # padrão não mapeado: honestidade antiga
        item["descricao"] = re.sub(
            r"\s*(tod[oa]s?\s+(?:o\s+)?(dia|m[eê]s|semana|segunda|ter[çc]a|"
            r"quarta|quinta|sexta|s[áa]bado|domingo)|de\s+\d+\s+em\s+"
            r"\d+\s+horas?|\d+x\s+por\s+\w+|por\s+m[eê]s)\s*", " ",
            item["descricao"], flags=re.IGNORECASE).strip(" ,.-") \
            or item["descricao"]

    res["items"].append(item)
    if item["tipo"] == "lembrete":
        res["reply"] = _reminder_reply(item, user_name) + nota_recur
    else:
        valor_txt = (f"R$ {item['valor_reais']:.2f}".replace(".", ",")
                     if item["valor_reais"] else "valor não informado")
        res["reply"] = (f"Registrado, {user_name}. Despesa de {valor_txt} "
                        f"em {item['categoria']}." + nota_recur)
    return res


def handle_audio(transcript: str, user_name: str = "Kevin") -> dict:
    cleaned = clean_audio_transcript(transcript)
    res = handle_text(cleaned, user_name)
    item = res["items"][0]
    res["reply"] += (f"\n\n_Áudio processado e categorizado em "
                     f"{item['tipo'].capitalize()}s/{item['categoria']}._")
    return res


def handle_image_with_text(ocr_content: str, instruction: str,
                           user_name: str = "Kevin") -> dict:
    """Imagem + instrução: extrai dados do OCR e executa o comando."""
    res = _base_result()
    combined = f"{ocr_content}. {instruction}"
    item = _build_item(combined)

    low_instr = instruction.lower()
    if re.search(r"\b(pago|paguei|arquiv)", low_instr):
        item["tipo"], item["status"] = "despesa", "concluido"
    elif re.search(r"\b(agend|lembr|avis)", low_instr):
        item["tipo"], item["status"] = "lembrete", "pendente"

    # v6: NUNCA gravar dado extraído por visão sem confirmação do usuário.
    res["needs_decision"] = True
    res["pending_payload"] = {**item, "_confirm": item["tipo"]}
    res["reply"] = _confirm_msg(item, user_name)
    return res


def _confirm_msg(item: dict, user_name: str) -> str:
    """Mensagem de confirmação dos dados extraídos por visão (botões 1/2)."""
    valor_txt = (f"R$ {item['valor_reais']:.2f}".replace(".", ",")
                 if item["valor_reais"] else "valor não identificado")
    venc_txt = _fmt_br(item.get("data_vencimento"))
    acao = ("arquivar como *despesa paga*" if item["tipo"] == "despesa"
            else "agendar *lembrete* um dia antes do vencimento")
    saud = f"{user_name}, " if user_name else ""
    return (f"Li aqui, {saud}*{item['descricao']}* — {valor_txt} — "
            f"vencimento {venc_txt}. Vou {acao}.\n\n"
            f"Tá certo?\n"
            f"*1* ✅ Sim, pode salvar\n"
            f"*2* ✏️ Corrigir (me manda o dado certo, ex.: "
            f"_\"valor 210,50 vence 25/07\"_)")


def _correction_money(text: str) -> Optional[float]:
    """Extrai valor de uma frase de correção mesmo sem 'R$'/'reais'.
    Ignora dígitos que fazem parte de datas (25/07, 25-07 etc.)."""
    val = extract_money(text)
    if val is not None:
        return val
    no_dates = re.sub(r"\b\d{1,2}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?\b", " ", text)
    m = re.search(r"\b(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2})\b",
                  no_dates)
    if m:
        return float(m.group(1).replace(".", "").replace(",", "."))
    m = re.search(r"\b(\d{2,6})\b", no_dates)  # inteiro solto (ex.: 'valor 300')
    return float(m.group(1)) if m else None


_CORRECTION_STOPWORDS = {"valor", "vence", "vencimento", "venc", "dia",
                         "r$", "rs", "reais", "real", "é", "e", "o", "a",
                         "no", "na", "em", "de", "do", "da", "para", "pra"}


def _merge_correction(old: dict, correction_text: str) -> dict:
    """Reaproveita o item extraído, sobrescrevendo só o que o usuário corrigiu."""
    merged = dict(old)
    val = _correction_money(correction_text)
    if val is not None:
        merged["valor_reais"] = val
    venc = extract_due_date(correction_text)
    if venc:
        merged["data_vencimento"] = venc
    # descrição só muda se sobrar texto substantivo além de valor/data
    residual = re.sub(r"[\d.,/\-R$]+", " ", correction_text).lower().split()
    residual = [w for w in residual if w not in _CORRECTION_STOPWORDS]
    if len(residual) >= 2:
        merged["descricao"] = _summarize(correction_text)
        merged["categoria"] = classify_category(correction_text)
    return merged


def handle_silent_image(ocr_content: str, user_name: str = "Kevin") -> dict:
    """Regra de Ouro: imagem sem contexto -> menu numerado + micro-educação."""
    res = _base_result()
    item = _build_item(ocr_content)
    valor_txt = (f"R$ {item['valor_reais']:.2f}".replace(".", ",")
                 if item["valor_reais"] else "valor não identificado")
    venc_iso = item["data_vencimento"]
    venc_txt = _fmt_br(venc_iso)

    res["needs_decision"] = True
    res["pending_payload"] = item
    res["reply"] = (
        f"Identifiquei um documento financeiro no valor de {valor_txt} "
        f"com vencimento em {venc_txt}. Como procedo?\n\n"
        f"1️⃣ Salvar como **Despesa Paga**.\n"
        f"2️⃣ Agendar **Lembrete de Cobrança** para um dia antes do vencimento.\n\n"
        f"_(Se algum dado acima estiver errado, responda com a correção — "
        f"ex.: \"valor 210,50 vence 25/07\".)_\n"
        f"_(💡 Dica: na próxima, mande a foto junto com um áudio de 2 segundos "
        f"dizendo 'pago' ou 'agendar' que eu resolvo direto.)_"
    )
    return res


def resolve_pending_decision(choice: str, pending: dict) -> dict:
    """Processa a resposta 1/2 do menu de decisão — e, no fluxo v6,
    a confirmação SIM/correção dos dados extraídos por visão."""
    res = _base_result()
    c = choice.strip().lower()
    item = dict(pending)

    # --- v6: confirmação de dados extraídos de imagem -----------------
    if "_confirm" in pending:
        acao = pending["_confirm"]
        item.pop("_confirm", None)
        if re.fullmatch(r"(1|sim|s|confere|confirmo|ok|isso|pode|certo|exato)[!.\s]*", c):
            if acao == "despesa":
                item["tipo"], item["status"] = "despesa", "concluido"
                res["items"].append(item)
                res["reply"] = "Confirmado. ✅ Arquivado como *despesa paga*."
            else:
                item["tipo"], item["status"] = "lembrete", "pendente"
                if item.get("data_vencimento"):
                    y, m, d = map(int, item["data_vencimento"].split("-"))
                    item["data_vencimento"] = (date(y, m, d)
                                               - timedelta(days=1)).isoformat()
                res["items"].append(item)
                res["reply"] = (f"Confirmado. ✅ Lembrete agendado para "
                                f"{_fmt_br(item.get('data_vencimento'))}.")
            return res
        if re.search(r"\d", c):  # usuário mandou correção com números
            merged = _merge_correction(item, choice)
            res["needs_decision"] = True
            res["pending_payload"] = {**merged, "_confirm": acao}
            res["reply"] = "Corrigido! 👇\n\n" + _confirm_msg(merged, "")\
                .replace("Li aqui, : ", "Ficou assim: ")
            return res
        if re.fullmatch(r"2[!.\s]*", c) or re.search(r"\b(n[aã]o|errado|errada|corrig)\b", c):
            res["needs_decision"] = True
            res["pending_payload"] = pending
            res["reply"] = ("Sem problema — me manda o dado certo em uma "
                            "linha (ex.: _\"Enel 210,50 vence 25/07\"_) "
                            "que eu ajusto antes de salvar.")
            return res
        res["needs_decision"] = True
        res["pending_payload"] = pending
        res["reply"] = ("Só preciso da sua escolha 🙂\n"
                        "*1* ✅ salvar como está\n"
                        "*2* ✏️ corrigir (me manda o dado certo)")
        return res
    if c.startswith("1") or "pag" in c:
        item["tipo"], item["status"] = "despesa", "concluido"
        res["items"].append(item)
        res["reply"] = "Feito. Arquivado como **Despesa Paga**."
    elif c.startswith("2") or "agend" in c or "lembr" in c:
        item["tipo"], item["status"] = "lembrete", "pendente"
        if item.get("data_vencimento"):
            y, m, d = map(int, item["data_vencimento"].split("-"))
            remind = date(y, m, d) - timedelta(days=1)
            item["data_vencimento"] = remind.isoformat()
        res["items"].append(item)
        res["reply"] = (f"Agendado. Lembrete de cobrança em "
                        f"{_fmt_br(item.get('data_vencimento'))}.")
    elif re.search(r"\d{2,}|r\$", c):  # v6: correção de valor/data no menu
        merged = _merge_correction(item, choice)
        valor_txt = (f"R$ {merged['valor_reais']:.2f}".replace(".", ",")
                     if merged["valor_reais"] else "valor não identificado")
        res["needs_decision"] = True
        res["pending_payload"] = merged
        res["reply"] = (f"Corrigido: *{valor_txt}*, vencimento "
                        f"{_fmt_br(merged.get('data_vencimento'))}. "
                        f"E agora?\n\n1️⃣ Salvar como **Despesa Paga**\n"
                        f"2️⃣ Agendar **Lembrete de Cobrança**")
    else:
        res["needs_decision"] = True
        res["pending_payload"] = pending
        res["reply"] = ("Não entendi. Responda **1** (despesa paga), "
                        "**2** (agendar lembrete) — ou corrija os dados "
                        "em uma linha (ex.: _\"valor 210,50 vence 25/07\"_).")
    return res


def handle_video() -> dict:
    res = _base_result()
    res["reply"] = (
        "Para garantir precisão e velocidade máxima, processamos apenas fotos, "
        "áudios e textos rápidos. Envie um print ou áudio resumindo o vídeo "
        "que eu executo na hora."
    )
    return res


# ---------------------------------------------------------------------------
# Camada LLM (opcional)
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """Você é o motor de compreensão do RESOLVE AI, assistente de vida via WhatsApp para BRASILEIROS.
Responda APENAS com JSON válido, sem markdown, sem preâmbulo, no formato:
{
  "reply": "resposta natural em pt-BR, máx 2-3 linhas",
  "items": [{
      "tipo": "lembrete|despesa|documento",
      "categoria": "Alimentação|Pet|Veículo|Contas|Saúde|Casa|Lazer|Outros",
      "descricao": "resumo curto e limpo (sem verbos de comando, sem data, sem valor)",
      "valor_reais": 0.0 ou null,
      "data_vencimento": "YYYY-MM-DD" ou null,
      "hora_alvo": "HH:MM" ou null,
      "status": "pendente|concluido"
  }],
  "needs_decision": false
}

== COMO BRASILEIRO FALA (interprete, não exija formalidade) ==
- Gíria de dinheiro: "50 conto"/"30 pila"/"20 mango"/"100 pau" = R$ 50/30/20/100.
- Abreviações: vc, hj, amn, tbm, qdo, pq, blz, vlw, fds, sla — entenda todas.
- Áudio transcrito vem enrolado: "ééé então tipo comprei uma ração aí deu uns 89"
  → despesa R$ 89, Pet. Ignore muletas ("tipo", "aí", "né", "então").
- Tempo relativo BR: "daqui a pouco"=+1h, "mais tarde"=hoje 18:00, "de manhã"=09:00,
  "de tarde"=15:00, "de noite"=20:00, "cedo"=08:00, "depois do almoço"=13:30,
  "fim do mês"=último dia do mês, "semana que vem"=+7 dias, "dia de pagamento"≈dia 05.
- Contexto implícito: "acabou o gás" = quer registrar/repor gás de cozinha;
  "o boleto da net chegou" = conta de internet a vencer (pergunte valor/vencimento
  se não vierem); "tô devendo o dentista" = lembrete de pagamento pendente.

== PREVEJA O CENÁRIO (uma antecipação útil, nunca mais que uma) ==
Se registrar algo que se repete (gás, ração, remédio, mensalidade), ofereça o
próximo passo em UMA frase: "Gás costuma durar ~45 dias — quer que eu já te
avise perto de acabar?". Não force; ofereça.

== REGRA DE OURO: NUNCA RESPOSTA VAZIA ==
Proibido responder "não entendi" seco. Na dúvida:
1) Dê seu MELHOR PALPITE e confirme: "Entendi que você pagou R$ 14 num lanche
   — registrei em Alimentação. Era isso?" (registre com o palpite).
2) Se realmente não der para agir, faça UMA pergunta específica que destrave:
   "Esse 'resolver o negócio do carro' é a revisão, o IPVA ou outra coisa?"
   — nunca peça "mais contexto" genérico.

== SEJA ASSERTIVO E PROATIVO (você é um MORDOMO, não um formulário) ==
Se a mensagem tem uma ação + um tempo, CRIE O LEMBRETE, não pergunte.
Exemplos que você DEVE resolver na hora, sem pedir esclarecimento:
- "me lembra daqui 5min que preciso planejar minhas férias"
  → lembrete, descrição "planejar as férias", hora_alvo = agora+5min. NUNCA
    responda "não ficou claro" — está clianíssimo.
- "me lembra amanhã de ligar pro dentista" → lembrete pra amanhã.
- "planejar férias sábado" → lembrete pra sábado.
Só peça esclarecimento se faltar a AÇÃO em si (ex.: "me lembra daqui 1h" sem
dizer de quê). Se tem ação, aja. Confirme sempre o que criou em 1 linha
curta com um ícone: "📌 Anotado: *planejar as férias* — te aviso às 18:35."
Se o usuário perguntar "anotou?" logo após um pedido, confirme o último item
criado; nunca finja que não entendeu.

== INTENÇÕES CONVERSACIONAIS ==
Saudação, agradecimento, "o que você faz", consultas ("quanto gastei",
"o que tem pendente") e baixas ("paguei a luz"): responda naturalmente no
"reply" com items=[] — outra camada resolve consultas e baixas. Nunca invente
dados que o usuário não deu.

== FORA DO SEU ESCOPO: NUNCA CHUTE, NUNCA ALUCINE ==
Você é um MORDOMO de organização pessoal — não é buscador, nem calculadora,
nem enciclopédia. Se pedirem trivia, fatos gerais, contas de matemática,
notícias, esportes, história ("quem ganhou a copa", "quanto é 5+10",
"capital da França"): NÃO responda o fato (você erraria e perderia a
confiança). Responda com items=[] e um "reply" honesto e simpático que
redireciona, ex.:
- "Essa eu deixo pro Google 😄 Meu forte é não deixar você esquecer das
   coisas — contas, consultas, compras. Quer anotar alguma?"
- "Haha, matemática não é comigo — mas prazo e vencimento eu nunca erro.
   Tem algo que você não quer deixar passar?"
Regra absoluta: se não tem CERTEZA de um fato, você NÃO afirma o fato.
Melhor admitir com charme do que inventar. Um mordomo que erra fato perde
o emprego.

== LEMBRETE PESSOAL ≠ CONTA A PAGAR (não confunda) ==
"tipo":"lembrete" é para AÇÕES/COMPROMISSOS (estender a roupa, arrumar a
bolsa, estudar Step da MBA, ligar pro dentista, buscar filho). Esses NUNCA
recebem "marque como pago" — não se paga uma tarefa. Só "tipo":"despesa" ou
conta com valor_reais recebe linguagem de pagamento. Para lembrete puro, a
ação certa é "feito/concluído", nunca "pago". Estudo, tarefa doméstica e
compromisso são SEMPRE lembrete, jamais despesa.

== REGRAS FIXAS ==
- Data de referência (hoje): {today}. Hora atual: {now}.
- "paguei/comprei/gastei" -> despesa status "concluido".
- Lembrete sem data nem hora -> registre e pergunte o quando em UMA frase.
- Imagem SEM instrução -> needs_decision=true, items=[], reply com menu
  (1 = despesa paga, 2 = lembrete) mostrando o que você leu.
- Você LEMBRA o usuário de pagar; NUNCA paga, compra ou transfere por ele.
- Tom: parceiro direto, zero corporativês, no máximo 1 emoji."""


def _call_llm(user_content: str) -> Optional[dict]:
    """Chama o LLM via litellm. 1 retry em falha de parse. None -> mock."""
    try:
        from litellm import completion  # import tardio: só se instalado
        system = (LLM_SYSTEM_PROMPT
                  .replace("{today}", date.today().isoformat())
                  .replace("{now}", tempo.agora().strftime("%H:%M")))
        for attempt in range(2):
            resp = completion(
                model=LLM_MODEL,
                max_tokens=600,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ] + ([{"role": "user", "content":
                       "Sua resposta anterior não era JSON válido. "
                       "Responda SOMENTE o JSON."}] if attempt else []),
            )
            raw = resp.choices[0].message.content
            raw = re.sub(r"```(?:json)?|```", "", raw).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue  # retry uma vez
            result = _base_result(mode="llm")
            result["reply"] = data.get("reply", "")
            result["items"] = data.get("items", []) or []
            result["needs_decision"] = bool(data.get("needs_decision", False))
            for it in result["items"]:
                it.setdefault("link_afiliado",
                              affiliate_link_for(it.get("descricao", "")))
                it.setdefault("status", "pendente")
                it.setdefault("hora_alvo", None)
            if not result["reply"]:
                continue  # reply vazio viola a regra de ouro -> retry/mock
            return result
        return None
    except Exception:
        return None  # fallback silencioso para o mock


# ---------------------------------------------------------------------------
# Camada de INTERPRETAÇÃO: intenção -> ação (funciona no Mock, sem LLM)
# ---------------------------------------------------------------------------

INTENT_PATTERNS = [
    ("saudacao", r"^(oi+|ol[aá]|bom dia|boa tarde|boa noite|e a[ií]|eai|opa|hey|salve|fala)\b"),
    ("agradecimento", r"^(obrigad[oa]+|valeu|vlw|show|top|perfeito|massa|boa)\W*$"),
    ("capacidades", r"o que (voc[eê]|vc|tu) faz|como (voc[eê]|vc|isso) funciona|pra que (voc[eê]|vc|isso) serve|me ajuda com o qu|quais.*(comandos|fun[cç][oõ]es)"),
    ("consulta_gastos", r"quanto (j[aá] )?gastei|meus gastos|total gasto|gastei quanto|resumo.*gasto"),
    ("consulta_agenda", r"o que (tem|vence|t[aá] pendente)|meus lembretes|minha agenda|o que (voc[eê]|vc) anotou|pr[oó]ximos (vencimentos|lembretes)|(lista|mostra).*(pendente|lembrete)|o que falta"),
    ("adiar", r"^adia(?:r)?\b"),
    ("remover", r"^(esquece|apaga|remove|tira)\b"),
    ("editar", r"^(muda|mudar|altera|alterar|corrige|corrigir|troca)\b"),
]

CONCLUSAO_RE = re.compile(
    r"^(ok[,\s]+)?(j[aá]\s+)?(paguei|resolvi|feito|conclu[ií]|quitei|terminei|"
    r"lembrei|fiz|arrumei|guardei)\b", re.IGNORECASE)


def detect_intent(text: str) -> str:
    low = text.strip().lower()
    for intent, pat in INTENT_PATTERNS:
        if re.search(pat, low):
            return intent
    if CONCLUSAO_RE.search(low) and extract_money(low) is None:
        return "conclusao"          # "paguei a conta de luz" (sem valor novo)
    if len(low) <= 3 or re.fullmatch(
            r"[\W_]*(hmm+|hum+|kkk+|rs+|ok|t[aá]|sei l[aá]|\?+)[\W_]*", low):
        return "vago"
    return "registro"


def _match_pending_item(user_id: int, text: str) -> Optional[dict]:
    """Encontra o lembrete pendente que melhor casa com o texto."""
    import db
    stop = {"paguei", "resolvi", "feito", "conclui", "concluí", "quitei",
            "ja", "já", "a", "o", "de", "da", "do", "no", "na", "hoje",
            "agora", "essa", "esse", "aquela", "aquele"}
    words = {w for w in re.findall(r"\w+", text.lower())
             if w not in stop and len(w) > 2}
    best, best_score = None, 0
    for item in db.list_items(user_id, status="pendente"):
        iw = set(re.findall(r"\w+", item["descricao"].lower()))
        # match exato OU por radical (estendi ~ estender): prefixo de 4+ letras
        score = 0
        for w in words:
            if w in iw:
                score += 1
            elif any(len(w) >= 4 and len(x) >= 4 and
                     (w[:4] == x[:4]) for x in iw):
                score += 1
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= 1 else None


def _split_multi(text: str) -> list[str]:
    """'paguei 50 de luz e 30 de água' -> partes, quando há ≥2 valores."""
    raw_parts = [p.strip() for p in re.split(r"\s+e\s+|,(?!\d)\s*", text)]
    parts = [p for p in raw_parts if extract_money(p) is not None]
    if len(parts) < 2:
        return [text]
    # propaga o verbo de pagamento da frase para as partes sem verbo
    verb = re.match(r"^\s*(paguei|gastei|comprei)\b", text, re.IGNORECASE)
    if verb:
        v = verb.group(1)
        parts = [p if re.match(r"^\s*(paguei|gastei|comprei)\b", p,
                               re.IGNORECASE) else f"{v} {p}"
                 for p in parts]
    return parts


def _fmt_valor(v: Optional[float]) -> str:
    return f"R$ {v:.2f}".replace(".", ",") if v is not None else ""


def converse(
    user_id: int,
    user_name: str,
    kind: str,
    content: str,
    instruction: str = "",
    pending: Optional[dict] = None,
) -> dict:
    """
    Ponto de entrada CONVERSACIONAL: interpreta a intenção, consulta e
    atualiza o banco quando preciso, persiste itens e devolve resposta pronta.
    Mesmo contrato de process_input.
    """
    import db

    def _persist(result: dict) -> None:
        for item in result.get("items", []):
            db.add_item(user_id=user_id, tipo=item["tipo"],
                        categoria=item.get("categoria", "Outros"),
                        descricao=(item.get("descricao") or "item")[:120],
                        valor_reais=item.get("valor_reais"),
                        data_vencimento=item.get("data_vencimento"),
                        hora_alvo=item.get("hora_alvo"),
                        recorrencia=item.get("recorrencia"),
                        status=item.get("status", "pendente"),
                        link_afiliado=item.get("link_afiliado"))
        if not result.get("items"):
            db.touch_user(user_id)

    # Mídia e decisões seguem o fluxo original (Regra de Ouro intacta)
    if kind in ("decisao", "video", "imagem_com_texto", "imagem_silenciosa"):
        if kind == "decisao":
            content = normalize_br(content)
        result = process_input(kind, content, instruction=instruction,
                               user_name=user_name, pending=pending)
        _persist(result)
        return result

    text = clean_audio_transcript(content) if kind == "audio" else content
    text = normalize_br(text)
    intent = detect_intent(text)
    base = _base_result()

    if intent == "saudacao":
        base["reply"] = (f"Oi, {user_name}! 👋 Pode mandar: uma conta pra eu "
                         f"lembrar, um gasto pra registrar, ou pergunte "
                         f"_\"o que tem pendente?\"_. Estou ouvindo.")
    elif intent == "agradecimento":
        base["reply"] = "Tamo junto! 🤝 Qualquer coisa, é só mandar."
    elif intent == "capacidades":
        base["reply"] = (
            "Eu tiro coisas da sua cabeça. Pode mandar do seu jeito:\n"
            "• _\"conta de luz vence dia 20, R$ 187\"_ → eu lembro antes\n"
            "• _\"paguei 250 no mercado\"_ → registro o gasto\n"
            "• _\"paguei a conta de luz\"_ → dou baixa no lembrete\n"
            "• _\"quanto gastei esse mês?\"_ · _\"o que tem pendente?\"_\n"
            "• 📷 foto de boleto ou 🎤 áudio enrolado mesmo\n"
            "Comandos: *assinar* · *cancelar* · *apagar meus dados*")
    elif intent == "consulta_gastos":
        total = db.month_spend(user_id)
        if total <= 0:
            base["reply"] = ("Ainda não registrei gastos seus este mês. "
                             "Manda tipo _\"paguei 250 no mercado\"_ que eu "
                             "começo a somar.")
        else:
            cats = db.spend_by_category(user_id)
            top = "\n".join(f"• {c}: {_fmt_valor(v)}"
                            for c, v in list(cats.items())[:4] if v > 0)
            base["reply"] = (f"📊 Você gastou *{_fmt_valor(total)}* este "
                             f"mês:\n{top}")
    elif intent == "consulta_agenda":
        pendentes = db.list_items(user_id, status="pendente")
        if not pendentes:
            base["reply"] = ("Nada pendente — sua cabeça está oficialmente "
                             "leve. 🧘 Quando surgir algo, manda que eu "
                             "seguro.")
        else:
            linhas = "\n".join(
                f"• {i['descricao']}"
                + (f" — vence {i['data_vencimento'][8:10]}/"
                   f"{i['data_vencimento'][5:7]}"
                   if i["data_vencimento"] else "")
                + (f" ({_fmt_valor(i['valor_reais'])})"
                   if i["valor_reais"] else "")
                for i in pendentes[:8])
            base["reply"] = (f"📋 Você tem *{len(pendentes)} pendente(s)*:\n"
                             f"{linhas}\n\nResolveu algum? Me fala "
                             f"(_\"paguei a conta de luz\"_) que eu dou "
                             f"baixa.")
    elif intent == "conclusao":
        match = _match_pending_item(user_id, text)
        # Fallback: se não casou pelo texto (ex.: resposta seca "feito" a um
        # alarme), dá baixa no último item que o bot alarmou. Isso conserta a
        # contradição de pedir "responda feito" e não entender o "feito".
        if not match:
            match = db.last_alarmed_item(user_id)
        if match:
            db.update_item_status(match["id"], "concluido")
            rest = len(db.list_items(user_id, status="pendente"))
            base["reply"] = (f"Baixa dada! ✅ *{match['descricao']}* "
                             f"concluído. "
                             + (f"Sobraram {rest} pendente(s)."
                                if rest else "Zerou a lista. 🧘"))
        else:
            item = _build_item(text)
            item["tipo"], item["status"] = "despesa", "concluido"
            base["items"].append(item)
            base["reply"] = ("Não achei lembrete pendente disso, então "
                             "registrei como despesa concluída. Quer que "
                             "eu some no mês? Me diz o valor "
                             "(ex.: _\"foi 89 reais\"_).")
    elif intent == "adiar":
        # "adiar 1h" / "adia a net pra dia 20" / "adiar pra amanhã"
        resto = re.sub(r"^adia(?:r)?\s*", "", text, flags=re.IGNORECASE)
        alvo = None
        # 1) alvo nomeado: "a net", "o boleto da escola"
        nome = re.sub(r"\b(pra?|para)\b.*$", "", resto).strip(" ,.")
        if len(re.sub(r"[^a-zà-ú]", "", nome.lower())) >= 3:
            alvo = _match_pending_item(user_id, nome)
        if not alvo:
            alvo = db.last_alarmed_item(user_id)
        if not alvo:
            pend = db.list_items(user_id, status="pendente")
            alvo = pend[0] if pend else None
        if not alvo:
            base["reply"] = ("Não achei nada pendente pra adiar. Manda o "
                             "lembrete de novo que eu agendo.")
        else:
            nova_data = extract_due_date(resto)
            nova_hora = extract_due_time(resto)
            m = re.search(r"(\d+)\s*(min|minuto|h|hora)", resto.lower())
            eh_relativo = (m and not re.search(
                r"amanh|semana|dia\s+\d|segunda|ter[çc]a|quarta|quinta|"
                r"sexta|s[áa]bado|domingo|[àa]s\s", resto.lower()))
            if eh_relativo:  # "adiar 1h" / "adiar 30 min" = +tempo, não 01:00
                n = int(m.group(1))
                minutos = n if m.group(2).startswith("min") else n * 60
                base_h = alvo.get("hora_alvo") or tempo.agora().strftime("%H:%M")
                h, mi = map(int, base_h.split(":"))
                novo = (tempo.agora().replace(hour=h, minute=mi)
                        + timedelta(minutes=minutos))
                db.postpone_item(
                    alvo["id"],
                    new_date=(novo.date().isoformat()
                              if novo.date() > date.today() else None),
                    new_time=novo.strftime("%H:%M"))
                base["reply"] = (f"Adiado: *{alvo['descricao']}* agora toca "
                                 f"às {novo.strftime('%H:%M')}. ⏰")
            elif nova_data:  # "pra dia 20" / "pra amanhã" / "pra sexta"
                db.postpone_item(alvo["id"], new_date=nova_data,
                                 new_time=nova_hora)
                base["reply"] = (f"Adiado: *{alvo['descricao']}* ficou pra "
                                 f"{_fmt_br(nova_data)}"
                                 + (f" às {nova_hora}" if nova_hora else "")
                                 + ". 👍")
            else:
                amanha = (date.today() + timedelta(days=1)).isoformat()
                db.postpone_item(alvo["id"], new_date=amanha)
                base["reply"] = (f"Adiado: *{alvo['descricao']}* ficou pra "
                                 f"amanhã. 👍")
    elif intent == "editar":
        base["reply"] = ("Editar direto ainda tá chegando. Por enquanto o "
                         "caminho é: _\"esquece <o item>\"_ e manda de novo "
                         "com o dado certo — 10 segundos e fica redondo. 😉")
    elif intent == "remover":
        resto = re.sub(r"^(esquece|apaga|remove|tira)\s*", "", text,
                       flags=re.IGNORECASE).strip(" ,.")
        alvo = _match_pending_item(user_id, resto) if resto else None
        if alvo:
            db.update_item_status(alvo["id"], "concluido")
            base["reply"] = (f"Feito — tirei *{alvo['descricao']}* da sua "
                             f"lista. 🗑️")
        else:
            base["reply"] = ("Qual deles? Me fala o nome (ex.: _\"esquece a "
                             "conta de água\"_) que eu removo.")
    elif intent == "vago":
        # Anti-resposta-vazia: usa o contexto do usuário para destravar.
        pendentes = db.list_items(user_id, status="pendente")
        if pending:
            base["reply"] = ("A gente estava resolvendo um item — responda "
                             "*SIM* pra confirmar, *1*/*2* pra escolher, ou "
                             "me manda a correção em uma linha.")
            base["needs_decision"] = True
            base["pending_payload"] = pending
        elif pendentes:
            prox = pendentes[0]
            venc = (f" (vence {prox['data_vencimento'][8:10]}/"
                    f"{prox['data_vencimento'][5:7]})"
                    if prox["data_vencimento"] else "")
            base["reply"] = (f"Não peguei essa 😅 Enquanto isso: seu próximo "
                             f"pendente é *{prox['descricao']}*{venc}. "
                             f"Quer dar baixa, adiar, ou registrar algo novo? "
                             f"Me manda do seu jeito.")
        else:
            base["reply"] = ("Não peguei essa 😅 Me manda de novo do seu "
                             "jeito — pode ser áudio, foto do boleto ou "
                             "texto corrido, tipo: _\"luz vence dia 20, "
                             "187 conto\"_.")
    else:
        # --- registro, com suporte a múltiplos itens numa frase -----------
        parts = _split_multi(text)
        if len(parts) > 1:
            for p in parts:
                base["items"].extend(handle_text(p, user_name)["items"])
            resumo = "\n".join(
                f"• {i['descricao']}"
                + (f" — {_fmt_valor(i['valor_reais'])}"
                   if i["valor_reais"] else "")
                for i in base["items"])
            base["reply"] = f"Registrei {len(base['items'])} itens:\n{resumo}"
        else:
            result = process_input(kind, content, instruction=instruction,
                                   user_name=user_name, pending=pending)
            _persist(result)
            return result

    _persist(base)
    return base


# ---------------------------------------------------------------------------
# API pública do motor
# ---------------------------------------------------------------------------

def process_input(
    kind: str,
    content: str,
    instruction: str = "",
    user_name: str = "Kevin",
    pending: Optional[dict] = None,
) -> dict:
    """
    Ponto de entrada único.
    kind: 'texto' | 'audio' | 'imagem_com_texto' | 'imagem_silenciosa'
          | 'video' | 'decisao'
    """
    if kind == "decisao" and pending:
        return resolve_pending_decision(normalize_br(content), pending)

    if kind == "video":
        return handle_video()

    if kind in ("texto", "audio"):
        content = normalize_br(content)

    if LLM_AVAILABLE:
        prompt_map = {
            "texto": f"[TEXTO] {content}",
            "audio": f"[TRANSCRIÇÃO DE ÁUDIO INFORMAL] {content}",
            "imagem_com_texto": f"[OCR DA IMAGEM] {content}\n[INSTRUÇÃO DO USUÁRIO] {instruction}",
            "imagem_silenciosa": f"[OCR DA IMAGEM, SEM INSTRUÇÃO] {content}",
        }
        llm_result = _call_llm(prompt_map.get(kind, content))
        if llm_result:
            if llm_result["needs_decision"]:
                llm_result["pending_payload"] = _build_item(content)
            return llm_result

    # Fallback / modo padrão: Mock inteligente
    handlers = {
        "texto": lambda: handle_text(content, user_name),
        "audio": lambda: handle_audio(content, user_name),
        "imagem_com_texto": lambda: handle_image_with_text(content, instruction, user_name),
        "imagem_silenciosa": lambda: handle_silent_image(content, user_name),
    }
    handler = handlers.get(kind)
    if handler is None:
        res = _base_result()
        res["reply"] = "Formato não reconhecido. Envie texto, áudio ou foto."
        return res
    return handler()
