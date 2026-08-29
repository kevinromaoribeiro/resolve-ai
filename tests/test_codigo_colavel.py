# -*- coding: utf-8 -*-
"""O CODIGO DE PAGAMENTO, PRONTO PRA COLAR.

Pedido do Kevin em 29/08/2026: quando chegar a hora de pagar, o bot devolve o
codigo de barras (ou o PIX copia-e-cola) SEM espaco e SEM ponto, do jeito que
o app do banco aceita — e dizendo qual dos dois e, porque colar codigo de
barras no campo de PIX nao funciona e a pessoa acha que o bot errou.

CUIDADO QUE ATRAVESSA ESTE ARQUIVO: hoje o codigo e REMOVIDO de proposito
(`boleto.sem_codigo_de_pagamento`). O motivo era bom — o OCR inteiro virava
descricao do item e o codigo ficava salvo na lista. Guardar o codigo inverte
essa decisao, entao ele vive em coluna PROPRIA: nunca na descricao, nunca em
log, e so sai no aviso de vencimento.
"""
import pytest

import boleto
import db


# linha digitavel de boleto bancario (47 digitos) e de concessionaria (48)
LINHA_BANCO = "34191.79001 01043.510047 91020.150008 2 91070026000"
LINHA_CONCESS = "846700000017 543100582024 016808791210 176629489407"
PIX_COPIA_COLA = ("00020126580014BR.GOV.BCB.PIX0136123e4567-e12b-12d1-a456-"
                  "426655440000520400005303986540510.005802BR5913Fulano de "
                  "Tal6008BRASILIA62070503***6304ABCD")


# ---------------------------------------------------------------------------
# extrair e normalizar
# ---------------------------------------------------------------------------

def test_le_linha_digitavel_de_banco():
    c = boleto.codigo_de_pagamento(LINHA_BANCO)
    assert c["tipo"] == "boleto"
    assert c["colavel"] == "34191790010104351004791020150008291070026000"
    assert c["colavel"].isdigit(), "ficou espaco ou ponto no que vai pro banco"


def test_le_boleto_de_concessionaria():
    c = boleto.codigo_de_pagamento(LINHA_CONCESS)
    assert c["tipo"] == "boleto"
    assert len(c["colavel"]) == 48, c["colavel"]


def test_le_pix_copia_e_cola():
    c = boleto.codigo_de_pagamento("Pague com PIX: " + PIX_COPIA_COLA)
    assert c["tipo"] == "pix", c
    assert c["colavel"].startswith("00020126")
    assert " " not in c["colavel"]


def test_texto_sem_codigo_devolve_nada():
    for t in ("foto do cachorro", "", None, "conta de luz 187,40"):
        assert boleto.codigo_de_pagamento(t) is None, t


def test_numero_curto_nao_vira_codigo():
    """CPF, telefone e valor NAO podem virar codigo de pagamento."""
    for t in ("meu cpf e 123.456.789-00", "liga 11 98821-5902",
              "valor 1.234,56"):
        assert boleto.codigo_de_pagamento(t) is None, t


# ---------------------------------------------------------------------------
# a mensagem que a pessoa recebe
# ---------------------------------------------------------------------------

def test_a_mensagem_diz_qual_e_o_tipo():
    """Colar codigo de barras no campo de PIX nao funciona."""
    m_boleto = boleto.bloco_para_pagar(
        {"tipo": "boleto", "colavel": "3419179001010435100479102015000829107002600"})
    assert "barras" in m_boleto.lower()
    assert "pix" not in m_boleto.lower()

    m_pix = boleto.bloco_para_pagar({"tipo": "pix", "colavel": "00020126ABC"})
    assert "pix" in m_pix.lower()
    assert "barras" not in m_pix.lower()


def test_o_codigo_sai_sozinho_numa_linha():
    """Se vier grudado em texto, o toque-e-copia do WhatsApp pega junk."""
    m = boleto.bloco_para_pagar(
        {"tipo": "boleto", "colavel": "34191790010104351004791020150008291070026000"})
    linhas = [l.strip() for l in m.split("\n") if l.strip()]
    assert "34191790010104351004791020150008291070026000" in linhas, linhas


def test_sem_codigo_nao_inventa_bloco():
    assert boleto.bloco_para_pagar(None) == ""
    assert boleto.bloco_para_pagar({"tipo": "boleto", "colavel": ""}) == ""


# ---------------------------------------------------------------------------
# onde o codigo mora: coluna propria, nunca na descricao
# ---------------------------------------------------------------------------

def test_guarda_em_coluna_propria(usuario):
    iid = db.add_item(user_id=usuario["id"], tipo="despesa",
                      categoria="Contas", descricao="conta de luz",
                      valor_reais=187.40, status="pendente",
                      codigo_pagamento="34191790010104351004791020150008291070026000",
                      codigo_tipo="boleto")
    it = db.get_item(iid)
    assert it["codigo_pagamento"].isdigit()
    assert it["codigo_tipo"] == "boleto"
    assert "3419" not in (it["descricao"] or ""), (
        "codigo vazou pra descricao — e o que a pessoa ve na lista")


def test_item_sem_codigo_continua_funcionando(usuario):
    """A coluna e opcional: a maioria dos itens nao tem codigo nenhum."""
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="dentista",
                      valor_reais=None, status="pendente")
    it = db.get_item(iid)
    assert it["codigo_pagamento"] is None


# ---------------------------------------------------------------------------
# de ponta a ponta: entra no boleto, sai no aviso
# ---------------------------------------------------------------------------

def test_o_aviso_de_vencimento_leva_o_codigo(usuario, horario_util):
    """O momento em que o codigo serve pra alguma coisa e a hora de pagar."""
    import datetime as _dt
    import scheduler
    import tempo
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta de luz", valor_reais=187.40,
                data_vencimento=(tempo.hoje() + _dt.timedelta(days=1)
                                 ).isoformat(),
                status="pendente",
                codigo_pagamento="34191790010104351004791020150008291070026000",
                codigo_tipo="boleto")
    disp = scheduler.check_due_items()
    assert disp, "nem disparou"
    msg = disp[0]["message"]
    assert "34191790010104351004791020150008291070026000" in msg, msg
    assert "barras" in msg.lower()


def test_item_sem_codigo_avisa_normal(usuario, horario_util):
    """A maioria dos itens nao tem codigo — o aviso nao pode ficar estranho."""
    import datetime as _dt
    import scheduler
    import tempo
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Saúde",
                descricao="dentista", valor_reais=None,
                data_vencimento=(tempo.hoje() + _dt.timedelta(days=1)
                                 ).isoformat(), status="pendente")
    disp = scheduler.check_due_items()
    assert disp
    msg = disp[0]["message"]
    assert "barras" not in msg.lower() and "pix" not in msg.lower(), msg


def test_o_codigo_nunca_aparece_na_lista(usuario):
    """`ver tudo` mostra a descricao — e o codigo nao pode estar la."""
    import wa_bot
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta de luz", valor_reais=187.40,
                status="pendente",
                codigo_pagamento="34191790010104351004791020150008291070026000",
                codigo_tipo="boleto")
    resp = wa_bot._handle_commands(usuario, usuario["telefone"], "ver tudo") or ""
    assert "3419179" not in resp, "codigo vazou pra lista da pessoa"


def test_o_codigo_nao_entra_no_template():
    """Template com codigo de pagamento e recusa certa na Meta — e ficaria
    gravado no historico dela."""
    import templates
    for nome, t in templates.CATALOGO.items():
        assert "barras" not in t.corpo.lower(), nome
        assert "0002012" not in t.corpo, nome


def test_a_foto_do_boleto_guarda_o_codigo(usuario, monkeypatch):
    """De ponta a ponta de verdade: OCR -> item com codigo guardado."""
    import ai_engine
    import wa_bot
    monkeypatch.setattr(ai_engine, "classify_category", lambda d: "Contas")
    ocr = ("ENEL DISTRIBUICAO SAO PAULO\n"
           "Vencimento 20/09/2026   Valor a pagar R$ 187,40\n"
           + LINHA_BANCO)
    resp = wa_bot._registrar_documento_financeiro(
        usuario, usuario["telefone"], ocr, legenda="")
    assert resp, "o boleto nem virou item"
    itens = db.list_items(usuario["id"], status="pendente")
    assert itens, itens
    it = db.get_item(itens[0]["id"])
    assert it["codigo_pagamento"] == "34191790010104351004791020150008291070026000"
    assert it["codigo_tipo"] == "boleto"
    assert "3419" not in (it["descricao"] or "")


def test_comprovante_pago_nao_guarda_codigo(usuario, monkeypatch):
    """Conta ja paga nao precisa de codigo — nao carrega dado sensivel a toa."""
    import ai_engine
    import wa_bot
    monkeypatch.setattr(ai_engine, "classify_category", lambda d: "Contas")
    ocr = ("ENEL SAO PAULO\nVencimento 20/09/2026  Valor R$ 187,40\n"
           + LINHA_BANCO)
    wa_bot._registrar_documento_financeiro(
        usuario, usuario["telefone"], ocr, legenda="essa eu já paguei")
    for it in db.list_items(usuario["id"]):
        cheio = db.get_item(it["id"])
        if cheio["status"] == "concluido":
            assert not cheio["codigo_pagamento"], cheio


# ---------------------------------------------------------------------------
# AUDITORIA M3.5 — P1-6 e P1-7: o codigo nao pode vazar
# ---------------------------------------------------------------------------

def test_codigo_nao_vai_pro_log_quando_o_envio_falha(usuario, horario_util,
                                                     monkeypatch):
    """P1-6: na falha de envio o log gravava os 200 primeiros chars da
    mensagem — e a mensagem agora carrega o codigo. Regra do projeto: o
    codigo NUNCA vai pra log."""
    import datetime as _dt
    import tempo
    import wa_bot
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="luz", valor_reais=None,
                data_vencimento=(tempo.hoje() + _dt.timedelta(days=1)
                                 ).isoformat(), status="pendente",
                codigo_pagamento="34191790010104351004791020150008291070026000",
                codigo_tipo="boleto")
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": False, "via": None,
                                         "motivo": "falha_no_envio"})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()
    with db.get_conn() as c:
        linhas = [r["preview"] or "" for r in
                  c.execute("SELECT preview FROM msg_log")]
    for p in linhas:
        assert "3419179" not in p, "codigo de pagamento no log: %r" % p


def test_pix_sem_crc_nao_engole_o_ocr(usuario):
    """P1-7: sem o CRC final, o fallback devolvia TODO o resto do texto como
    'codigo' — CPF, nome e endereco iam pra coluna, pra mensagem e pro log."""
    import boleto
    ocr = ("00020126580014BR.GOV.BCB.PIX0136abc-def-ghi\n"
           "Beneficiario: JOAO DA SILVA CPF 123.456.789-00\n"
           "Endereco: Rua das Flores 100  Telefone 11 98888-7777")
    c = boleto.codigo_de_pagamento(ocr)
    if c is not None:
        assert "123.456.789" not in c["colavel"], c
        assert "JOAO" not in c["colavel"].upper(), c
        assert len(c["colavel"]) < 512, len(c["colavel"])


def test_pix_com_crc_continua_funcionando():
    import boleto
    c = boleto.codigo_de_pagamento("Pague: " + PIX_COPIA_COLA)
    assert c and c["tipo"] == "pix"
    assert c["colavel"].endswith("6304ABCD")


def test_valor_na_mesma_linha_nao_esconde_o_codigo():
    """P2 da auditoria: 'Valor 187.40 34191.79001...' devolvia None — o
    ponto do valor entrava no conjunto e o codigo ficava com digito a mais."""
    import boleto
    c = boleto.codigo_de_pagamento(
        "Valor do documento 187.40 " + LINHA_BANCO)
    assert c, "codigo perdido por causa do valor na mesma linha"
    assert c["colavel"] == "34191790010104351004791020150008291070026000"
