"""M2.2 — lembretes que o bot pode saber sozinho (IPVA, licenciamento, feriados).

REGRA DO BLOCO, dada pelo Kevin: fonte externa fora do ar **não pode fazer
lembrete sumir nem nascer com data errada**. As duas metades importam — a
segunda é a mais fácil de violar, porque data errada parece que funcionou.

Desenho que decorre disso: o calendário de IPVA/licenciamento não é uma API,
é uma TABELA publicada por estado a cada ano. Então a tabela versionada no
repo é a fonte primária (dado, não lógica), e o que vier de fora só pode
CONFIRMAR — nunca sobrescrever com algo que não passe na validação.
"""
import datetime as _dt

import pytest

import calendario
import db
import tempo
import wa_bot
from conftest import TELEFONE, responder


# --- o ano seguinte, INVENTADO de proposito ------------------------------

@pytest.fixture(autouse=True)
def tabela_ficticia_do_ano_seguinte(monkeypatch):
    """DADO FALSO, e por isso ele mora aqui dentro e nao em producao.

    A tabela de producao tem UM ano — 2026 — porque so esse foi conferido
    contra o edital da Sefaz (ver test_m25_calendario_oficial.py). Mas
    metade do que ESTE arquivo testa e a logica de VIRADA DE ANO do wa_bot:
    item do ano passado nao bloqueia o do ano novo, "a proxima ocorrencia"
    e nao "a ultima", `>=` no dia exato do vencimento. Sem um segundo ano
    na tabela, todos esses testes passam com os dois lados vazios — e teste
    que passa vazio e pior que teste ausente, porque parece cobertura.

    As datas abaixo NAO sao o calendario de 2027. Se alguem copiar isso pra
    `calendario.py`, o `test_so_existe_ano_que_o_dono_conferiu` reprova.
    """
    ano = tempo.hoje().year + 1
    # dez dias uteis seguidos a partir da segunda segunda-feira de janeiro:
    # e a FORMA do calendario de SP, o suficiente pra exercitar a logica.
    d = _dt.date(ano, 1, 1)
    while d.weekday() != 0:
        d += _dt.timedelta(days=1)
    d += _dt.timedelta(days=7)
    dias = []
    while len(dias) < 10:
        if d.weekday() < 5 and d.isoformat() not in calendario.feriados(ano):
            dias.append(d.isoformat())
        d += _dt.timedelta(days=1)
    ordem = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0]

    ipva = dict(calendario.IPVA)
    ipva[("SP", ano)] = dict(zip(ordem, dias))
    monkeypatch.setattr(calendario, "IPVA", ipva)

    meses = dict(calendario.LICENCIAMENTO_MES)
    meses[("SP", ano)] = dict(calendario.LICENCIAMENTO_MES[("SP", 2026)])
    monkeypatch.setattr(calendario, "LICENCIAMENTO_MES", meses)
    lic = dict(calendario.LICENCIAMENTO)
    lic[("SP", ano)] = {f: calendario._ultimo_dia_util(ano, m).isoformat()
                        for f, m in meses[("SP", ano)].items()}
    monkeypatch.setattr(calendario, "LICENCIAMENTO", lic)
    monkeypatch.setattr(calendario, "ANOS_COBERTOS", [2026, ano])
    return ano


# --- a tabela ------------------------------------------------------------

def test_tabela_tem_o_ano_corrente():
    assert tempo.hoje().year in calendario.ANOS_COBERTOS


@pytest.mark.parametrize("final", list(range(10)))
def test_todo_final_de_placa_tem_ipva(final):
    v = calendario.vencimentos("SP", final, 2026)
    tipos = {x["tipo"] for x in v}
    assert "ipva" in tipos, f"final {final} sem IPVA"
    assert "licenciamento" in tipos, f"final {final} sem licenciamento"


@pytest.mark.parametrize("final", list(range(10)))
def test_datas_sao_iso_e_do_ano_pedido(final):
    for v in calendario.vencimentos("SP", final, 2026):
        assert v["data"].startswith("2026-"), v
        _dt.date.fromisoformat(v["data"])          # estoura se malformada


@pytest.mark.parametrize("ano", [2026, 2027])
def test_dez_finais_dez_datas_no_ipva(ano):
    """DEZ datas distintas no IPVA — uma por final, e por ano.

    A versao anterior deste teste lia `vencimentos(...)[0]`, que e SEMPRE o
    IPVA (a funcao itera IPVA primeiro), com limiar `>= 5` sobre 10 finais.
    Era cego pro licenciamento — e o licenciamento tinha o final 0 repetindo
    a data do final 7 nos dois anos.

    O LICENCIAMENTO SAIU DAQUI, e nao por conveniencia: o edital de SP
    pareia os finais (1-2, 3-4, 5-6, 7-8), entao exigir dez datas distintas
    la reprovaria a tabela CERTA. O que protege o licenciamento agora e a
    comparacao com o mes do edital, no test_m25_calendario_oficial.py —
    teste de conteudo no lugar de teste de simetria.
    """
    datas = {}
    for f in range(10):
        for v in calendario.vencimentos("SP", f, ano):
            if v["tipo"] == "ipva":
                datas.setdefault(v["data"], []).append(f)
    repetidas = {d: fs for d, fs in datas.items() if len(fs) > 1}
    assert not repetidas, f"ipva/{ano}: datas repetidas {repetidas}"
    assert len(datas) == 10, f"ipva/{ano}: {len(datas)} datas pra 10 finais"


@pytest.mark.parametrize("ano", [2026, 2027])
def test_licenciamento_pareia_os_finais_como_o_edital(ano):
    """1-2, 3-4, 5-6, 7-8 dividem o mesmo prazo; 9 e 0 tem o seu."""
    datas = {f: v["data"] for f in range(10)
             for v in calendario.vencimentos("SP", f, ano)
             if v["tipo"] == "licenciamento"}
    for a, b in ((1, 2), (3, 4), (5, 6), (7, 8)):
        assert datas[a] == datas[b], f"{ano}: finais {a} e {b} divergiram"
    assert datas[9] != datas[0]
    assert len(set(datas.values())) == 6, datas


@pytest.mark.parametrize("ano", [2026, 2027])
@pytest.mark.parametrize("tipo", ["ipva", "licenciamento"])
def test_final_zero_e_o_ultimo(tipo, ano):
    """Em todo calendario por final de placa no Brasil o 0 e o ultimo. Era
    o que estava quebrado, e o que revela erro de digitacao na tabela."""
    datas = {}
    for f in range(10):
        for v in calendario.vencimentos("SP", f, ano):
            if v["tipo"] == tipo:
                datas[f] = v["data"]
    assert datas[0] == max(datas.values()), (
        f"{tipo}/{ano}: final 0 em {datas[0]}, mas o maior e "
        f"{max(datas.values())}")


@pytest.mark.parametrize("ano", [2026, 2027])
def test_nenhuma_data_da_tabela_cai_em_dia_sem_banco(ano):
    for f in range(10):
        for v in calendario.vencimentos("SP", f, ano):
            assert calendario.aviso_de_feriado(v["data"]) is None, (
                f"{v['rotulo']} {ano} cai em "
                f"{calendario.aviso_de_feriado(v['data'])}")


def test_uf_desconhecida_nao_inventa():
    assert calendario.vencimentos("XX", 1, 2026) == []


def test_ano_nao_coberto_nao_inventa():
    """Sem a tabela do ano, NAO se chuta a data do ano anterior: IPVA muda
    de calendario todo ano."""
    assert calendario.vencimentos("SP", 1, 2099) == []


@pytest.mark.parametrize("final", [-1, 10, None, "a"])
def test_final_invalido_nao_estoura(final):
    assert calendario.vencimentos("SP", final, 2026) == []


# --- feriados: fonte externa com fallback exato --------------------------

def test_feriados_sao_calculados_sem_rede():
    """Feriado nacional e CALCULAVEL (fixos + os moveis, que dependem da
    Pascoa). Nao ha fonte externa: ela nao trazia informacao, so latencia."""
    f = calendario.feriados(2026)
    assert f, "sem feriado nenhum"
    assert "2026-01-01" in f and "2026-09-07" in f and "2026-12-25" in f


def test_nao_sobrou_codigo_de_rede_no_modulo():
    """O no-op da rodada 1 voltou deslocado: tirar a rede do caminho
    sincrono deixou a API, o cache e o descarte de lixo sem consumidor.
    Codigo morto em producao e a regra 5 — e vira armadilha na proxima
    auditoria."""
    import inspect
    fonte = inspect.getsource(calendario)
    for morto in ("_buscar_feriados_online", "_CACHE_FERIADOS", "httpx"):
        assert morto not in fonte, (
            f"{morto} ficou no calendario.py sem consumidor de producao")


def test_feriados_moveis_batem_com_a_pascoa():
    f = calendario.feriados(2026)
    # Pascoa 2026: 05/04. Carnaval = -47 dias, Corpus Christi = +60.
    assert "2026-02-17" in f, "carnaval errado"
    assert "2026-06-04" in f, "corpus christi errado"


@pytest.mark.parametrize("ano", [2024, 2025, 2026, 2027, 2028])
def test_feriados_so_do_ano_pedido(ano):
    """Nenhuma data de outro ano, nenhuma malformada."""
    import re as _re
    for data in calendario.feriados(ano):
        assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}", data), data
        assert data.startswith(f"{ano}-"), data


def test_nenhum_caminho_de_producao_vai_na_rede(usuario, monkeypatch):
    """VIGIA O ALVO, não o dublê.

    A versão anterior contava `httpx.get` — mas o `conftest` já cortava
    `_buscar_feriados_online` antes, então o teste passava mesmo com o
    conserto DESFEITO. Era o mesmo defeito estrutural que ele dizia vigiar,
    um nível acima: só acusava quando duas coisas quebravam juntas.

    Agora instrumenta o `httpx` INTEIRO (get e Client) e roda os dois
    caminhos de produção que consultam feriado — placa e boleto.
    """
    import httpx
    idas = []
    for nome in ("get", "post", "request"):
        if hasattr(httpx, nome):
            monkeypatch.setattr(httpx, nome,
                                lambda *a, **kw: idas.append(a[0] if a else nome))

    responder("minha placa é ABC1D23")
    assert not idas, f"o caminho da placa foi na rede: {idas}"

    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: (
        "Boleto Ficha de Compensacao. Beneficiario: Enel. "
        "Vencimento 25/12/2026. Valor do Documento R$ 187,45"))
    wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": "REDE"}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}})
    assert not idas, f"o caminho do boleto foi na rede: {idas}"


def test_aviso_de_feriado_nao_consulta_fonte_externa(monkeypatch):
    """Trava direta do conserto: se `aviso_de_feriado` voltar a passar por
    qualquer coisa que não seja cálculo local, isto quebra."""
    chamadas = []
    real = calendario._feriados_calculados
    monkeypatch.setattr(calendario, "_feriados_calculados",
                        lambda ano: chamadas.append(ano) or real(ano))
    assert calendario.aviso_de_feriado("2026-12-25") == "Natal"
    assert chamadas == [2026], (
        f"aviso_de_feriado nao usou o calculo local: {chamadas}")


def test_ipva_concluido_do_MESMO_ano_nao_e_ressuscitado(usuario):
    """Conserto do P1-4 criou este buraco: filtrar por `pendente` tornava
    invisivel o item que a pessoa FECHOU, e o bot recriava identico.

    Em SP ha desconto por antecipacao — pagar o IPVA do ano seguinte em
    dezembro e dar baixa e o comportamento premiado, e era exatamente ele
    que ganhava um item fantasma de volta."""
    responder("minha placa é ABC1D23")
    itens = db.list_items(usuario["id"])
    assert itens
    for i in itens:
        db.update_item_status(i["id"], "concluido")

    responder("minha placa é ABC1D23")

    depois = db.list_items(usuario["id"])
    assert len(depois) == len(itens), (
        f"ressuscitou item que a pessoa ja fechou: "
        f"{[(i['descricao'], i['status']) for i in depois]}")


def test_o_filtro_de_ano_sozinho_resolve_o_ano_novo(usuario):
    """Trava da OUTRA metade: se alguem tirar a comparacao de ano, o item
    do ano passado volta a bloquear o do ano novo."""
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Veículo",
                descricao="IPVA (final 3)", data_vencimento="2026-01-14",
                status="concluido")
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Veículo",
                descricao="Licenciamento (final 3)",
                data_vencimento="2026-06-30", status="pendente")
    responder("minha placa é ABC1D23")
    anos = {i["data_vencimento"][:4] for i in db.list_items(usuario["id"])}
    assert "2027" in anos, f"o item de 2026 bloqueou o de 2027: {anos}"


# --- feriado avisado: o USO que justifica a feature ----------------------

def test_feriados_tem_consumidor_de_producao():
    """Feature construida e nao ligada e no-op — a regra 5 do projeto.
    Achado do auditor na rodada 1 do M2.2."""
    import inspect
    fonte = inspect.getsource(wa_bot)
    assert "aviso_de_feriado" in fonte, (
        "calendario.feriados() nao e usado por nenhum codigo de producao")


@pytest.mark.parametrize("data,esperado", [
    ("2026-12-25", "Natal"),
    ("2026-09-07", "Independência"),
    ("2026-02-17", "Carnaval"),
])
def test_avisa_feriado(data, esperado):
    assert calendario.aviso_de_feriado(data) == esperado


@pytest.mark.parametrize("data,esperado", [
    ("2026-08-22", "sábado"),
    ("2026-08-23", "domingo"),
])
def test_avisa_fim_de_semana(data, esperado):
    assert calendario.aviso_de_feriado(data) == esperado


def test_dia_util_nao_gera_aviso():
    assert calendario.aviso_de_feriado("2026-08-20") is None


@pytest.mark.parametrize("ruim", [None, "", "20/08/2026", "nao-e-data",
                                  "2026-13-45"])
def test_data_ruim_nao_estoura(ruim):
    assert calendario.aviso_de_feriado(ruim) is None


def test_conta_que_vence_em_feriado_avisa(usuario, monkeypatch):
    """Conta que vence em feriado nao pode ser paga no dia — quem descobre
    na hora paga multa."""
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: (
        "Boleto Ficha de Compensacao. Beneficiario: Enel. "
        "Vencimento 25/12/2026. Valor do Documento R$ 187,45"))
    reply = (wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": "FER"}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}}) or {}).get("text", "")
    assert "Natal" in reply and "banco fechado" in reply.lower(), reply


# --- placa -> lembretes ---------------------------------------------------

def test_extrai_final_da_placa():
    # Mercosul (letra na 5a posicao) e inequivoco e vale sozinho.
    assert calendario.final_da_placa("ABC1D23") == 3
    assert calendario.final_da_placa("XYZ 9A87") == 7
    # O formato ANTIGO e indistinguivel de "3 letras + 4 digitos", entao so
    # vale com a palavra "placa" — senao "o ipva do ano 2026" virava placa.
    assert calendario.final_da_placa("abc-1234") is None
    assert calendario.final_da_placa("placa abc-1234") == 4


@pytest.mark.parametrize("ruim", ["", None, "sem numero", "ABC"])
def test_placa_invalida_nao_vira_final(ruim):
    assert calendario.final_da_placa(ruim) is None


@pytest.mark.parametrize("frase", [
    "o ipva do ano 2026 ja saiu?",
    "o ipva deu 1234 reais",
    "quanto foi o licenciamento? deu 1234",
    "o ipva no final 3 parcelas",
    "nota fiscal 1234",
    "CNPJ 12.345.678/0001-90",
    "CEP 01310-100",
    "paguei o ipva ontem",
])
def test_frase_comum_nao_vira_placa(frase):
    """O P0 da auditoria: "ano 2026" virava final 6, "deu 1234" virava
    final 4 — e o bot respondia "Pronto 🚗 Guardei" com dois lembretes na
    data de outra pessoa, a partir de uma PERGUNTA."""
    assert calendario.final_da_placa(frase) is None, frase


@pytest.mark.parametrize("frase", [
    # trava da guarda "placa" no fallback `final N`
    "paguei o licenciamento no final 5 do mes",
    "o ipva no final 3",
    # trava do LOOKAHEAD: aqui a palavra "placa" ESTA na frase, entao a
    # guarda de contexto ja passou e o lookahead e a unica defesa. Sem
    # esses tres casos ele podia ser removido sem quebrar nada.
    "minha placa, o ipva no final 3 parcelas",
    "troquei a placa; paguei o ipva no final 3 vezes",
    "placa nova, licenciamento no final 2 x de 500",
    "ipva final 2 x de 500",
    "licenciamento final 3 parcelas",
    "ipva no final 2 vezes",
])
def test_final_sem_contexto_de_placa_nao_conta(frase):
    """As duas guardas que o teste de mutacao mostrou desprotegidas: sem
    elas, "no final 5 do mes" e "final 2 x de 500" viram placa."""
    assert calendario.final_da_placa(frase) is None, frase


@pytest.mark.parametrize("frase,esperado", [
    ("minha placa é ABC1D23", 3),
    ("placa ABC-1234", 4),
    ("placa ABC.1234", 4),
    ("a placa é XYZ 9A87", 7),
    ("placa final 7", 7),
    ("meu carro ABC1D23 vence quando?", 3),
])
def test_placa_de_verdade_continua_sendo_lida(frase, esperado):
    assert calendario.final_da_placa(frase) == esperado, frase


def test_pergunta_sobre_ipva_nao_cria_lembrete(usuario):
    """Fluxo completo do P0: a pessoa PERGUNTA e o bot nao pode responder
    'Pronto, guardei'."""
    reply = responder("o ipva do ano 2026 ja saiu?")
    carro = [i for i in db.list_items(usuario["id"])
             if "final" in (i["descricao"] or "").lower()]
    assert not carro, f"criou lembrete de placa a partir de pergunta: {carro}"
    assert "Guardei pelo final" not in reply, reply


# A suite de fluxo TEM que variar o final da placa. Com so um final (o 3,
# cujas datas de 2026 ja passaram), a metade "ano corrente" da consulta
# nunca era exercitada — e foi assim que a duplicata de licenciamento dos
# finais 9 e 0 passou por tres rodadas de auditoria sem aparecer.
PLACAS = {"final 3": "ABC1D23", "final 9": "ABC1D29", "final 0": "ABC1D20"}


@pytest.mark.parametrize("placa", sorted(PLACAS.values()))
def test_placa_no_whatsapp_cria_os_lembretes(usuario, placa):
    reply = responder(f"minha placa é {placa}")
    itens = db.list_items(usuario["id"])
    descricoes = " | ".join(i["descricao"].lower() for i in itens)
    assert "ipva" in descricoes, f"nao criou IPVA: {descricoes} / {reply!r}"
    assert "licenciamento" in descricoes, descricoes
    for i in itens:
        assert i["data_vencimento"], f"lembrete sem data: {i}"


@pytest.mark.parametrize("placa", sorted(PLACAS.values()))
def test_nunca_dois_itens_com_a_mesma_descricao(usuario, placa):
    """Nos finais 9 e 0 o licenciamento dos DOIS anos ainda esta no futuro,
    e nasciam dois itens com o mesmo nome."""
    responder(f"minha placa é {placa}")
    descricoes = [i["descricao"] for i in db.list_items(usuario["id"])]
    assert len(descricoes) == len(set(descricoes)), (
        f"itens indistinguiveis na lista: {descricoes}")


@pytest.mark.parametrize("placa", sorted(PLACAS.values()))
def test_a_baixa_funciona_depois_de_cadastrar_a_placa(usuario, placa):
    """O comando central do produto — e o que os templates aprovados pela
    Meta mandam responder. Com duas duplicatas, "feito Licenciamento" nao
    fechava nenhuma e ainda criava um item chamado "feito Licenciamento"."""
    responder(f"minha placa é {placa}")
    antes = len(db.list_items(usuario["id"]))
    alvo = [i for i in db.list_items(usuario["id"])
            if "licenciamento" in i["descricao"].lower()][0]
    db.log_dispatch(usuario["id"], "hora", alvo["id"])

    responder("feito Licenciamento")

    depois = db.list_items(usuario["id"])
    assert len(depois) == antes, f"a baixa criou lixo: {[i['descricao'] for i in depois]}"
    fechado = [i for i in depois if i["id"] == alvo["id"]][0]
    assert fechado["status"] == "concluido", (
        f"'feito Licenciamento' nao fechou nada: {fechado}")


@pytest.mark.parametrize("placa", sorted(PLACAS.values()))
def test_um_lembrete_por_tipo(usuario, placa):
    """Lembrete com 16 meses de antecedencia nao e servico. Quando chegar a
    hora, o do ano seguinte e criado."""
    responder(f"minha placa é {placa}")
    tipos = [i["descricao"].split(" (")[0] for i in db.list_items(usuario["id"])]
    assert len(tipos) == len(set(tipos)), tipos


# O final 5 entra aqui de proposito: hoje (17/08/2026) o licenciamento dele
# vence em 31/08/2026 — duas semanas. E o caso em que trocar "a proxima
# data" por "a ultima" custa caro e nao aparece na forma da resposta.
FINAIS_COM_DATA = {3: "ABC1D23", 9: "ABC1D29", 0: "ABC1D20", 5: "ABC1D25",
                   1: "ABC1D21"}


@pytest.mark.parametrize("placa,esperado", [
    # VALORES LITERAIS, não a fórmula recalculada. Os testes abaixo derivam
    # o esperado com o mesmo algoritmo da produção — isso mata mutação de UM
    # lado (provado), mas um erro de conceito mudado nos dois lados passaria.
    # Estas datas são as que doem se mudarem, escritas à mão.
    #
    # Tudo de 2026, que é o único ano CONFERIDO contra o edital, e com a data
    # de hoje travada em janeiro — é o único mês em que o IPVA e o
    # licenciamento do mesmo ano estão os dois no futuro. Em agosto este
    # teste mediria só metade e não diria isso em lugar nenhum.
    ("ABC1D25", {"ipva": "2026-01-16", "licenciamento": "2026-09-30"}),
    ("ABC1D29", {"ipva": "2026-01-22", "licenciamento": "2026-11-30"}),
    ("ABC1D20", {"ipva": "2026-01-23", "licenciamento": "2026-12-31"}),
])
def test_datas_literais_do_calendario(usuario, monkeypatch, placa, esperado):
    monkeypatch.setattr(tempo, "hoje", lambda: _dt.date(2026, 1, 5))
    responder(f"minha placa é {placa}")
    criado = {}
    for i in db.list_items(usuario["id"]):
        tipo = "ipva" if "ipva" in i["descricao"].lower() else "licenciamento"
        criado[tipo] = i["data_vencimento"]
    assert criado == esperado, f"{placa}: {criado}"


@pytest.mark.parametrize("final,placa", sorted(FINAIS_COM_DATA.items()))
def test_a_data_gravada_e_a_proxima_ocorrencia(usuario, final, placa):
    """VERIFICA O VALOR, não a forma.

    Os testes de cardinalidade (um item por tipo, sem descricao repetida,
    baixa funcionando) ficavam VERDES com duas mutacoes distintas: agrupar
    pela MAIOR data em vez da proxima, e consultar o ano ANTERIOR. As duas
    trocam o vencimento de duas semanas pelo de 16 meses — a pessoa nao e
    avisada e a lista continua parecendo certa.

    Foi exatamente assim que a duplicata dos finais 9 e 0 atravessou tres
    auditorias: teste que confere a forma da resposta, nunca o valor.
    """
    hoje = tempo.hoje().isoformat()
    todas = (calendario.vencimentos("SP", final, tempo.hoje().year)
             + calendario.vencimentos("SP", final, tempo.hoje().year + 1))
    esperado = {}
    for v in sorted([x for x in todas if x["data"] >= hoje],
                    key=lambda x: x["data"]):
        esperado.setdefault(v["tipo"], v["data"])

    responder(f"minha placa é {placa}")

    criado = {}
    for i in db.list_items(usuario["id"]):
        tipo = "ipva" if "ipva" in i["descricao"].lower() else "licenciamento"
        criado[tipo] = i["data_vencimento"]
    assert criado == esperado, (
        f"final {final}: gravou {criado}, a proxima ocorrencia e {esperado}")


@pytest.mark.parametrize("dia,final,placa", [
    # 05/01: unico cenario em que o IPVA do ANO CORRENTE e o que vale —
    # o eixo que a suite inteira nao exercitava (tudo rodava em agosto).
    (_dt.date(2026, 1, 5), 3, "ABC1D23"),
    (_dt.date(2026, 1, 5), 9, "ABC1D29"),
    # 20/01: o IPVA do final 3 (14/01) ja passou, o do ano seguinte vale.
    (_dt.date(2026, 1, 20), 3, "ABC1D23"),
    # NO DIA EXATO do vencimento: `>=` vs `>`. Quem manda a placa no dia
    # perde o item do dia e ganha o de 16 meses — janela de um dia por
    # final por ano, mesmo modo de falha silencioso.
    (_dt.date(2026, 8, 31), 5, "ABC1D25"),
    (_dt.date(2026, 12, 22), 9, "ABC1D29"),
    (_dt.date(2027, 1, 14), 3, "ABC1D23"),
])
def test_data_certa_em_janeiro(usuario, monkeypatch, dia, final, placa):
    """O EIXO DE DATA. Em agosto nenhum final tem IPVA do ano corrente no
    futuro, entao metade da consulta nunca era exercitada."""
    monkeypatch.setattr(tempo, "hoje", lambda: dia)
    hoje = dia.isoformat()
    todas = (calendario.vencimentos("SP", final, dia.year)
             + calendario.vencimentos("SP", final, dia.year + 1))
    esperado = {}
    for v in sorted([x for x in todas if x["data"] >= hoje],
                    key=lambda x: x["data"]):
        esperado.setdefault(v["tipo"], v["data"])

    responder(f"minha placa é {placa}")

    criado = {}
    for i in db.list_items(usuario["id"]):
        tipo = "ipva" if "ipva" in i["descricao"].lower() else "licenciamento"
        criado[tipo] = i["data_vencimento"]
    assert criado == esperado, (
        f"em {dia}, final {final}: gravou {criado}, esperado {esperado}")


def test_placa_repetida_nao_duplica(usuario):
    responder("minha placa é ABC1D23")
    antes = len(db.list_items(usuario["id"]))
    responder("minha placa é ABC1D23")
    assert len(db.list_items(usuario["id"])) == antes


def test_nao_cria_lembrete_de_data_que_ja_passou(usuario):
    """Metade da regra: nada de nascer com data errada. A outra metade e
    nao nascer no passado — lembrete vencido dispara cobranca na hora."""
    responder("minha placa é ABC1D23")
    hoje = tempo.hoje().isoformat()
    for i in db.list_items(usuario["id"]):
        assert i["data_vencimento"] >= hoje, (
            f"lembrete nasceu vencido: {i['descricao']} em "
            f"{i['data_vencimento']}")


def test_ipva_concluido_do_ano_passado_nao_bloqueia_o_do_ano_novo(usuario):
    """Quem USA o produto direito — deu baixa quando pagou — era justamente
    quem perdia o lembrete do ano seguinte. E a resposta dizia 'Guardei'."""
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Veículo",
                descricao="IPVA (final 3)", data_vencimento="2026-01-14",
                status="concluido")
    responder("minha placa é ABC1D23")
    ipvas = [i for i in db.list_items(usuario["id"])
             if "ipva" in i["descricao"].lower() and i["status"] == "pendente"]
    assert ipvas, "o IPVA concluido do ano passado bloqueou o do ano novo"


def test_categoria_do_lembrete_de_carro_existe(usuario):
    """"Carro" nao esta em db.VALID_CATEGORIES: virava "Outros" em silencio
    e o dash de gastos por categoria perdia o veiculo."""
    responder("minha placa é ABC1D23")
    itens = [i for i in db.list_items(usuario["id"]) if "final" in i["descricao"]]
    assert itens and all(i["categoria"] == "Veículo" for i in itens), (
        [(i["descricao"], i["categoria"]) for i in itens])


def test_resposta_mostra_o_ano(usuario):
    """Uma mensagem so lista datas de anos diferentes. Sem o ano, '19/01'
    lido em agosto parece data que ja passou."""
    reply = responder("minha placa é ABC1D23")
    import re as _re
    assert _re.search(r"\d{2}/\d{2}/\d{4}", reply), reply


def test_calendario_esgotado_avisa_em_vez_de_dizer_que_ja_tem(usuario,
                                                              monkeypatch):
    """De julho a dezembro do ultimo ano da tabela, o bot afirmava ter itens
    que nao tinha — justo quando a manutencao anual esta atrasada."""
    monkeypatch.setattr(calendario, "vencimentos",
                        lambda uf, f, ano: [{"tipo": "ipva",
                                             "data": "2020-01-10",
                                             "rotulo": "IPVA (final 3)"}])
    reply = responder("minha placa é ABC1D23")
    assert "já tenho na sua lista" not in reply, (
        f"disse que tem itens que nao tem: {reply!r}")
    assert not db.list_items(usuario["id"])


def test_placa_sem_tabela_do_ano_avisa_em_vez_de_inventar(usuario,
                                                          monkeypatch):
    monkeypatch.setattr(calendario, "vencimentos", lambda *a, **kw: [])
    reply = responder("minha placa é ABC1D23")
    assert not db.list_items(usuario["id"]), "inventou lembrete sem tabela"
    assert reply.strip(), "ficou mudo"


# --- o invariante que o Kevin escreveu ------------------------------------

def test_fonte_fora_do_ar_nao_apaga_lembrete_existente(usuario, monkeypatch):
    """"API fora do ar não pode fazer o lembrete sumir." """
    responder("minha placa é ABC1D23")
    antes = {(i["descricao"], i["data_vencimento"])
             for i in db.list_items(usuario["id"])}
    assert antes

    def _explode(*a, **kw):
        raise RuntimeError("fonte caiu")
    # A fonte que sobrou é a TABELA. Se ela explodir, o invariante do Kevin
    # continua valendo: o lembrete que já existe não some nem muda.
    monkeypatch.setattr(calendario, "vencimentos", _explode)
    monkeypatch.setattr(calendario, "_feriados_calculados", _explode)

    responder("minha placa é ABC1D23")     # mesma mensagem, fonte quebrada
    depois = {(i["descricao"], i["data_vencimento"])
              for i in db.list_items(usuario["id"])}
    assert antes <= depois, (
        f"a fonte fora do ar mexeu nos lembretes que ja existiam: "
        f"{antes - depois}")


def test_nunca_sobrescreve_data_que_o_usuario_definiu(usuario):
    """O usuario mandou 'IPVA dia 20/03'. A tabela diz outra coisa. Quem
    manda e ele — o bot nao corrige o dono."""
    meu = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Carro", descricao="IPVA",
                      data_vencimento="2026-03-20", status="pendente")
    responder("minha placa é ABC1D23")
    with db.get_conn() as conn:
        r = conn.execute("SELECT data_vencimento FROM items WHERE id=?",
                         (meu,)).fetchone()
    assert r["data_vencimento"] == "2026-03-20", (
        "a tabela sobrescreveu a data que a pessoa definiu")
