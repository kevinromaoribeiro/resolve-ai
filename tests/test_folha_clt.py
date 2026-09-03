# -*- coding: utf-8 -*-
"""A folha nao pode mentir sobre o salario de ninguem.

Cada bloco aqui guarda um dos tres defeitos que vieram do FinControlPro.
Se algum voltar, o teste cai antes de a pessoa planejar o mes em cima do
numero errado.
"""
import financeiro as f


# --- 1. INSS: progressivo, nao aliquota unica -------------------------

def test_inss_e_progressivo_nao_aliquota_cheia():
    """Quem ganha 3.000 nao paga 12% sobre tudo — paga por fatia.

    O erro classico e cobrar a aliquota da faixa em que a pessoa caiu
    sobre o salario inteiro. Aqui isso daria 360,00.
    """
    assert f.inss(3000) < 3000 * 0.12
    esperado = (1518.00 * 0.075
                + (2793.88 - 1518.00) * 0.09
                + (3000 - 2793.88) * 0.12)
    assert abs(f.inss(3000) - round(esperado, 2)) < 0.02


def test_inss_congela_no_teto():
    """Acima do teto o valor para de subir. Sempre."""
    teto = f.inss(8157.41)
    assert f.inss(12000) == teto
    assert f.inss(50000) == teto
    assert f.inss(999999) == teto


def test_inss_do_salario_minimo_e_so_a_primeira_faixa():
    assert abs(f.inss(1518.00) - 113.85) < 0.02


def test_inss_nao_mistura_ano():
    """O teto de 2024 (7.786,02) nao pode reaparecer.

    Este era o defeito 1: faixas de 2024 convivendo com o desconto
    simplificado de 2025 na mesma conta.
    """
    for tab in f.TABELAS.values():
        tetos = [t for t, _ in tab.inss]
        assert 7786.02 not in tetos
        assert 1412.00 not in tetos


# --- 2. Base do IRRF: a menor entre completa e simplificada -----------

def test_simplificado_nao_desconta_inss_duas_vezes():
    """O desconto simplificado SUBSTITUI as deducoes, inclusive o INSS.

    Se o INSS entrasse dos dois lados, a base cairia demais e o imposto
    sairia menor do que a lei manda.
    """
    tab = f.tabela_do_ano(2026)
    bruto = 3000.0
    base = f.base_irrf(bruto, f.inss(bruto), 0, 0.0)
    assert base >= bruto - tab.simplificado - 0.01


def test_dependente_reduz_ou_mantem_nunca_aumenta():
    bruto = 9000.0
    d = f.inss(bruto)
    sem = f.base_irrf(bruto, d, 0)
    com = f.base_irrf(bruto, d, 3)
    assert com <= sem


def test_base_nunca_e_negativa():
    assert f.base_irrf(500, f.inss(500), 0) >= 0
    assert f.base_irrf(1000, f.inss(1000), 10, 900.0) >= 0


# --- 3. O redutor da Lei 15.270/2025 ---------------------------------

def test_ate_cinco_mil_nao_paga_imposto_em_2026():
    """A lei isentou ate 5.000/mes. Sem o redutor a conta cobrava."""
    for bruto in (4200, 4500, 4800, 5000):
        assert f.folha(bruto, ano=2026)["irrf"] == 0.0


def test_o_redutor_some_no_teto():
    """Em 7.350 o beneficio acabou: o imposto e o cheio."""
    r = f.folha(7350, ano=2026)
    assert r["redutor"] < 1.0
    assert f.folha(8000, ano=2026)["redutor"] == 0.0


def test_o_redutor_desce_em_reta_sem_degrau():
    """Entre 5.000 e 7.350 o imposto sobe suave — nao pode ter salto.

    Degrau aqui significaria alguem ganhar mais e receber menos.
    """
    anterior = -1.0
    for bruto in range(5000, 7400, 50):
        liq = f.folha(bruto, ano=2026)["liquido"]
        assert liq > anterior, "ganhar mais nao pode diminuir o liquido"
        anterior = liq


def test_o_redutor_nunca_vira_devolucao():
    """Abater mais que o imposto viraria dinheiro de volta. Nunca."""
    for bruto in range(4900, 7500, 25):
        r = f.folha(bruto, ano=2026)
        assert r["irrf"] >= 0.0
        assert r["redutor"] <= r["redutor"] + r["irrf"]


def test_dois_mil_e_cinco_reduziu_de_verdade():
    """A lei de 2026 tem que cobrar menos que 2025 na faixa do beneficio."""
    assert f.folha(6000, ano=2026)["irrf"] < f.folha(6000, ano=2025)["irrf"]


def test_2025_nao_tem_redutor():
    """A lei vale de 2026. Aplicar retroativo seria inventar isencao."""
    assert f.TAB_2025.redutor is None
    assert f.folha(6000, ano=2025)["redutor"] == 0.0


# --- 4. Adiantamento: sobre o BRUTO, e as parcelas fecham ------------

def test_as_duas_parcelas_somam_o_liquido():
    """Defeito 2, o mais visivel: dia 15 + dia 30 tem que dar o liquido.

    Na tela antiga o liquido aparecia logo acima das parcelas, e elas
    nunca somavam ele.
    """
    for bruto in (1518, 2000, 2500, 3200, 4500, 5000, 6000, 7350,
                  8000, 9500, 12000, 20000):
        r = f.folha(bruto)
        assert abs(r["parcela_15"] + r["parcela_30"] - r["liquido"]) < 0.02


def test_adiantamento_e_percentual_do_bruto_nao_do_liquido():
    bruto = 4000.0
    r = f.folha(bruto, adiantamento=0.40)
    assert abs(r["parcela_15"] - bruto * 0.40) < 0.01
    assert r["parcela_15"] > r["liquido"] * 0.40


def test_o_percentual_do_adiantamento_e_parametro():
    """40% e o costume, nao a lei. Empresa que paga 30% tem que caber."""
    bruto = 4000.0
    assert abs(f.folha(bruto, adiantamento=0.30)["parcela_15"] - 1200.0) < 0.01
    assert f.folha(bruto, adiantamento=0.0)["parcela_15"] == 0.0
    r = f.folha(bruto, adiantamento=0.0)
    assert abs(r["parcela_30"] - r["liquido"]) < 0.02


def test_adiantamento_maior_que_o_liquido_nao_vira_parcela_negativa():
    """Quem tem desconto pesado pode ter o vale maior que o liquido.

    O certo e a segunda parcela zerar, nao ficar negativa na tela.
    """
    r = f.folha(3000, outros_descontos=2000.0, adiantamento=0.90)
    assert r["parcela_30"] >= 0.0
    assert r["parcela_15"] <= r["liquido"] + 0.01


# --- 5. O liquido bate com o que descontou ---------------------------

def test_liquido_e_bruto_menos_o_que_saiu():
    bruto = 6500.0
    r = f.folha(bruto, outros_descontos=150.0, pensao=200.0)
    esperado = bruto - r["inss"] - r["irrf"] - 200.0 - 150.0
    assert abs(r["liquido"] - round(esperado, 2)) < 0.02


def test_liquido_nunca_negativo():
    r = f.folha(1518, outros_descontos=99999.0)
    assert r["liquido"] >= 0.0


# --- 6. Honestidade: tabela nao conferida sai marcada ----------------

def test_tabela_nao_conferida_avisa_que_e_estimativa(monkeypatch):
    """Numero de salario nao se chuta em silencio.

    O teste NAO pergunta se a tabela de 2026 esta conferida hoje — se
    perguntasse, ele se desligaria sozinho no dia em que alguem marcasse
    conferida=True, e a garantia sumiria junto. Ele monta uma tabela nao
    conferida e exige o aviso.
    """
    falsa = f.Tabela(ano=2031, inss=list(f.TAB_2025.inss),
                     irrf=list(f.TAB_2025.irrf), dependente=189.59,
                     simplificado=607.20, fonte="tabela de mentira",
                     conferida=False)
    monkeypatch.setitem(f.TABELAS, 2031, falsa)
    r = f.folha(4000, ano=2031)
    assert r["estimativa"] is True
    assert r["aviso"]
    assert "stimativa" in r["aviso"]


def test_ano_novo_nasce_nao_conferido():
    """Ano cuja portaria ninguem leu tem que nascer marcado.

    Marcar conferida=True sem alguem ter conferido a portaria e como o
    defeito nasce: a tela para de dizer "estimativa" e o chute vira fato.
    """
    assert f.TAB_2026.conferida is False, (
        "so marque 2026 como conferida depois de bater as faixas de INSS "
        "e IRRF com a portaria oficial do ano")


def test_tabela_conferida_nao_polui_a_tela_com_aviso():
    r = f.folha(4000, ano=2025)
    assert r["estimativa"] is False
    assert r["aviso"] == ""


def test_toda_tabela_declara_a_fonte():
    for tab in f.TABELAS.values():
        assert tab.fonte.strip(), "tabela sem fonte nao entra"
    assert f.folha(3000)["fonte"]


# --- 7. Entrada suja nao derruba a tela ------------------------------

def test_bruto_vazio_zero_ou_lixo_nao_estoura():
    for ruim in (0, None, "", -500, "abc", [], {}):
        r = f.folha(ruim)
        assert r["liquido"] == 0.0
        assert r["parcela_15"] == 0.0
        assert r["parcela_30"] == 0.0


def test_ano_desconhecido_cai_na_tabela_mais_nova():
    assert f.tabela_do_ano(1998).ano == max(f.TABELAS)
    assert f.tabela_do_ano(2099).ano == max(f.TABELAS)
    assert f.folha(3000, ano=2099)["liquido"] > 0


def test_tudo_arredondado_em_centavos():
    """Salario com fracao de centavo na tela e sintoma de conta solta."""
    r = f.folha(4327.77, dependentes=2)
    for campo in ("inss", "irrf", "liquido", "parcela_15", "parcela_30"):
        assert round(r[campo], 2) == r[campo]
