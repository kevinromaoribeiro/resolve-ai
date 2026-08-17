# -*- coding: utf-8 -*-
"""
calendario.py — datas que o bot pode saber sozinho (IPVA, licenciamento, feriados).

REGRA DO BLOCO (M2.2): fonte externa fora do ar **não pode fazer lembrete
sumir nem nascer com data errada**. As duas metades importam, e a segunda é
a mais fácil de violar — data errada parece que funcionou.

POR QUE TABELA, E NÃO API:
o calendário de IPVA e licenciamento não é um serviço, é uma TABELA que cada
estado publica uma vez por ano, por final de placa. Não existe API oficial
gratuita. Então a tabela versionada aqui é a fonte primária — dado, não
lógica, como `casos_de_uso.py` — e a rede é, no máximo, confirmação.

Consequência prática: o modo degradado é o normal. Sem internet, IPVA e
licenciamento funcionam igual; feriado nacional é calculável (fixos + os
móveis, que derivam da Páscoa), então a API é atalho, nunca dependência.

COMO ATUALIZAR (uma vez por ano, quando o estado publica):
  1. acrescente o ano em `IPVA` e `LICENCIAMENTO` abaixo
  2. rode `pytest tests/test_m22_calendario.py`
  3. sem o ano na tabela o bot NÃO cria lembrete nenhum — ele não chuta a
     data do ano anterior, porque o calendário muda todo ano.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger("resolveai")

# ---------------------------------------------------------------------------
# IPVA e LICENCIAMENTO — tabela por UF, ano e final de placa
# ---------------------------------------------------------------------------
# Fonte: calendário publicado pela Sefaz/Detran de cada estado.
# ATENÇÃO: os dias abaixo são os do calendário de SP para 2026 (cota única /
# 1ª parcela do IPVA em janeiro, licenciamento escalonado no 2º semestre).
# Conferir contra o edital do ano antes de estender pra outros estados —
# data errada aqui vira lembrete errado na mão de todo mundo com carro.

# O FINAL 0 É O ÚLTIMO, SEMPRE. É o padrão de todo calendário por final de
# placa no Brasil, e a primeira versão desta tabela o enfiava no meio: no
# IPVA ele caía entre o 7 e o 8, e no licenciamento ele REPETIA a data do
# final 7 — ou seja, todo dono de placa final 0 recebia o lembrete com dois
# meses de erro. Duas checagens em teste travam isso agora: dez datas
# distintas por tipo/ano, e o final 0 tem que ser a maior data do conjunto.
IPVA = {
    ("SP", 2026): {1: "2026-01-12", 2: "2026-01-13", 3: "2026-01-14",
                   4: "2026-01-15", 5: "2026-01-16", 6: "2026-01-19",
                   7: "2026-01-20", 8: "2026-01-21", 9: "2026-01-22",
                   0: "2026-01-23"},
    ("SP", 2027): {1: "2027-01-12", 2: "2027-01-13", 3: "2027-01-14",
                   4: "2027-01-15", 5: "2027-01-18", 6: "2027-01-19",
                   7: "2027-01-20", 8: "2027-01-21", 9: "2027-01-22",
                   0: "2027-01-25"},
}

LICENCIAMENTO = {
    ("SP", 2026): {1: "2026-04-30", 2: "2026-05-29", 3: "2026-06-30",
                   4: "2026-07-31", 5: "2026-08-31", 6: "2026-09-30",
                   7: "2026-10-30", 8: "2026-11-30", 9: "2026-12-22",
                   0: "2026-12-30"},
    ("SP", 2027): {1: "2027-04-30", 2: "2027-05-31", 3: "2027-06-30",
                   4: "2027-07-30", 5: "2027-08-31", 6: "2027-09-30",
                   7: "2027-10-29", 8: "2027-11-30", 9: "2027-12-22",
                   0: "2027-12-30"},
}

ANOS_COBERTOS = sorted({ano for _, ano in IPVA})
UFS_COBERTAS = sorted({uf for uf, _ in IPVA})


def vencimentos(uf: str, final_placa, ano: int) -> list:
    """[{tipo, data, rotulo}] para este final de placa neste ano.

    Lista VAZIA quando não se sabe — UF fora da tabela, ano não publicado,
    final inválido. Vazio é resposta legítima: melhor não criar lembrete do
    que criar no dia errado.
    """
    try:
        final = int(final_placa)
    except (TypeError, ValueError):
        return []
    if not 0 <= final <= 9:
        return []
    uf = (uf or "").strip().upper()

    saida = []
    for tipo, tabela, rotulo in (
            ("ipva", IPVA, "IPVA"),
            ("licenciamento", LICENCIAMENTO, "Licenciamento")):
        data = (tabela.get((uf, ano)) or {}).get(final)
        if not data:
            log.info("[calendario] sem tabela de %s para %s/%s", tipo, uf, ano)
            continue
        saida.append({"tipo": tipo, "data": data,
                      "rotulo": f"{rotulo} (final {final})"})
    return saida


# ---------------------------------------------------------------------------
# PLACA
# ---------------------------------------------------------------------------
# MERCOSUL é inequívoco: a 5ª posição é LETRA, e isso não colide com texto
# comum ("ano 2026", "deu 1234" não têm letra ali).
_PLACA_MERCOSUL_RE = re.compile(r"\b([A-Z]{3}[\s.-]?\d[A-Z]\d{2})\b", re.I)
# O formato antigo (ABC1234) é indistinguível de "palavra de 3 letras +
# 4 dígitos", então SÓ vale colado à palavra "placa". Sem isso, "o ipva do
# ano 2026 ja saiu?" virava placa de final 6 e o bot respondia "Pronto,
# guardei" com dois lembretes na data de outra pessoa.
_PLACA_ANTIGA_RE = re.compile(
    r"placa[\s:]*(?:e|é|:)?\s*([A-Z]{3}[\s.-]?\d{4})\b", re.I)
_FINAL_RE = re.compile(
    r"final\s*(?:de\s*placa\s*)?[:\s]*(\d)\b(?!\s*(?:parcela|vez|x\b))", re.I)


def final_da_placa(texto: Optional[str]) -> Optional[int]:
    """O último dígito da placa — é ele que define o calendário.

    Devolve None em texto comum. A guarda de contexto (`_PLACA_PEDIDO_RE`,
    no wa_bot) não basta: a frase mais natural do mundo sobre IPVA contém a
    palavra "ipva" E um número. Quem separa placa de número é este regex.
    """
    if not texto:
        return None
    t = str(texto).strip()
    for rx in (_PLACA_MERCOSUL_RE, _PLACA_ANTIGA_RE):
        m = rx.search(t)
        if m:
            digitos = re.sub(r"\D", "", m.group(1))
            return int(digitos[-1]) if digitos else None
    # "placa final 7" — só com a palavra "placa" na frase, pelo mesmo motivo.
    if re.search(r"\bplacas?\b", t, re.I):
        m = _FINAL_RE.search(t)
        if m:
            return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# FERIADOS
# ---------------------------------------------------------------------------
def _pascoa(ano: int) -> date:
    """Algoritmo de Meeus/Jones/Butcher. Sem ele, os móveis dependeriam de
    rede — e feriado é a coisa mais previsível que existe."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lo = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lo) // 451
    mes, dia = divmod(h + lo - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def _feriados_calculados(ano: int) -> dict:
    p = _pascoa(ano)
    fixos = {
        f"{ano}-01-01": "Confraternização Universal",
        f"{ano}-04-21": "Tiradentes",
        f"{ano}-05-01": "Dia do Trabalho",
        f"{ano}-09-07": "Independência",
        f"{ano}-10-12": "Nossa Senhora Aparecida",
        f"{ano}-11-02": "Finados",
        f"{ano}-11-15": "Proclamação da República",
        f"{ano}-11-20": "Consciência Negra",
        f"{ano}-12-25": "Natal",
    }
    moveis = {
        (p - timedelta(days=47)).isoformat(): "Carnaval",
        (p - timedelta(days=2)).isoformat(): "Sexta-feira Santa",
        p.isoformat(): "Páscoa",
        (p + timedelta(days=60)).isoformat(): "Corpus Christi",
    }
    return {**fixos, **moveis}


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def aviso_de_feriado(data_iso: Optional[str]) -> Optional[str]:
    """"Dia 20/11 é feriado" — quando a data cai em dia sem banco.

    É PRA ISSO que o calendário de feriados existe neste produto. Conta que
    vence em feriado ou fim de semana não pode ser paga no dia: quem
    descobre isso na hora paga multa. O bot sabe a data e sabe o feriado —
    juntar as duas é o serviço.

    Devolve None quando é dia útil (o caso comum), pra quem chama só
    concatenar sem `if`.
    """
    if not data_iso or not _ISO_RE.match(str(data_iso)):
        return None
    try:
        d = date.fromisoformat(str(data_iso))
    except ValueError:
        return None
    if d.weekday() == 5:
        return "sábado"
    if d.weekday() == 6:
        return "domingo"
    return feriados(d.year).get(data_iso)


def feriados(ano: int) -> dict:
    """{data_iso: nome} dos feriados NACIONAIS do ano. Sem rede, nunca.

    A primeira versão consultava a BrasilAPI e caía no cálculo local. Duas
    auditorias depois, o que sobrou dessa arquitetura foi só o custo:

    1. ligar o aviso de feriado fez a API ser chamada UMA VEZ POR MENSAGEM —
       a suíte estourou o timeout de 120s e, em produção, seria uma chamada
       de rede no caminho síncrono de resposta ao usuário. Webhook lento é
       reentrega da Meta, e reentrega é mensagem duplicada;
    2. tirar a rede do caminho síncrono deixou a API, o cache e o descarte
       de lixo SEM CONSUMIDOR — no-op silencioso, que é a regra 5.

    O `/feriados/v1` da BrasilAPI devolve exatamente os nacionais, que o
    cálculo local já cobre (e ainda acrescenta Consciência Negra). O produto
    não usa feriado estadual em lugar nenhum. Então a fonte externa não
    trazia informação: trazia latência e um caminho de falha.

    Se um dia precisar de feriado ESTADUAL, aí sim vale uma fonte externa —
    com o mesmo cuidado de não ficar no caminho de resposta.
    """
    return _feriados_calculados(ano)
