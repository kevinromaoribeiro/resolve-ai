"""Rodada 9 do M2.1 — o veto e o conectivo, e o trade-off que sobra.

`de` tem 2 letras, passava no filtro e virava token válido dos dois lados.
Razão social brasileira é cheia de conectivo ("Companhia DE Saneamento",
"Banco DO Brasil"), então o token que não distingue nada é justamente o que
mais aparece — e um comprovante de "PGTO DE ENERGIA" quitava a conta de
saneamento tendo `de` como único ponto em comum.

O segundo achado da rodada (sigla no comprovante × razão social no boleto)
NÃO foi consertado, e o teste no fim deste arquivo trava a decisão junto com
o motivo. Ver DECISOES.md.
"""
import datetime as _dt

import pytest

import db
import tempo
import wa_bot
from conftest import TELEFONE


def _futuro(dias=35):
    return (tempo.hoje() + _dt.timedelta(days=dias)).strftime("%d/%m/%Y")


def _ontem():
    return (tempo.hoje() - _dt.timedelta(days=1)).strftime("%d/%m/%Y")


def _foto(monkeypatch, texto, msg_id):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    return (wa_bot.handle_incoming({"data": {"key": {
        "remoteJid": f"{TELEFONE}@s.whatsapp.net", "fromMe": False,
        "id": msg_id}, "pushName": "Kevin",
        "message": {"imageMessage": {"caption": ""}}}}) or {}).get("text", "")


def _conta(nome, valor, venc):
    return (f"Boleto Ficha de Compensacao. Beneficiario: {nome}. "
            f"Vencimento {venc}. Valor do Documento R$ {valor}")


def _comprovante(nome, valor, venc):
    return (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em {_ontem()}. "
            f"Beneficiario: {nome}. Vencimento do titulo: {venc}. "
            f"Valor Pago R$ {valor}")


# --- P0-21: conectivo nao pode autorizar baixa --------------------------

@pytest.mark.parametrize("token", ["de", "da", "do", "ltda", "sa", "cia"])
def test_conectivo_e_sufixo_nao_sao_nome(token):
    assert wa_bot._tokens_de_nome(token) == set(), token


def test_comprovante_de_energia_nao_quita_saneamento(usuario, monkeypatch):
    venc = _futuro(35)
    _foto(monkeypatch, _conta("Companhia de Saneamento Basico", "150,00",
                              venc), "K1")
    _foto(monkeypatch, _comprovante("PGTO DE ENERGIA ELETRICA", "150,00",
                                    venc), "K2")
    pendentes = db.list_items(usuario["id"], status="pendente")
    assert any("Saneamento" in i["descricao"] for i in pendentes), (
        f"o conectivo 'de' autorizou a baixa errada: "
        f"{db.list_items(usuario['id'])}")


@pytest.mark.parametrize("par", [
    ("Banco do Brasil", "PGTO DA ENERGIA"),
    ("Cia de Gas", "COMPROVANTE DE INTERNET"),
    ("Secretaria da Fazenda", "PAGAMENTO DE CONDOMINIO"),
])
def test_razao_social_com_conectivo_nao_casa_com_outro(usuario, monkeypatch,
                                                       par):
    credor, outro = par
    venc = _futuro(35)
    _foto(monkeypatch, _conta(credor, "150,00", venc), f"L{abs(hash(par))}")
    _foto(monkeypatch, _comprovante(outro, "150,00", venc),
          f"M{abs(hash(par))}")
    pendentes = db.list_items(usuario["id"], status="pendente")
    assert pendentes, f"'{outro}' quitou '{credor}' por conectivo"


# --- o caminho feliz nao pode ser barrado -------------------------------

@pytest.mark.parametrize("credor,no_comprovante", [
    ("Companhia de Saneamento Basico", "CIA SANEAMENTO"),
    ("Condominio Edificio Sao Jose", "CONDOMINIO EDIFICIO SAO JOSE"),
    ("Enel Distribuicao Sao Paulo", "ENEL DISTRIBUICAO"),
])
def test_mesmo_credor_com_nome_parcial_fecha(usuario, monkeypatch, credor,
                                             no_comprovante):
    venc = _futuro(35)
    _foto(monkeypatch, _conta(credor, "150,00", venc), f"N{abs(hash(credor))}")
    _foto(monkeypatch, _comprovante(no_comprovante, "150,00", venc),
          f"O{abs(hash(credor))}")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 1 and itens[0]["status"] == "concluido", (
        f"o veto barrou baixa legitima: {itens}")


# --- o trade-off declarado ----------------------------------------------

def test_sigla_no_comprovante_nao_fecha_e_isso_e_decisao(usuario,
                                                         monkeypatch):
    """DECISÃO REGISTRADA, não bug esquecido.

    Boleto imprime razão social ("Companhia de Saneamento Basico do
    Estado"), extrato do banco imprime a marca ("SABESP"). Sem token em
    comum, o veto barra — e a conta paga fica pendente.

    A regra que resolveria isto ("só vetar quando os DOIS lados têm 2+
    tokens distintivos") reabre o P0-19: `ENEL DISTRIBUICAO` × `conta
    SABESP` tem exatamente a mesma forma — um lado com 1 token, zero
    interseção — e voltaria a fechar a conta errada. Medido.

    Entre os dois erros, escolho este: conta paga que continua pendente
    gera cobrança a mais (visível, e a pessoa corrige com "paguei X", que a
    mensagem ensina). Conta NÃO paga que some da lista é perda silenciosa —
    a classe do incidente de 14/08.
    """
    venc = _futuro(35)
    _foto(monkeypatch, _conta("Companhia de Saneamento Basico do Estado",
                              "92,10", venc), "P1")
    _foto(monkeypatch, _comprovante("SABESP", "92,10", venc), "P2")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 2, itens
    assert {i["status"] for i in itens} == {"pendente", "concluido"}


@pytest.mark.parametrize("beneficiario,credor", [
    ("ENEL DISTRIBUICAO", "SABESP"),                       # P0-19
    ("SABESP", "Companhia de Saneamento Basico do Estado"),  # P1-28
    ("PGTO DE ENERGIA ELETRICA", "Companhia de Saneamento"),  # P0-21
    ("Agora Ltda", "SABESP"),                              # P0-22
])
def test_a_decisao_do_veto_esta_travada(usuario, beneficiario, credor):
    """TRAVA A DECISÃO, não o estado final.

    As duas primeiras versões desta trava eram documentação: uma assertava
    só sobre `_tokens_de_nome` (protegia a medição, não a regra) e a outra
    olhava o banco no fim do fluxo — e continuou VERDE quando o auditor
    simulou o relaxamento do veto. Quem trocasse a regra veria 797 testes
    passando.

    Agora chama a função que decide, com os quatro pares medidos ao longo
    das auditorias. Qualquer regra nova que volte a fechar um deles quebra
    aqui, na hora.
    """
    venc = _futuro(35)
    iso = "-".join(reversed(venc.split("/")))
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao=f"conta {credor}", valor_reais=150.00,
                data_vencimento=iso, status="pendente")
    dados = {"valor_reais": 150.00, "vencimento_titulo": iso,
             "beneficiario": beneficiario}
    assert wa_bot._conta_pendente_equivalente(usuario["id"], dados) is None, (
        f"o veto deixou '{beneficiario}' quitar 'conta {credor}'")


def test_o_veto_nao_barra_o_mesmo_credor(usuario):
    """O par positivo da trava: sem ele, um veto que recusa TUDO passaria."""
    venc = _futuro(35)
    iso = "-".join(reversed(venc.split("/")))
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta SABESP", valor_reais=150.00,
                data_vencimento=iso, status="pendente")
    dados = {"valor_reais": 150.00, "vencimento_titulo": iso,
             "beneficiario": "SABESP"}
    assert wa_bot._conta_pendente_equivalente(usuario["id"], dados) is not None


# --- P0-22: nome preenchido sem token distintivo -------------------------

@pytest.mark.parametrize("nome", ["Agora Ltda", "Tudo Sim SA", "Ja Cia"])
def test_nome_generico_inteiro_nao_autoriza_baixa(usuario, monkeypatch, nome):
    """Beneficiário PREENCHIDO cujos tokens zeram é o bot não sabendo nada
    sobre aquele nome. `if quem and alvo` tratava isso como permissão —
    fail-open no lugar onde o bloco inteiro é fail-closed."""
    venc = _futuro(35)
    _foto(monkeypatch, _conta("SABESP", "150,00", venc), f"Z{abs(hash(nome))}")
    _foto(monkeypatch, _comprovante(nome, "150,00", venc),
          f"W{abs(hash(nome))}")
    pendentes = db.list_items(usuario["id"], status="pendente")
    assert any("SABESP" in i["descricao"] for i in pendentes), (
        f"'{nome}' (tokens vazios) quitou a conta da SABESP: "
        f"{db.list_items(usuario['id'])}")


def test_comprovante_sem_nome_continua_fechando_pela_chave(usuario,
                                                           monkeypatch):
    """Contrato declarado: SEM beneficiário não há contradição possível, e a
    chave decide. Não pode ser confundido com "nome que zerou"."""
    venc = _futuro(35)
    _foto(monkeypatch, _conta("SABESP", "150,00", venc), "SN1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Vencimento do titulo: {venc}. "
                   f"Valor Pago R$ 150,00")
    _foto(monkeypatch, comprovante, "SN2")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 1 and itens[0]["status"] == "concluido", itens


# --- P2 do auditor: o erro escolhido tem que ser visível -----------------

def test_veto_avisa_qual_conta_ficou_pendente(usuario, monkeypatch):
    """A escolha registrada e ficar com o erro CORRIGIVEL. Pra ser
    corrigivel, ele precisa aparecer — com o comando pronto."""
    venc = _futuro(35)
    _foto(monkeypatch, _conta("Companhia de Saneamento Basico do Estado",
                              "92,10", venc), "AV1")
    reply = _foto(monkeypatch, _comprovante("SABESP", "92,10", venc), "AV2")
    assert "Saneamento" in reply and "paguei" in reply.lower(), (
        f"a conta continuou pendente e a mensagem nao deu sinal: {reply!r}")


# --- trava do P1-7 da auditoria do M2.2: rotulo composto -----------------

@pytest.mark.parametrize("empresa", [
    "Supermercado Total Atacado 2 Ltda",
    "Colegio Data Vida 3 Marias",
    "Escola Recibo Azul 2 Irmaos Ltda",
    "Financeira Valor Justo 10 Ltda",
    "Cooperativa de Credito Valor Mais",
    "Enel Distribuicao Sao Paulo",
    "Light Servicos",
])
def test_razao_social_nao_e_truncada_por_rotulo_composto(empresa):
    """O elo do rotulo composto ("Vencimento DO TITULO: 21/09") com o
    conectivo OPCIONAL aceitava duas palavras quaisquer entre o rotulo e o
    numero — e truncava razao social: "Supermercado Total Atacado 2 Ltda"
    virava "Supermercado". Nome truncado reabre o item fantasma."""
    import boleto
    texto = (f"Boleto Ficha de Compensacao. Beneficiario: {empresa}. "
             f"Vencimento do titulo: 20/09/2026. "
             f"Valor do Documento R$ 100,00")
    benef = (boleto.extrair(texto).get("beneficiario") or "").rstrip(".")
    assert benef.lower().startswith(empresa.split()[0].lower()), benef
    assert len(benef.split()) >= len(empresa.split()) - 1, (
        f"'{empresa}' foi truncado para {benef!r}")


@pytest.mark.parametrize("texto,proibido", [
    ("Boleto. Beneficiario: Enel Distribuicao. Vencimento do titulo: "
     "20/09/2026. Valor do Documento R$ 100,00", "vencimento"),
    ("Boleto. Beneficiario: SABESP. Data do pagamento: 20/08/2026. "
     "Valor do Documento R$ 100,00", "pagamento"),
])
def test_rotulo_composto_continua_sendo_cortado(texto, proibido):
    import boleto
    benef = (boleto.extrair(texto).get("beneficiario") or "").lower()
    assert proibido not in benef, benef
