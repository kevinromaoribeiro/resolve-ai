"""Rodada 6 do M2.1 — o defeito veio da funcionalidade NOVA da rodada.

Duas rodadas seguidas o problema grave nasceu na borda recém-escrita: na 5,
o corte de nome; na 6, a baixa automática pelo comprovante. O núcleo de
classificação parou de se mover.

O P0 desta rodada tinha remédio pronto DENTRO do repositório: `_casar_cauda`
já resolveu "não concluir no escuro" na FASE 1, com placar, desempate e
recusa no empate. A função nova decidia por interseção de uma palavra.
"""
import datetime as _dt

import pytest

import boleto
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


# --- P0-17: nao dar baixa no escuro --------------------------------------

def test_nao_da_baixa_com_dois_candidatos_parecidos(usuario, monkeypatch):
    """'conta ENEL SP' e 'conta ENEL RJ', mesmo valor: 'SP'/'RJ' tem 2
    letras e eram descartados, sobrava 'enel' e o bot fechava a primeira —
    dizendo 'o comprovante confere'."""
    _foto(monkeypatch, _conta("ENEL SP", "92,10", _futuro(35)), "A1")
    _foto(monkeypatch, _conta("ENEL RJ", "92,10", _futuro(40)), "A2")

    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Beneficiario: ENEL RJ. Valor Pago R$ 92,10")
    _foto(monkeypatch, comprovante, "A3")

    pendentes = {i["descricao"] for i in
                 db.list_items(usuario["id"], status="pendente")}
    assert "conta ENEL SP" in pendentes, (
        "deu baixa na conta que NAO foi paga — ela some da lista e nunca "
        "mais dispara lembrete")


def test_nao_da_baixa_entre_dois_iguais(usuario, monkeypatch):
    """Duas SABESP de mesmo valor, vencimentos diferentes: sem desempate,
    escolher e cara ou coroa."""
    _foto(monkeypatch, _conta("SABESP", "92,10", _futuro(35)), "B1")
    _foto(monkeypatch, _conta("SABESP", "92,10", _futuro(65)), "B2")

    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Beneficiario: SABESP. Valor Pago R$ 92,10")
    _foto(monkeypatch, comprovante, "B3")

    pendentes = db.list_items(usuario["id"], status="pendente")
    assert len(pendentes) == 2, (
        f"fechou uma das duas no escuro: {db.list_items(usuario['id'])}")


def test_vencimento_do_titulo_desempata(usuario, monkeypatch):
    """O desempate de graca: o comprovante imprime o vencimento do titulo."""
    venc_a, venc_b = _futuro(35), _futuro(65)
    _foto(monkeypatch, _conta("SABESP", "92,10", venc_a), "C1")
    _foto(monkeypatch, _conta("SABESP", "92,10", venc_b), "C2")

    comprovante = (f"COMPROVANTE DE PAGAMENTO DE TITULO. Pagamento efetuado "
                   f"em {_ontem()}. Beneficiario: SABESP. "
                   f"Vencimento do titulo: {venc_b}. Valor Pago R$ 92,10")
    _foto(monkeypatch, comprovante, "C3")

    iso_b = "-".join(reversed(venc_b.split("/")))
    quitada = [i for i in db.list_items(usuario["id"])
               if i["status"] == "concluido" and i["data_vencimento"] == iso_b]
    assert quitada, (
        f"o vencimento do titulo nao foi usado pra desempatar: "
        f"{db.list_items(usuario['id'])}")


def test_um_candidato_so_continua_dando_baixa(usuario, monkeypatch):
    venc = _futuro(35)
    _foto(monkeypatch, _conta("SABESP", "92,10", venc), "D1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Beneficiario: SABESP. "
                   f"Vencimento do titulo: {venc}. Valor Pago R$ 92,10")
    reply = _foto(monkeypatch, comprovante, "D2")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 1 and itens[0]["status"] == "concluido", itens
    assert "baixa" in reply.lower()


def test_sem_vencimento_do_titulo_nao_fecha(usuario, monkeypatch):
    """CONTRATO DECLARADO: sem o vencimento do titulo no comprovante, o bot
    NAO fecha nada — registra o pagamento e deixa a conta pendente.

    O criterio deixou de ser semelhanca de nome (que errou em 3 rodadas
    seguidas, sempre fechando a conta errada) e passou a ser valor +
    vencimento, os dois impressos no proprio documento. Sem a chave
    completa, quem fecha e a pessoa: 'paguei X', que a mensagem ensina."""
    _foto(monkeypatch, _conta("SABESP", "92,10", _futuro(35)), "E1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Beneficiario: SABESP. Valor Pago R$ 92,10")
    _foto(monkeypatch, comprovante, "E2")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 2, itens
    assert {i["status"] for i in itens} == {"pendente", "concluido"}


def test_nome_generico_nao_fecha_conta_de_outra_empresa(usuario, monkeypatch):
    """`descricao_de` gera sempre "conta <quem>", entao a palavra `conta`
    aparecia em TODA descricao: um comprovante da ENEL quitava a conta da
    SABESP e o bot dizia "o comprovante confere"."""
    venc = _futuro(35)
    _foto(monkeypatch, _conta("SABESP", "92,10", venc), "F1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em "
                   f"{_ontem()}. Beneficiario: CONTA DE LUZ ENEL. "
                   f"Valor Pago R$ 92,10")
    _foto(monkeypatch, comprovante, "F2")
    pendentes = db.list_items(usuario["id"], status="pendente")
    assert any("SABESP" in i["descricao"] for i in pendentes), (
        f"comprovante da ENEL quitou a conta da SABESP: "
        f"{db.list_items(usuario['id'])}")


def test_pago_no_dia_do_vencimento_ainda_desempata(usuario, monkeypatch):
    """Pagar NO DIA do vencimento e o caso mais comum — e era justo nele
    que o sinal era descartado (data de pagamento == vencimento)."""
    hoje = tempo.hoje().strftime("%d/%m/%Y")
    _foto(monkeypatch, _conta("SABESP", "92,10", hoje), "G1")
    comprovante = (f"COMPROVANTE DE PAGAMENTO. Pagamento efetuado em {hoje}. "
                   f"Beneficiario: SABESP. Vencimento do titulo: {hoje}. "
                   f"Valor Pago R$ 92,10")
    _foto(monkeypatch, comprovante, "G2")
    itens = db.list_items(usuario["id"])
    assert len(itens) == 1 and itens[0]["status"] == "concluido", itens


# --- P1-21: carne com cabecalho de entrega -------------------------------

def test_carne_com_comprovante_de_entrega_no_cabecalho():
    """Carimbo e prova; data e mencao. Por isso so o carimbo fica acima do
    canhoto."""
    texto = (f"Comprovante de entrega ao sacado. Carne Ficha de Compensacao. "
             f"Beneficiario: Lojas Americanas. pago em 10/08/2026 referente "
             f"a entrega. Vencimento {_futuro(30)}. "
             f"Valor do Documento R$ 249,90")
    d = boleto.extrair(texto)
    assert d["status_sugerido"] == "pendente", (
        f"carne a pagar entrou como pago: {d}")
    assert "pago em" not in (d["beneficiario"] or "").lower(), d


def test_carimbo_continua_acima_do_canhoto():
    texto = (f"Boleto Ficha de Compensacao. Recibo do Pagador. "
             f"Beneficiario: Sabesp. Vencimento {_futuro()}. "
             f"Valor do Documento R$ 92,10. Autenticacao mecanica: 4455")
    assert boleto.extrair(texto)["status_sugerido"] == "concluido"


# --- P1-22: nome de empresa COM numero -----------------------------------

@pytest.mark.parametrize("empresa", [
    "Rede 3 Coracoes", "Loja 5 Estrelas", "Grupo 3R Servicos",
    "Condominio Edificio 4 Estacoes", "99 Tecnologia Ltda",
    "Enel Distribuicao Sao Paulo", "Companhia Energetica Total",
])
def test_numero_no_nome_nao_trunca(empresa):
    d = boleto.extrair(_conta(empresa, "100,00", "20/09/2026"))
    benef = (d.get("beneficiario") or "").rstrip(".")
    assert benef.lower() == empresa.rstrip(".").lower(), (
        f"'{empresa}' virou {benef!r}")


def test_codigo_continua_cortado_com_a_regra_nova():
    vaz = ("Boleto Ficha de Compensacao Beneficiario: ENEL SP "
           "34191.79001 01043.510047 91020.150008 4 98110000018745 "
           "Valor do Documento R$ 187,45 Vencimento 20/08/2026")
    assert boleto.extrair(vaz)["beneficiario"] == "ENEL SP"


# --- P1-23: historico SEM virgula ----------------------------------------

def test_historico_sem_virgula_nao_come_o_vencimento():
    d = boleto.extrair("Boleto do condominio historico da parcela anterior "
                       "vencimento 10/09/2026 valor do documento R$ 450,00")
    assert d["data_vencimento"] == "2026-09-10", (
        f"OCR de uma linha sem pontuacao perdeu o vencimento: {d}")


def test_parcela_anterior_sem_virgula_continua_ignorada():
    d = boleto.extrair(
        f"BOLETO Ficha de Compensacao Beneficiario: Condominio "
        f"PARCELA ANTERIOR PAGO EM 10/08/2026 "
        f"VENCIMENTO {_futuro()} VALOR DO DOCUMENTO R$ 450,00")
    assert d["status_sugerido"] == "pendente", d
