# -*- coding: utf-8 -*-
"""Folha de pagamento CLT — INSS, IRRF, adiantamento e parcela final.

Por que este arquivo existe: a conta que veio do FinControlPro estava errada
em tres pontos, e errar aqui e pior do que nao ter a conta. O numero aparece
pra pessoa como "o que voce recebe", e se estiver errado ela planeja o mes
inteiro em cima de uma mentira nossa.

Os tres defeitos, e o conserto:

1. TABELA VELHA. Usava faixas de INSS de 2024 (teto 7.786,02) junto com o
   desconto simplificado de 2025 (607,20). Duas leis diferentes na mesma
   conta. Agora cada ano e uma tabela fechada, datada e com fonte; e a
   tabela diz se foi conferida.

2. ADIANTAMENTO SOBRE O LIQUIDO. Calculava 40% do liquido. O adiantamento
   do dia 15 e percentual do BRUTO, e nao sofre desconto nenhum — INSS e
   IRRF sao retidos so no fechamento. Por isso as duas parcelas nunca
   fechavam com o liquido exibido logo acima delas na mesma tela.

3. O REDUTOR DE 2026 NAO EXISTIA. A Lei 15.270/2025 nao mexeu nas faixas:
   ela abate o imposto ja calculado. Sem o redutor, a conta cobra imposto
   de quem a lei isentou.

Regra de ouro deste modulo: quando a tabela do ano nao foi conferida por um
humano, o resultado sai com `aviso` preenchido e `estimativa=True`, e quem
exibe TEM que mostrar isso. Numero de salario nao se chuta em silencio.
"""
from __future__ import annotations

CENTAVO = 2


def _r(v: float) -> float:
    """Arredonda em centavos, como a folha faz."""
    return round(v + 1e-9, CENTAVO)


class Tabela:
    """As faixas de um ano. `conferida` e o que separa numero de chute."""

    def __init__(self, ano, inss, irrf, dependente, simplificado,
                 redutor=None, fonte="", conferida=False):
        self.ano = ano
        self.inss = inss                    # [(teto_da_faixa, aliquota)]
        self.irrf = irrf                    # [(teto_da_base, aliquota, parcela)]
        self.dependente = dependente
        self.simplificado = simplificado
        self.redutor = redutor              # (piso, teto, a, b) da Lei 15.270
        self.fonte = fonte
        self.conferida = conferida


# --- 2025 ---------------------------------------------------------------
# Salario minimo 1.518,00. IRRF na redacao vigente a partir de maio/2025,
# que subiu a isencao para 2.428,80.
TAB_2025 = Tabela(
    ano=2025,
    inss=[(1518.00, 0.075), (2793.88, 0.09),
          (4190.83, 0.12), (8157.41, 0.14)],
    irrf=[(2428.80, 0.0, 0.0), (2826.65, 0.075, 182.16),
          (3751.05, 0.15, 394.16), (4664.68, 0.225, 675.49),
          (float("inf"), 0.275, 908.73)],
    dependente=189.59,
    simplificado=607.20,
    redutor=None,
    fonte="Portaria de reajuste do INSS de 2025 e tabela do IRRF de maio/2025",
    conferida=True,
)

# --- 2026 ---------------------------------------------------------------
# A Lei 15.270/2025 isenta ate 5.000,00 por mes e reduz o imposto ate
# 7.350,00. Ela NAO alterou as faixas: age como abatimento do imposto ja
# calculado.
#
# As faixas de INSS de 2026 mudam junto com o salario minimo e saem em
# portaria de janeiro. Enquanto ninguem conferir a portaria, este ano herda
# as faixas de 2025 e sai marcado como NAO CONFERIDO — o resultado carrega
# `aviso` e a tela e obrigada a dizer "estimativa".
TAB_2026 = Tabela(
    ano=2026,
    inss=list(TAB_2025.inss),
    irrf=list(TAB_2025.irrf),
    dependente=TAB_2025.dependente,
    simplificado=TAB_2025.simplificado,
    redutor=(5000.00, 7350.00, 1095.11, 0.149018),
    fonte=("Lei 15.270/2025 (redutor). Faixas herdadas de 2025 — "
           "conferir a portaria de reajuste de 2026."),
    conferida=False,
)

TABELAS = {2025: TAB_2025, 2026: TAB_2026}


def tabela_do_ano(ano: int) -> Tabela:
    """A tabela do ano, ou a mais recente que existe (nunca estoura)."""
    if ano in TABELAS:
        return TABELAS[ano]
    return TABELAS[max(TABELAS)]


def inss(bruto: float, ano: int = 2026) -> float:
    """INSS progressivo: cada faixa cobra a propria aliquota sobre a fatia.

    Nao e "aliquota unica pela faixa em que caiu" — esse e o erro classico.
    Acima do teto, o valor congela.
    """
    if bruto <= 0:
        return 0.0
    tab = tabela_do_ano(ano)
    total, piso = 0.0, 0.0
    for teto, aliq in tab.inss:
        if bruto <= piso:
            break
        fatia = min(bruto, teto) - piso
        total += fatia * aliq
        piso = teto
    return _r(total)


def base_irrf(bruto: float, desconto_inss: float, dependentes: int = 0,
              pensao: float = 0.0, ano: int = 2026) -> float:
    """A base menor entre o modelo completo e o desconto simplificado.

    Completo: bruto - INSS - dependentes - pensao.
    Simplificado: bruto - desconto unico. Ele SUBSTITUI todas as deducoes,
    inclusive o INSS — por isso o INSS nao entra dos dois lados.
    A pessoa fica com a que der menos imposto, que e a base menor.
    """
    tab = tabela_do_ano(ano)
    completa = bruto - desconto_inss - (dependentes * tab.dependente) - pensao
    simples = bruto - tab.simplificado
    return max(0.0, min(completa, simples))


def _imposto_da_tabela(base: float, tab: Tabela) -> float:
    for teto, aliq, parcela in tab.irrf:
        if base <= teto:
            return max(0.0, base * aliq - parcela)
    return 0.0


def redutor_da_lei(bruto: float, imposto: float, tab: Tabela) -> float:
    """Lei 15.270/2025: abate o imposto ja calculado.

    Ate o piso, zera. Entre piso e teto, cai em reta ate zero. Nunca abate
    mais do que o imposto — senao viraria devolucao.
    """
    if not tab.redutor or imposto <= 0:
        return 0.0
    piso, teto, a, b = tab.redutor
    if bruto <= piso:
        return imposto
    if bruto > teto:
        return 0.0
    return min(imposto, max(0.0, a - b * bruto))


def irrf(bruto: float, desconto_inss: float, dependentes: int = 0,
         pensao: float = 0.0, ano: int = 2026) -> float:
    tab = tabela_do_ano(ano)
    base = base_irrf(bruto, desconto_inss, dependentes, pensao, ano)
    imposto = _imposto_da_tabela(base, tab)
    return _r(max(0.0, imposto - redutor_da_lei(bruto, imposto, tab)))


def folha(bruto: float, dependentes: int = 0, pensao: float = 0.0,
          outros_descontos: float = 0.0, adiantamento: float = 0.40,
          ano: int = 2026) -> dict:
    """A folha inteira: o que desconta, o que cai no 15 e o que cai no 30.

    `adiantamento` e o percentual do BRUTO que a empresa paga no dia 15.
    Nao e lei — a CLT so exige o pagamento ate o 5o dia util do mes
    seguinte. E politica de cada empresa, entao e parametro, com 40% de
    padrao por ser o mais comum. Sobre o adiantamento nao incide INSS nem
    IRRF: tudo e retido no fechamento.

    Invariante que o codigo antigo quebrava:
        parcela_15 + parcela_30 == liquido
    """
    tab = tabela_do_ano(ano)
    try:
        bruto = max(0.0, float(bruto or 0))
    except (TypeError, ValueError):
        bruto = 0.0

    if bruto <= 0:
        return {"bruto": 0.0, "inss": 0.0, "irrf": 0.0, "base_irrf": 0.0,
                "redutor": 0.0, "liquido": 0.0, "parcela_15": 0.0,
                "parcela_30": 0.0, "ano": tab.ano, "fonte": tab.fonte,
                "estimativa": not tab.conferida, "aviso": ""}

    d_inss = inss(bruto, ano)
    base = base_irrf(bruto, d_inss, dependentes, pensao, ano)
    imposto_cheio = _imposto_da_tabela(base, tab)
    abatido = redutor_da_lei(bruto, imposto_cheio, tab)
    d_irrf = _r(max(0.0, imposto_cheio - abatido))

    liquido = _r(bruto - d_inss - d_irrf - pensao - outros_descontos)
    liquido = max(0.0, liquido)
    p15 = _r(bruto * adiantamento)
    # o resto e o resto: garante que as duas parcelas somem o liquido.
    p30 = _r(liquido - p15)
    if p30 < 0:  # adiantamento maior que o liquido: acerto no mes seguinte
        p15, p30 = liquido, 0.0

    aviso = ""
    if not tab.conferida:
        aviso = ("Estimativa: a tabela de %d ainda nao foi conferida. "
                 "Confira no seu holerite." % tab.ano)

    return {"bruto": _r(bruto), "inss": d_inss, "irrf": d_irrf,
            "base_irrf": _r(base), "redutor": _r(abatido),
            "liquido": liquido, "parcela_15": p15, "parcela_30": p30,
            "ano": tab.ano, "fonte": tab.fonte,
            "estimativa": not tab.conferida, "aviso": aviso}
