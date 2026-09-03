# -*- coding: utf-8 -*-
"""Comprovante que casa com DUAS contas: perguntar, nunca duplicar.

O defeito: `_conta_pendente_equivalente` devolvia None quando mais de um
pendente batia valor E vencimento. Quem chama entende None como "nao e baixa
de nada", cai no fluxo de conta nova e GRAVA UM ITEM. Sobravam tres itens
onde havia duas contas — o gasto do mes contado a mais e o lembrete da conta
JA PAGA disparando no vencimento.

E era invisivel: `_pendente_de_mesmo_valor` tambem devolve None com mais de
um, entao nem a dica de correcao aparecia. A pessoa so descobria quando o bot
cobrasse uma conta que ela ja tinha pago.

Duas contas com mesmo valor e mesmo vencimento nao sao caso raro de
laboratorio: mensalidade, condominio, seguro e parcela de carne caem assim.
"""
import datetime as _dt

import db
import tempo
import wa_bot
from conftest import TELEFONE


def _iso(dias=35):
    return (tempo.hoje() + _dt.timedelta(days=dias)).strftime("%Y-%m-%d")


def _br(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def _ontem():
    return (tempo.hoje() - _dt.timedelta(days=1)).strftime("%d/%m/%Y")


def _comprovante(nome, valor, venc_iso):
    return (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em {_ontem()}. "
            f"Beneficiario: {nome}. Vencimento do titulo: {_br(venc_iso)}. "
            f"Valor Pago R$ {valor}")


def _foto(monkeypatch, texto, msg_id):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    return (wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": msg_id}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}}) or {}).get("text", "")


def _texto(monkeypatch, msg, msg_id):
    return (wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": msg_id}, "pushName": "Kevin",
        "message": {"conversation": msg}}}) or {}).get("text", "")


def _duas_contas(user_id, valor=150.0, venc=None):
    venc = venc or _iso(35)
    a = db.add_item(user_id=user_id, tipo="despesa", categoria="casa",
                    descricao="conta de luz", valor_reais=valor,
                    data_vencimento=venc, status="pendente")
    b = db.add_item(user_id=user_id, tipo="despesa", categoria="casa",
                    descricao="conta de agua", valor_reais=valor,
                    data_vencimento=venc, status="pendente")
    return a, b, venc


def _pendentes(user_id):
    return [i for i in db.list_items(user_id, status="pendente")]


def _todos(user_id):
    return list(db.list_items(user_id))


# --- O DEFEITO: o terceiro item ---------------------------------------

def test_comprovante_ambiguo_nao_cria_item_novo(usuario, monkeypatch):
    """Duas contas iguais + comprovante nao pode virar tres itens."""
    _duas_contas(usuario["id"])
    venc = _iso(35)
    antes = len(_todos(usuario["id"]))

    _foto(monkeypatch, _comprovante("ENEL", "150,00", venc), "amb1")

    assert len(_todos(usuario["id"])) == antes, (
        "o comprovante ambiguo gravou um item novo — e o defeito")


def test_comprovante_ambiguo_pergunta_em_vez_de_calar(usuario, monkeypatch):
    _duas_contas(usuario["id"])
    r = _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "amb2")
    assert "Qual delas" in r
    assert "conta de luz" in r and "conta de agua" in r
    assert "Nenhuma dessas" in r
    assert "*1*" in r and "*2*" in r and "*3*" in r


def test_nenhuma_das_duas_e_dada_como_paga_sem_a_pessoa_escolher(
        usuario, monkeypatch):
    """O bot nao pode decidir no escuro qual das duas ele fecha.

    PLACEBO CORRIGIDO (auditoria): so `len(_pendentes) == 2` passava mesmo
    com o defeito de volta, porque o defeito criava um TERCEIRO item sem
    tocar nas duas pendentes. O total tem que entrar na conta.
    """
    _duas_contas(usuario["id"])
    _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "amb3")
    assert len(_pendentes(usuario["id"])) == 2
    assert len(_todos(usuario["id"])) == 2, (
        "nao pode existir um terceiro item enquanto a pessoa nao escolher")


# --- A resposta da pessoa ---------------------------------------------

def test_escolher_uma_da_baixa_so_nela(usuario, monkeypatch):
    a, b, venc = _duas_contas(usuario["id"])
    _foto(monkeypatch, _comprovante("ENEL", "150,00", venc), "amb4")

    r = _texto(monkeypatch, "1", "amb4b")
    assert "baixa" in r.lower()

    restantes = _pendentes(usuario["id"])
    assert len(restantes) == 1, "so uma das duas podia sair da lista"
    assert (restantes[0].get("descricao") or "") == "conta de agua"


def test_nenhuma_dessas_guarda_o_comprovante_e_poupa_as_duas(
        usuario, monkeypatch):
    """A saida existe pra o comprovante nao se perder.

    A chave valor+vencimento SELECIONA mas nao IDENTIFICA: o pagamento pode
    ser de uma conta que nem esta na lista.
    """
    _duas_contas(usuario["id"])
    antes = len(_todos(usuario["id"]))
    _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "amb5")

    r = _texto(monkeypatch, "3", "amb5b")

    assert "conta nova" in r.lower()
    assert len(_todos(usuario["id"])) == antes + 1, "o comprovante sumiu"
    assert len(_pendentes(usuario["id"])) == 2, (
        "escolher 'nenhuma' nao pode mexer nas contas que continuam abertas")
    novo = [i for i in _todos(usuario["id"])
            if i.get("status") == "concluido"]
    assert len(novo) == 1


def test_numero_fora_da_lista_nao_faz_nada(usuario, monkeypatch):
    """PLACEBO CORRIGIDO: `antes` era medido DEPOIS da foto e absorvia o
    item espurio do defeito. O total agora e ancorado antes de tudo.
    """
    _duas_contas(usuario["id"])
    antes = len(_todos(usuario["id"]))
    _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "amb6")
    _texto(monkeypatch, "7", "amb6b")
    assert len(_pendentes(usuario["id"])) == 2
    assert len(_todos(usuario["id"])) == antes


# --- Os tres P1 da auditoria ------------------------------------------

def test_pergunta_sem_resposta_nao_come_o_comprovante(usuario, monkeypatch):
    """P1-1: o bot disse "Recebi o comprovante". Sumir depois disso e pior
    do que o defeito antigo — la o item era errado, mas existia.
    """
    _duas_contas(usuario["id"])
    _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "p1a")

    wa_bot.BAIXA_ESCOLHA[TELEFONE]["quando"] = tempo.agora() - _dt.timedelta(
        seconds=wa_bot.BAIXA_ESCOLHA_TTL_S + 60)
    _texto(monkeypatch, "oi", "p1a2")

    pagos = [i for i in _todos(usuario["id"]) if i.get("status") == "concluido"]
    assert len(pagos) == 1, "o comprovante evaporou sem uma linha de aviso"
    assert len(_pendentes(usuario["id"])) == 2, (
        "resgatar nao pode fechar conta que a pessoa nunca escolheu")


def test_resgate_nao_vale_pra_pergunta_sem_documento(usuario, monkeypatch):
    """A pergunta de baixa por texto nao tem comprovante pra resgatar."""
    a, b, _venc = _duas_contas(usuario["id"])
    antes = len(_todos(usuario["id"]))
    wa_bot.BAIXA_ESCOLHA[TELEFONE] = {
        "ids": [a, b],
        "quando": tempo.agora() - _dt.timedelta(
            seconds=wa_bot.BAIXA_ESCOLHA_TTL_S + 60)}
    _texto(monkeypatch, "oi", "p1a3")
    assert len(_todos(usuario["id"])) == antes


def test_reenviar_a_foto_depois_de_escolher_nao_dobra_o_gasto(
        usuario, monkeypatch):
    """P1-2: o caminho ambiguo retorna antes do dedup la de cima."""
    _duas_contas(usuario["id"])
    texto = _comprovante("ENEL", "150,00", _iso(35))
    _foto(monkeypatch, texto, "p1b")
    _texto(monkeypatch, "3", "p1b2")
    depois_da_primeira = len(_todos(usuario["id"]))

    _foto(monkeypatch, texto, "p1b3")

    assert len(_todos(usuario["id"])) == depois_da_primeira, (
        "a mesma foto gravou o comprovante duas vezes")


def test_pedir_os_kits_mata_a_pergunta_de_baixa(usuario, monkeypatch):
    """P1-3: decisao NOVA mata pergunta VELHA.

    Os kits tambem respondem por digito solto, e o `_escolha_de_baixa` roda
    antes deles. Sem o pop, o "1" do menu dos kits fechava a conta de luz.
    """
    _duas_contas(usuario["id"])
    _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "p1c")
    assert TELEFONE in wa_bot.BAIXA_ESCOLHA

    _texto(monkeypatch, "kits", "p1c2")
    assert TELEFONE not in wa_bot.BAIXA_ESCOLHA

    _texto(monkeypatch, "1", "p1c3")
    assert len(_pendentes(usuario["id"])) == 2, (
        "o numero do menu dos kits fechou uma conta")


def test_uma_opcao_so_nao_vira_menu(usuario, monkeypatch):
    """P2-1: se a lista encolher entre as duas leituras, nada de menu de um.

    Menu de uma opcao pularia o veto por nome e ainda escreveria
    "Tenho 1 contas com esse mesmo valor".
    """
    venc = _iso(35)
    _duas_contas(usuario["id"], venc=venc)
    real = wa_bot._pendentes_equivalentes
    chamadas = {"n": 0}

    def encolhendo(user_id, dados):
        chamadas["n"] += 1
        achados = real(user_id, dados)
        return achados if chamadas["n"] == 1 else achados[:1]

    monkeypatch.setattr(wa_bot, "_pendentes_equivalentes", encolhendo)
    r = _foto(monkeypatch, _comprovante("ENEL", "150,00", venc), "p2a")
    assert "1 contas" not in r
    assert "Qual delas" not in r


def test_conta_sem_descricao_fica_fora_do_menu(usuario, monkeypatch):
    """P2-2: o menu e numerado e imprimiria "*1* — None"."""
    venc = _iso(35)
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="casa",
                descricao="", valor_reais=150.0, data_vencimento=venc,
                status="pendente")
    achados = wa_bot._pendentes_equivalentes(
        usuario["id"], {"valor_reais": 150.0, "vencimento_titulo": venc})
    assert achados == []


def test_a_escolha_confere_o_dono_no_banco(usuario, monkeypatch):
    """Id que nao e da pessoa nao fecha nada, mesmo listado no slot."""
    _duas_contas(usuario["id"])
    outro_id = db.create_user(nome="Vizinha", telefone="5511999999999")
    alheio = db.add_item(user_id=outro_id, tipo="despesa",
                         categoria="casa", descricao="conta do vizinho",
                         valor_reais=150.0, data_vencimento=_iso(35),
                         status="pendente")
    wa_bot.BAIXA_ESCOLHA[TELEFONE] = {"ids": [alheio],
                                      "quando": tempo.agora()}
    _texto(monkeypatch, "1", "dono1")
    ainda = [i for i in db.list_items(outro_id, status="pendente")]
    assert len(ainda) == 1, "fechou item de outra pessoa"


def test_a_pergunta_morre_com_o_tempo(usuario, monkeypatch):
    """Um "1" solto dias depois nao pode concluir conta nenhuma."""
    _duas_contas(usuario["id"])
    _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "amb7")

    estado = wa_bot.BAIXA_ESCOLHA.get(TELEFONE)
    assert estado, "a pergunta tem que ficar armada"
    estado["quando"] = tempo.agora() - _dt.timedelta(
        seconds=wa_bot.BAIXA_ESCOLHA_TTL_S + 60)

    _texto(monkeypatch, "1", "amb7b")
    assert len(_pendentes(usuario["id"])) == 2


# --- O que NAO pode ter mudado ----------------------------------------

def test_uma_conta_so_continua_dando_baixa_sozinha(usuario, monkeypatch):
    """A ambiguidade e a excecao. O caminho normal nao vira pergunta."""
    venc = _iso(35)
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="casa",
                descricao="conta de luz ENEL", valor_reais=150.0,
                data_vencimento=venc, status="pendente")
    r = _foto(monkeypatch, _comprovante("ENEL", "150,00", venc), "un1")
    assert "Baixa dada" in r
    assert _pendentes(usuario["id"]) == []


def test_sem_nenhum_pendente_igual_continua_guardando_a_conta(
        usuario, monkeypatch):
    r = _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "un2")
    assert "Qual delas" not in r
    assert len(_todos(usuario["id"])) == 1


def test_a_pergunta_nao_escreve_no_pending(usuario, monkeypatch):
    """Slot proprio, nunca o PENDING — e o P0-2 do M5.4.

    Escrever no PENDING atropelaria uma confirmacao de boleto em curso.
    """
    _duas_contas(usuario["id"])
    wa_bot.PENDING.pop(TELEFONE, None)
    _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "amb8")
    assert TELEFONE not in wa_bot.PENDING
    assert TELEFONE in wa_bot.BAIXA_ESCOLHA


def test_a_pergunta_conta_como_decisao_viva(usuario, monkeypatch):
    """Nada de podcast pode atropelar a pergunta em curso."""
    _duas_contas(usuario["id"])
    _foto(monkeypatch, _comprovante("ENEL", "150,00", _iso(35)), "amb9")
    assert wa_bot._decisao_de_conversa_viva(TELEFONE) is True


# --- A consulta unica -------------------------------------------------

def test_a_busca_tem_teto(usuario):
    """A pergunta e numerada de 1 a 9 e ainda precisa da linha do 'nenhuma'."""
    venc = _iso(35)
    for n in range(9):
        db.add_item(user_id=usuario["id"], tipo="despesa", categoria="casa",
                    descricao=f"parcela {n}", valor_reais=150.0,
                    data_vencimento=venc, status="pendente")
    achados = wa_bot._pendentes_equivalentes(
        usuario["id"], {"valor_reais": 150.0, "vencimento_titulo": venc})
    assert len(achados) <= 5


def test_sem_vencimento_do_titulo_nao_procura_nada(usuario):
    """Sem os dois dados impressos no papel, nao ha chave — nao ha baixa."""
    _duas_contas(usuario["id"])
    assert wa_bot._pendentes_equivalentes(
        usuario["id"], {"valor_reais": 150.0}) == []
    assert wa_bot._pendentes_equivalentes(
        usuario["id"], {"vencimento_titulo": _iso(35)}) == []


def test_ambiguo_e_a_sentinela_nao_um_item(usuario):
    """`if _pendente:` num dicionario vazio seria falso e voltaria o defeito."""
    venc = _iso(35)
    _duas_contas(usuario["id"], venc=venc)
    r = wa_bot._conta_pendente_equivalente(
        usuario["id"], {"valor_reais": 150.0, "vencimento_titulo": venc})
    assert r is wa_bot.AMBIGUO
