# -*- coding: utf-8 -*-
"""M2.5 item 1 — a tabela oficial de SP, conferida pelo dono.

O que estes testes protegem nao e "o codigo roda". E: **a data que sai daqui
e a data do edital.** Data errada nao quebra nada, nao aparece em log, passa
por revisao de codigo e chega no usuario como se fosse verdade — foi por isso
que o dono teve que conferir a tabela na mao.

Tres coisas viram teste aqui:
  1. os dez dias do IPVA 2026 e os seis meses do licenciamento 2026, LITERAIS;
  2. a recusa de inventar ano nao publicado (o calendario muda todo ano);
  3. o prazo VENCIDO — hoje e 17/08/2026, entao final 1 e 2 ja perderam o
     licenciamento deste ano. Esse e o caso que mais vai aparecer.
"""
from datetime import date, timedelta

import pytest

import calendario
import db
import scheduler
import tempo
import wa_bot
from conftest import responder


# ---------------------------------------------------------------------------
# 1. A TABELA E A DO EDITAL
# ---------------------------------------------------------------------------
# Conferido pelo dono contra a Sefaz-SP em 17/08/2026. Cota unica / 1a parcela.
IPVA_OFICIAL_SP_2026 = {1: "2026-01-12", 2: "2026-01-13", 3: "2026-01-14",
                        4: "2026-01-15", 5: "2026-01-16", 6: "2026-01-19",
                        7: "2026-01-20", 8: "2026-01-21", 9: "2026-01-22",
                        0: "2026-01-23"}

# Licenciamento 2026 (carros, motos, onibus, reboques): MES LIMITE por final.
LICENCIAMENTO_OFICIAL_SP_2026 = {1: 7, 2: 7, 3: 8, 4: 8, 5: 9, 6: 9,
                                 7: 10, 8: 10, 9: 11, 0: 12}


def test_ipva_2026_bate_com_o_edital():
    assert calendario.IPVA[("SP", 2026)] == IPVA_OFICIAL_SP_2026


def test_licenciamento_2026_bate_com_o_edital():
    assert (calendario.LICENCIAMENTO_MES[("SP", 2026)]
            == LICENCIAMENTO_OFICIAL_SP_2026)


@pytest.mark.parametrize("final", range(10))
def test_licenciamento_cai_no_mes_certo(final):
    """A data gerada tem que estar DENTRO do mes limite do edital."""
    data = calendario.LICENCIAMENTO[("SP", 2026)][final]
    assert int(data[5:7]) == LICENCIAMENTO_OFICIAL_SP_2026[final], data
    assert data[:4] == "2026"


def test_licenciamento_e_o_ultimo_dia_util_do_mes():
    """31/10/2026 e SABADO. O prazo nao se estende por isso — quem deixa pro
    dia 31 simplesmente nao consegue pagar. Entao o lembrete e na sexta."""
    assert calendario.LICENCIAMENTO[("SP", 2026)][7] == "2026-10-30"
    assert calendario.LICENCIAMENTO[("SP", 2026)][8] == "2026-10-30"
    # os meses que terminam em dia util ficam no ultimo dia mesmo
    assert calendario.LICENCIAMENTO[("SP", 2026)][1] == "2026-07-31"
    assert calendario.LICENCIAMENTO[("SP", 2026)][0] == "2026-12-31"


@pytest.mark.parametrize("tipo", ["IPVA", "LICENCIAMENTO"])
def test_nenhuma_data_cai_em_dia_sem_banco(tipo):
    for (uf, ano), tabela in getattr(calendario, tipo).items():
        for final, iso in tabela.items():
            assert calendario.aviso_de_feriado(iso) is None, \
                f"{tipo} {uf}/{ano} final {final}: {iso} nao tem banco aberto"


def test_finais_pareados_tem_a_mesma_data():
    """O edital pareia 1-2, 3-4, 5-6, 7-8. Se a tabela 'consertar' isso pra
    ficar com dez datas distintas, ela deixou de ser o edital."""
    t = calendario.LICENCIAMENTO[("SP", 2026)]
    for a, b in ((1, 2), (3, 4), (5, 6), (7, 8)):
        assert t[a] == t[b], f"finais {a} e {b} deviam ter o mesmo prazo"
    assert t[9] != t[0]
    assert max(t.values()) == t[0], "final 0 e sempre o ultimo"


# ---------------------------------------------------------------------------
# 2. ANO NAO PUBLICADO: RECUSA, NAO CHUTE
# ---------------------------------------------------------------------------
def test_so_existe_ano_que_o_dono_conferiu():
    """Trava contra a proxima 'ajudinha': acrescentar 2027 de cabeca.

    A versao anterior desta tabela tinha um 2027 inteiro que eu inventei
    deslocando os dias do 2026 pra fugir do fim de semana. Passava em todos
    os testes — inclusive no que exige dez datas distintas e no que proibe
    sabado — porque teste de FORMA nao ve data errada.
    """
    for ano in {a for _, a in calendario.IPVA}:
        assert ano in calendario.ANOS_CONFERIDOS, (
            f"IPVA {ano} nao esta na lista de anos conferidos contra o "
            f"edital. Conferir, e so entao acrescentar em ANOS_CONFERIDOS.")
    for ano in {a for _, a in calendario.LICENCIAMENTO_MES}:
        assert ano in calendario.ANOS_CONFERIDOS


def test_ano_seguinte_nao_e_inventado():
    assert calendario.vencimentos("SP", 3, 2027) == []


def test_o_bot_avisa_quando_a_tabela_esta_acabando():
    """Manutencao anual que depende de memoria e manutencao que nao acontece."""
    # em agosto do ultimo ano coberto, o dono precisa ser avisado
    assert calendario.tabela_expirando(date(2026, 8, 17)) is not None
    # em janeiro ainda tem o ano inteiro pela frente
    assert calendario.tabela_expirando(date(2026, 1, 10)) is None


# ---------------------------------------------------------------------------
# 3. PRAZO VENCIDO — o caso de HOJE
# ---------------------------------------------------------------------------
@pytest.fixture
def hoje_agosto(monkeypatch):
    d = date(2026, 8, 17)
    monkeypatch.setattr(tempo, "hoje", lambda: d)
    return d


def test_prazo_vencido_nao_vira_lembrete(usuario, hoje_agosto):
    """Final 1: licenciamento venceu 31/07 e o IPVA venceu em janeiro."""
    responder("minha placa e ABC1D21")
    itens = db.list_items(usuario["id"])
    assert not itens, f"criou lembrete de data vencida: {itens}"


def test_prazo_vencido_e_dito_na_cara(usuario, hoje_agosto):
    r = responder("minha placa e ABC1D21").lower()
    assert "licenciamento" in r
    assert "passou" in r or "venceu" in r or "prazo" in r
    # nao pode dizer que guardou o que nao guardou
    assert "pronto" not in r[:40]


def test_prazo_vencido_nao_pula_calado_para_o_ano_seguinte(usuario,
                                                           hoje_agosto):
    """O silencio aqui e o defeito: a pessoa acha que esta coberta."""
    r = responder("minha placa e ABC1D21")
    assert "2027" in r, ("a resposta precisa dizer o que acontece com o ano "
                         "que vem, senao a pessoa supoe que ja esta agendado")
    for it in db.list_items(usuario["id"]):
        assert (it.get("data_vencimento") or "")[:4] != "2027"


def test_prazo_que_ainda_da_tempo_vira_lembrete(usuario, hoje_agosto):
    """Final 3: 31/08 — duas semanas. Esse ainda e servico."""
    responder("minha placa e ABC1D23")
    itens = db.list_items(usuario["id"])
    datas = [i["data_vencimento"] for i in itens]
    assert "2026-08-31" in datas, datas
    # o IPVA de 2026 ja passou e o de 2027 nao existe: so vem licenciamento
    assert len(itens) == 1, itens


def test_final_zero_ainda_tem_o_ano_inteiro(usuario, hoje_agosto):
    responder("minha placa e ABC1D20")
    datas = [i["data_vencimento"] for i in db.list_items(usuario["id"])]
    assert datas == ["2026-12-31"], datas


# ---------------------------------------------------------------------------
# 4. IPVA: o que o bot diz sobre parcela e desconto
# ---------------------------------------------------------------------------
@pytest.fixture
def hoje_janeiro(monkeypatch):
    d = date(2026, 1, 5)
    monkeypatch.setattr(tempo, "hoje", lambda: d)
    return d


def test_ipva_no_futuro_conta_do_parcelamento(usuario, hoje_janeiro):
    r = responder("minha placa e ABC1D21").lower()
    assert "ipva" in r
    assert "5x" in r or "parcel" in r
    assert "desconto" in r


def test_o_bot_nao_agenda_parcela_que_nao_pode_garantir(usuario, hoje_janeiro):
    """12/04/2026 e DOMINGO. "A parcela cai no mesmo dia todo mes" e regra
    de boca: no calendario real ela desliza pro dia util. Mencionar o
    parcelamento e servico; agendar cinco datas chutadas e o contrario."""
    responder("minha placa e ABC1D21")
    ipva = [i for i in db.list_items(usuario["id"])
            if "ipva" in (i["descricao"] or "").lower()]
    assert len(ipva) == 1, f"so a cota unica/1a parcela vira item: {ipva}"
    assert ipva[0]["data_vencimento"] == "2026-01-12"


# ---------------------------------------------------------------------------
# 5. ANTECEDENCIA REAL
# ---------------------------------------------------------------------------
def test_licenciamento_avisa_com_antecedencia_de_verdade(usuario):
    """Avisar de licenciamento em D-3 e avisar tarde: se cair emenda de
    feriado, a pessoa nao resolve. Obrigacao de veiculo avisa em D-30."""
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Veículo",
                descricao="Licenciamento (final 3)",
                data_vencimento=(tempo.hoje() + timedelta(days=30)).isoformat(),
                status="pendente")
    saida = scheduler.check_due_items(ref=tempo.hoje())
    meus = [d for d in saida if d["user_id"] == usuario["id"]]
    assert meus, "D-30 de obrigacao de veiculo tem que disparar"


def test_conta_comum_nao_ganha_aviso_de_30_dias(usuario):
    """A antecedencia maior e SO pra obrigacao anual. Se vazar pro resto, o
    bot vira aquele que avisa da luz um mes antes — e a pessoa silencia."""
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="Luz", valor_reais=180.0,
                data_vencimento=(tempo.hoje() + timedelta(days=30)).isoformat(),
                status="pendente")
    saida = scheduler.check_due_items(ref=tempo.hoje())
    assert not [d for d in saida if d["user_id"] == usuario["id"]]


def test_a_janela_de_consulta_cobre_o_maior_aviso():
    """O defeito que este teste tranca custou meia hora de debug cego.

    `DUE_WINDOW_DAYS` e o filtro de SQL; `DUE_ALERT_DAYS*` e o filtro em
    Python. Se a janela for menor que o maior dia de aviso, o item nem chega
    a ser lido — o aviso some SEM erro, sem log, com a lista vindo vazia. E
    o `wa_bot` sobrescreve os dois no import, entao a invariante tem que
    valer DEPOIS de importar producao, nao so no modulo cru.
    """
    import wa_bot                                          # noqa: F401
    dias = set(scheduler.DUE_ALERT_DAYS)
    for v in scheduler.DUE_ALERT_DAYS_POR_CATEGORIA.values():
        dias |= set(v)
    assert scheduler.DUE_WINDOW_DAYS >= max(dias), (
        f"janela {scheduler.DUE_WINDOW_DAYS} menor que o maior aviso "
        f"{max(dias)}: o aviso mais distante nunca dispara")


def test_a_politica_do_wa_bot_preserva_o_aviso_de_veiculo():
    """O wa_bot corta o aviso comum pra D-1 (nao encher o saco). Se esse
    corte levar junto a regua de veiculo, o M2.5 volta a avisar tarde."""
    import wa_bot                                          # noqa: F401
    assert scheduler.DUE_ALERT_DAYS == {1}
    assert 30 in scheduler.DUE_ALERT_DAYS_POR_CATEGORIA["Veículo"]


def test_a_resposta_diz_que_o_licenciamento_e_um_mes_inteiro(usuario,
                                                             hoje_agosto):
    """M33 — "e prazo de MES, nao data marcada" foi pedido explicito do dono
    e nao tinha um teste sequer. Escrever so "vence 31/08" faz a pessoa
    deixar pro dia 31 — que e justamente o dia em que ela pode nao conseguir
    resolver."""
    r = responder("minha placa e ABC1D23")
    assert "agosto" in r.lower(), r
    assert "último dia" in r.lower() or "ultimo dia" in r.lower(), r


def test_quem_recebe_lembrete_tambem_e_avisado_sobre_2027(usuario,
                                                          hoje_agosto):
    """Achado da auditoria (P1-6): a frase sobre o ano seguinte so existia no
    ramo "nao criei nada". Quem RECEBE o lembrete terminava lendo "eu te
    aviso com antecedencia" e supunha estar coberto — inclusive o final 0,
    cujo licenciamento cai em 31/12 e cujo IPVA vem 23 dias depois."""
    r = responder("minha placa e ABC1D20")
    assert "2026-12-31" in [i["data_vencimento"]
                            for i in db.list_items(usuario["id"])]
    assert "2027" in r, r


def test_a_nota_do_parcelamento_nao_e_so_de_janeiro(usuario, hoje_agosto):
    """A nota estava presa ao item de IPVA CRIADO, entao aparecia so em
    janeiro. Quem manda a placa em agosto e justamente quem ainda nao sabe
    que existe cota unica com desconto."""
    r = responder("minha placa e ABC1D23").lower()
    assert "desconto" in r or "5x" in r, r


def test_quem_perdeu_tudo_tambem_ouve_do_desconto(usuario, hoje_agosto):
    """Finais 1 e 2 perderam IPVA e licenciamento deste ano — e eram os
    UNICOS sem a nota de cota unica/parcelamento, porque ela morava so no
    ramo de sucesso. Quem mais precisa da informacao pro ano que vem."""
    r = responder("minha placa e ABC1D21").lower()
    assert "desconto" in r, r
    assert "5x" in r, r


def test_em_2027_a_unica_resposta_da_placa_nao_promete_nada(usuario,
                                                            monkeypatch):
    """A partir de 01/01/2027 a tabela acaba e o ramo "nao tenho o
    calendario" vira a UNICA resposta que o bot da pra placa — pra todo
    mundo. Era o texto da promessa falsa no lugar onde ela mais vale, e sem
    teste nenhum (achado do auditor na rodada 3)."""
    monkeypatch.setattr(tempo, "hoje", lambda: date(2027, 3, 10))
    r = responder("minha placa e ABC1D23")
    baixo = r.lower()
    assert not db.list_items(usuario["id"]), "criou lembrete sem tabela"
    # INVARIANTE DE POSICAO, e nao lista de palavras proibidas.
    #
    # A primeira versao barrava so o literal "sozinho" — qualquer redacao
    # nova ("pode deixar comigo", "eu te aviso quando chegar a hora")
    # passava, e o teste contava como cobertura inexistente (condicao
    # nomeada pelo auditor na rodada 4). Proibir as palavras tambem nao
    # serve: "me manda a placa que EU CRIO os lembretes" e legitimo — a
    # promessa esta condicionada a pessoa agir.
    #
    # O que separa uma da outra e a POSICAO: toda promessa tem que vir
    # DEPOIS do pedido. Promessa antes do pedido e promessa que o bot faz
    # sozinho, e essa ele nao cumpre.
    pedido = baixo.find("me manda a placa de novo")
    assert pedido >= 0, f"a resposta nao pede a placa de volta: {r}"
    for promessa in ("sozinho", "eu crio", "eu te aviso", "pode deixar",
                     "deixa comigo", "fico de olho"):
        pos = baixo.find(promessa)
        assert pos < 0 or pos > pedido, (
            f"promessa {promessa!r} antes do pedido — o bot se compromete "
            f"a agir sem a pessoa fazer nada: {r}")


def test_a_ressalva_de_2027_e_a_ultima_coisa_que_a_pessoa_le(usuario,
                                                             hoje_agosto):
    """P2-7 era exatamente A ORDEM: com a ressalva no meio, a ultima linha
    voltava a ser "eu te aviso com antecedencia", sem qualificacao — a frase
    que faz a pessoa parar de procurar a informacao em outro lugar."""
    r = responder("minha placa e ABC1D20").strip()
    assert r.endswith("me diz que eu guardo."), r[-120:]
    assert r.index("2027") > r.index("Eu te aviso com antecedência"), r
