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
  1. pegue o edital na Sefaz-SP e CONFIRA com os olhos
  2. acrescente o ano em `IPVA` (dias) e `LICENCIAMENTO_MES` (meses)
  3. acrescente o ano em `ANOS_CONFERIDOS` — sem isso o teste reprova, e é
     essa trava que impede alguém de "deduzir" o calendário do ano seguinte
  4. rode `pytest tests/test_m22_calendario.py tests/test_m25_calendario_oficial.py`
  5. sem o ano na tabela o bot NÃO cria lembrete nenhum — ele não chuta a
     data do ano anterior, porque o calendário muda todo ano. O dono é
     avisado com 150 dias de antecedência (`tabela_expirando`).
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
# meses de erro. O que trava isso agora e a tabela ser a FONTE transcrita
# (dia do IPVA, mes do licenciamento) e o teste comparar com o edital literal.
# SÓ ENTRA ANO QUE UM HUMANO CONFERIU CONTRA O EDITAL.
#
# A primeira versão desta tabela trazia um 2027 inteiro que eu produzi
# deslocando os dias de 2026 pra fugir do fim de semana. Ele passava em TODOS
# os testes que existiam — dez datas distintas, nenhuma em sábado, final 0 por
# último — porque teste de forma não enxerga data errada. Seriam 365 dias de
# lembrete no dia errado, sem log, sem exceção, sem nada quebrando.
#
# O calendário de SP não é derivável: os dias saem em dias ÚTEIS consecutivos
# de janeiro, mas qual é o primeiro deles é decisão da Sefaz, não regra. Então
# ano sem edital publicado = ano fora da tabela = nenhum lembrete. É pior pro
# usuário e é o único jeito honesto.
ANOS_CONFERIDOS = {2026}

IPVA = {
    # Conferido pelo dono contra a Sefaz-SP em 17/08/2026.
    # Cota única / 1ª parcela, automóveis.
    ("SP", 2026): {1: "2026-01-12", 2: "2026-01-13", 3: "2026-01-14",
                   4: "2026-01-15", 5: "2026-01-16", 6: "2026-01-19",
                   7: "2026-01-20", 8: "2026-01-21", 9: "2026-01-22",
                   0: "2026-01-23"},
}

# O IPVA de SP também pode ser pago em 5x (parcelas 2 a 5 em fev/mar/abr/mai)
# ou em cota única com desconto. O bot MENCIONA, e não agenda: 12/04/2026 é
# domingo, ou seja, "a parcela cai no mesmo dia todo mês" é regra de boca que
# o calendário real não cumpre. Mencionar é serviço; agendar cinco datas
# chutadas é o oposto do que este módulo existe pra fazer.
NOTA_IPVA = ("_Dá pra pagar em cota única com desconto ou parcelar em até 5x "
             "— as outras parcelas caem de fevereiro a maio._")

# A MESMA INFORMAÇÃO, pra quem já perdeu o deste ano. Ela mora aqui e não
# solta no `wa_bot` porque a primeira versão tinha DUAS cópias do texto: a
# constante (que só era alcançável de 01 a 23 de janeiro, ou seja, quase
# nunca) e uma cópia à mão que perdia o "de fevereiro a maio". Duas versões
# da mesma informação sobre dinheiro, e a canônica era a que não rodava.
NOTA_IPVA_ANO_QUE_VEM = ("_No ano que vem dá pra pagar em cota única com "
                         "desconto ou parcelar em até 5x, de fevereiro a "
                         "maio._")

# LICENCIAMENTO — o edital dá MÊS LIMITE, não dia marcado.
#
# Guardar o mês e derivar o dia é o que mantém a tabela igual à fonte. Guardar
# a data já pronta convida a errar na transcrição, e foi exatamente assim que
# a versão anterior nasceu com o final 0 repetindo a data do final 7.
#
# Os finais vêm PAREADOS de propósito (1-2, 3-4, 5-6, 7-8): é assim no edital.
# Um teste anterior exigia "dez datas distintas" e teria reprovado a tabela
# certa — teste que pede simetria onde a fonte não tem.
LICENCIAMENTO_MES = {
    # Conferido pelo dono em 17/08/2026 — carros, motos, ônibus e reboques.
    ("SP", 2026): {1: 7, 2: 7, 3: 8, 4: 8, 5: 9, 6: 9,
                   7: 10, 8: 10, 9: 11, 0: 12},
}


def _ultimo_dia_util(ano: int, mes: int) -> date:
    """O último dia do mês em que dá pra RESOLVER.

    O prazo não se estende porque caiu no fim de semana — 31/10/2026 é sábado
    e continua sendo o limite legal. Quem só for lembrado no dia 31 encontra
    o banco fechado e perde o prazo. Então o lembrete anda pra trás, nunca
    pra frente.
    """
    d = date(ano + (mes == 12), (mes % 12) + 1, 1) - timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in feriados(ano):
        d -= timedelta(days=1)
    return d


def _licenciamento_derivado() -> dict:
    return {chave: {final: _ultimo_dia_util(chave[1], mes).isoformat()
                    for final, mes in tabela.items()}
            for chave, tabela in LICENCIAMENTO_MES.items()}


ANOS_COBERTOS = sorted({ano for _, ano in IPVA}
                       | {ano for _, ano in LICENCIAMENTO_MES})
UFS_COBERTAS = sorted({uf for uf, _ in IPVA}
                      | {uf for uf, _ in LICENCIAMENTO_MES})

# Aviso ao DONO quando a manutenção anual está chegando. Manutenção que
# depende de alguém lembrar em dezembro é manutenção que não acontece — e
# esta falha é silenciosa: o bot simplesmente para de criar lembrete de carro,
# sem erro nenhum em lugar nenhum.
AVISAR_TABELA_A_VENCER_DIAS = 150


def tabela_expirando(hoje: Optional[date] = None) -> Optional[str]:
    """Recado pro dono quando o último ano coberto está acabando, ou None."""
    if not ANOS_COBERTOS:
        return "o calendário de IPVA/licenciamento está VAZIO"
    hoje = hoje or date.today()
    ultimo = max(ANOS_COBERTOS)
    if hoje.year > ultimo:
        return (f"o calendário de IPVA/licenciamento parou em {ultimo} — "
                f"nenhum lembrete de carro está sendo criado")
    faltam = (date(ultimo, 12, 31) - hoje).days
    if faltam > AVISAR_TABELA_A_VENCER_DIAS:
        return None
    return (f"o calendário de IPVA/licenciamento vai até {ultimo} "
            f"(faltam {faltam} dias) — pegar o edital de {ultimo + 1} "
            f"na Sefaz")


def vencimentos(uf: str, final_placa, ano: int,
                hoje: Optional[date] = None) -> list:
    """[{tipo, data, rotulo, prazo, passado}] para este final neste ano.

    Lista VAZIA quando não se sabe — UF fora da tabela, ano não publicado,
    final inválido. Vazio é resposta legítima: melhor não criar lembrete do
    que criar no dia errado.

    `passado` só é calculado quando `hoje` é informado, e existe porque o
    prazo VENCIDO é o caso mais comum no meio do ano: em agosto, o IPVA de
    janeiro já foi e o licenciamento dos finais 1 e 2 também. Quem chama
    precisa distinguir "não tenho essa data" de "essa data já passou" — as
    duas geram zero lembretes e exigem respostas completamente diferentes.
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
        item = {"tipo": tipo, "data": data,
                "rotulo": f"{rotulo} (final {final})",
                "passado": bool(hoje and data < hoje.isoformat())}
        if tipo == "licenciamento":
            # O prazo do licenciamento é o MÊS. `data` é só o último dia em
            # que dá pra pagar — quem recebe precisa dos dois pra escrever
            # "você tem julho inteiro" em vez de "vence dia 31".
            item["prazo_mes"] = LICENCIAMENTO_MES[(uf, ano)][final]
        saida.append(item)
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


# Derivado AQUI NO FIM de propósito: `_ultimo_dia_util` consulta `feriados`,
# que só existe depois. Módulo-nível, não por chamada — a tabela é imutável e
# recalcular a Páscoa a cada consulta seria trabalho por nada.
LICENCIAMENTO = _licenciamento_derivado()
