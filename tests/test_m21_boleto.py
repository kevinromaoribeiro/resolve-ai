"""M2.1 — foto de boleto vira item com data. E NUNCA vira pagamento.

Guardrail do produto, nao negociavel: o bot le o boleto, guarda e avisa
antes de vencer. Ele nao paga, nao gera PIX, nao devolve linha digitavel
pronta pra copiar. Metade destes testes existe so pra travar isso.

O que e Python e o que e LLM aqui: a visao le os pixels (nao tem como ser
outra coisa), mas quem decide se aquilo e boleto, se a data faz sentido e
se vira despesa paga ou lembrete e o Python.
"""
import datetime as _dt

import pytest

import boleto
import db
import tempo
import wa_bot
from conftest import TELEFONE

OCR_BOLETO = (
    "Boleto bancário da Enel Distribuição, valor R$ 187,45, "
    "vencimento em 20/08/2026, beneficiário ENEL DISTRIBUICAO SP. "
    "DADOS: valor=187,45; vencimento=20/08/2026; beneficiario=Enel; "
    "tipo=boleto; linha_digitavel=03399.63290 64000.000006 00125.201020 4 12345678901234"
)
OCR_COMPROVANTE = (
    "Comprovante de pagamento no valor de R$ 250,00 pago em 14/08/2026 "
    "para SUPERMERCADO XYZ. DADOS: valor=250,00; vencimento=14/08/2026; "
    "beneficiario=Supermercado XYZ; tipo=comprovante"
)


# --- extracao deterministica --------------------------------------------

def test_extrai_valor_vencimento_beneficiario():
    d = boleto.extrair(OCR_BOLETO)
    assert d is not None
    assert d["valor_reais"] == 187.45
    assert d["data_vencimento"] == "2026-08-20"
    assert "enel" in (d["beneficiario"] or "").lower()
    assert d["tipo"] == "boleto"


@pytest.mark.parametrize("texto,esperado", [
    # "boleto"/"vencimento" nos textos: valor sozinho NAO faz um documento
    # ser conta (cardapio e extrato tambem tem valor). Ver test_m21_auditoria.
    ("boleto, valor R$ 1.234,56 vence 05/09/2026", 1234.56),
    ("boleto R$ 89,90 vencimento 01/12/2026", 89.90),
    ("boleto, total de 45,00 reais, vence em 03/10/2026", 45.00),
    ("fatura R$ 1.000,00 vencimento 15/11/2026", 1000.00),
])
def test_formatos_de_valor_brasileiros(texto, esperado):
    d = boleto.extrair(texto)
    assert d and d["valor_reais"] == esperado, d


@pytest.mark.parametrize("texto,iso", [
    ("R$ 10,00 vencimento 20/08/2026", "2026-08-20"),
    ("R$ 10,00 vence 20/08/26", "2026-08-20"),
    ("R$ 10,00 vencimento 2026-08-20", "2026-08-20"),
])
def test_formatos_de_data(texto, iso):
    d = boleto.extrair(texto)
    assert d and d["data_vencimento"] == iso, d


def test_comprovante_e_despesa_ja_paga():
    """Comprovante nao pode virar lembrete de cobranca: seria o bot
    cobrando algo que a pessoa JA pagou."""
    d = boleto.extrair(OCR_COMPROVANTE)
    assert d["tipo"] == "comprovante"
    assert d["status_sugerido"] == "concluido"


def test_boleto_e_lembrete_pendente():
    d = boleto.extrair(OCR_BOLETO)
    assert d["status_sugerido"] == "pendente"


def test_sem_valor_nao_e_boleto():
    """Foto de cardapio, print de conversa, foto do cachorro: nao inventa."""
    assert boleto.extrair("Foto de um cachorro caramelo deitado no sofa") is None
    assert boleto.extrair("") is None
    assert boleto.extrair(None) is None


def test_data_absurda_e_recusada():
    """Data que a visao leu errado nao pode virar lembrete pra 2049."""
    d = boleto.extrair("R$ 100,00 vencimento 20/08/2049")
    assert d is None or d["data_vencimento"] is None, d


def test_data_muito_velha_e_recusada():
    d = boleto.extrair("R$ 100,00 vencimento 20/08/2019")
    assert d is None or d["data_vencimento"] is None, d


# --- GUARDRAIL: le e lembra, nunca paga ---------------------------------

def test_linha_digitavel_nunca_e_guardada():
    """Guardar codigo de pagamento nao ajuda a lembrar de nada e e o
    primeiro passo pra alguem imaginar que o bot paga."""
    d = boleto.extrair(OCR_BOLETO)
    texto_do_item = " ".join(str(v) for v in d.values() if v)
    assert "03399" not in texto_do_item, (
        f"a linha digitavel vazou pro item: {d}")
    assert "12345678901234" not in texto_do_item


def test_extracao_nao_devolve_acao_de_pagamento():
    d = boleto.extrair(OCR_BOLETO)
    proibidos = ("pagar", "pague", "pix", "copia e cola", "codigo de barras",
                 "linha digitavel")
    achados = [p for p in proibidos
               if p in " ".join(str(v).lower() for v in d.values() if v)]
    assert not achados, achados


def test_resposta_ao_usuario_nao_promete_pagamento(usuario, monkeypatch):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: OCR_BOLETO)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "IMG_BOL"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")
    baixo = reply.lower()
    for proibido in ("pagar por voce", "pago pra voce", "efetuar o pagamento",
                     "quer que eu pague", "pix"):
        assert proibido not in baixo, f"{proibido!r} apareceu em: {reply!r}"


# --- fluxo: foto vira item com data -------------------------------------

def test_foto_de_boleto_vira_item_com_data(usuario, monkeypatch):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: OCR_BOLETO)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "IMG_BOL2"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}

    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")

    itens = db.list_items(usuario["id"])
    assert len(itens) == 1, f"a foto nao virou item: {itens} / {reply!r}"
    it = itens[0]
    assert it["valor_reais"] == 187.45
    assert it["data_vencimento"] == "2026-08-20"
    assert it["status"] == "pendente"
    assert "187,45" in reply and "20/08" in reply, reply


def test_foto_de_comprovante_vira_despesa_paga(usuario, monkeypatch):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: OCR_COMPROVANTE)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "IMG_COMP"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}

    wa_bot.handle_incoming(payload)

    itens = db.list_items(usuario["id"])
    assert len(itens) == 1, itens
    assert itens[0]["status"] == "concluido", (
        "comprovante virou cobranca: o bot vai cobrar algo que ja foi pago")


def test_imagem_sem_dado_financeiro_mantem_o_menu(usuario, monkeypatch):
    """A Regra de Ouro continua valendo pra imagem que o Python nao
    entendeu: perguntar e melhor do que inventar."""
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image",
                        lambda b64: "Foto de um cachorro no sofa")
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "IMG_DOG"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}

    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")

    assert not db.list_items(usuario["id"]), "inventou item de uma foto de cachorro"
    assert reply.strip(), "ficou mudo"


def test_boleto_duplicado_nao_vira_dois_itens(usuario, monkeypatch):
    """Mandar a mesma foto duas vezes (acontece) nao pode dobrar a conta."""
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: OCR_BOLETO)
    for i in (1, 2):
        payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                    "fromMe": False, "id": f"IMG_DUP{i}"},
                            "pushName": "Kevin",
                            "message": {"imageMessage": {"caption": ""}}}}
        wa_bot.handle_incoming(payload)
    assert len(db.list_items(usuario["id"])) == 1, (
        "a mesma conta entrou duas vezes")


def test_a_correcao_prometida_na_mensagem_funciona(usuario, monkeypatch):
    """A resposta diz: _"se essa ja esta paga, e so me dizer 'paguei X'"_.

    Promessa em copy que o codigo nao cumpre foi exatamente o P1-7 do M2.0
    (o template mandava responder "ver tudo", que ninguem implementava).
    Aqui o teste executa a frase que a propria mensagem sugere.
    """
    import canal
    import motor_v8
    from conftest import responder
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: OCR_BOLETO)
    monkeypatch.setattr(motor_v8, "route", lambda *a, **kw: None)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": "IMG_PROM"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")

    # a frase que a mensagem manda o usuario responder, extraida dela mesma
    import re as _re
    m = _re.search(r'_"([^"]+)"_', reply)
    assert m, f"a mensagem nao sugere frase nenhuma: {reply!r}"
    sugerida = m.group(1)

    responder(sugerida)

    itens = db.list_items(usuario["id"])
    assert itens and itens[0]["status"] == "concluido", (
        f"a frase sugerida ({sugerida!r}) nao deu baixa: {itens}")


# --- PDF ------------------------------------------------------------------

def test_pdf_sem_biblioteca_nao_quebra(monkeypatch):
    """Se o pypdf nao estiver no build, o caminho degrada pro pedido de
    print — nunca estoura."""
    monkeypatch.setattr(boleto, "_LEITOR_PDF", None)
    assert boleto.texto_de_pdf(b"%PDF-1.4 qualquer coisa") is None


def test_pdf_com_texto_extrai_boleto(monkeypatch):
    """Boleto de banco em PDF e texto, nao imagem — da pra ler sem OCR."""
    monkeypatch.setattr(boleto, "_LEITOR_PDF",
                        lambda dados: "Enel R$ 187,45 vencimento 20/08/2026")
    texto = boleto.texto_de_pdf(b"%PDF-1.4")
    assert texto and "187,45" in texto
    d = boleto.extrair(texto)
    assert d["valor_reais"] == 187.45
