# -*- coding: utf-8 -*-
"""
tempo.py — Tempo do Brasil (fuso, feriados, datas importantes)
==============================================================
Centraliza TODA noção de "agora" no fuso America/Sao_Paulo.
Antes, datetime.now() pegava a hora do servidor (UTC) — 3h à frente —
e o bot entendia "7:58" como "11:03". Agora usamos agora() em todo lugar.
"""
from datetime import date, datetime, timedelta, timezone

# America/Sao_Paulo. Desde 2019 o Brasil não tem mais horário de verão,
# então o offset é fixo em -03:00. (Se voltar o DST, troca-se por zoneinfo.)
_BR_OFFSET = timezone(timedelta(hours=-3))

try:
    # Se o servidor tiver a tz database, usamos o nome oficial (mais robusto)
    from zoneinfo import ZoneInfo
    _BR_TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    _BR_TZ = _BR_OFFSET


def agora() -> datetime:
    """Datetime atual no fuso do Brasil, SEM tzinfo (naive) para casar com o
    resto do código, que compara com strings 'YYYY-MM-DD HH:MM'."""
    return datetime.now(_BR_TZ).replace(tzinfo=None)


def hoje() -> date:
    """Data de hoje no Brasil."""
    return agora().date()


# ── Feriados nacionais fixos + móveis (para mensagens interativas) ────────
def _pascoa(ano: int) -> date:
    """Algoritmo de Butcher para o Domingo de Páscoa."""
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_do_ano(ano: int) -> dict:
    """Mapa {date: nome} dos principais feriados/datas do Brasil no ano."""
    pascoa = _pascoa(ano)
    carnaval = pascoa - timedelta(days=47)
    sexta_santa = pascoa - timedelta(days=2)
    corpus = pascoa + timedelta(days=60)
    fixos = {
        date(ano, 1, 1): "Ano Novo",
        date(ano, 4, 21): "Tiradentes",
        date(ano, 5, 1): "Dia do Trabalho",
        date(ano, 9, 7): "Independência",
        date(ano, 10, 12): "Nossa Senhora Aparecida",
        date(ano, 11, 2): "Finados",
        date(ano, 11, 15): "Proclamação da República",
        date(ano, 11, 20): "Consciência Negra",
        date(ano, 12, 25): "Natal",
        # datas comerciais úteis para lembretes/mensagens
        date(ano, 2, 14): "Dia dos Namorados (comercial)",
        date(ano, 3, 8): "Dia da Mulher",
        date(ano, 6, 12): "Dia dos Namorados",
    }
    moveis = {
        carnaval: "Carnaval",
        sexta_santa: "Sexta-feira Santa",
        pascoa: "Páscoa",
        corpus: "Corpus Christi",
    }
    # Dia das Mães = 2º domingo de maio; Dia dos Pais = 2º domingo de agosto
    def nth_domingo(ano, mes, n):
        d = date(ano, mes, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)  # 1º domingo
        return d + timedelta(days=7 * (n - 1))
    moveis[nth_domingo(ano, 5, 2)] = "Dia das Mães"
    moveis[nth_domingo(ano, 8, 2)] = "Dia dos Pais"
    return {**fixos, **moveis}


def feriado_em(d: date):
    """Retorna o nome do feriado se a data for um, senão None."""
    return feriados_do_ano(d.year).get(d)
