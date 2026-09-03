# -*- coding: utf-8 -*-
"""ASSINATURA CONTROLADA NA MAO PELO DONO.

O Kevin decidiu (28/08/2026) que ninguem vira pagante sozinho: quando a
pessoa pede o link, ELE vai no Mercado Pago conferir e so entao aprova,
dizendo se foi MENSAL ou ANUAL. O dia em que ele aprova e o dia em que o
ciclo comeca a contar.

O motivo e concreto: o bot nao tem como saber se o cartao passou, e cobrar
quem ja pagou — ou cobrar quem pagou o ANUAL — e o jeito mais rapido de
perder um cliente de 11.

Regra que atravessa este arquivo: NENHUMA cobranca sai sozinha. O bot manda
o link quando a pessoa pede, e mais nada. Segunda cobranca so por comando
explicito do dono.
"""
import datetime as _dt

import pytest

import db
import tempo


def _u(nome="Cliente", tel="5511970000001"):
    return db.create_user(nome=nome, telefone=tel)


# ---------------------------------------------------------------------------
# aprovar: plano + data de inicio
# ---------------------------------------------------------------------------

def test_aprovar_marca_plano_status_e_inicio(usuario):
    uid = usuario["id"]
    assert db.aprovar_pagamento(uid, "mensal", por="painel") is True
    u = db.get_user(uid)
    assert u["status"] == "ativo"
    assert u["plano"] == "mensal"
    assert u["pago_em"][:10] == tempo.hoje().isoformat(), u["pago_em"]


def test_mensal_vence_no_mesmo_dia_do_mes_seguinte(usuario):
    uid = usuario["id"]
    db.aprovar_pagamento(uid, "mensal", em="2026-03-15")
    a = db.assinatura(db.get_user(uid), hoje=_dt.date(2026, 3, 20))
    assert a["vence_em"] == "2026-04-15", a
    assert a["dias_para_vencer"] == 26, a
    assert a["atrasado"] is False


def test_anual_vence_um_ano_depois(usuario):
    uid = usuario["id"]
    db.aprovar_pagamento(uid, "anual", em="2026-03-15")
    a = db.assinatura(db.get_user(uid), hoje=_dt.date(2026, 4, 1))
    assert a["vence_em"] == "2027-03-15", a
    assert a["atrasado"] is False


def test_dia_31_nao_estoura_em_mes_curto(usuario):
    """31/01 + 1 mes nao existe em fevereiro: cai no ultimo dia."""
    uid = usuario["id"]
    db.aprovar_pagamento(uid, "mensal", em="2026-01-31")
    a = db.assinatura(db.get_user(uid), hoje=_dt.date(2026, 2, 1))
    assert a["vence_em"] == "2026-02-28", a


def test_atraso_conta_os_dias(usuario):
    uid = usuario["id"]
    db.aprovar_pagamento(uid, "mensal", em="2026-03-15")
    a = db.assinatura(db.get_user(uid), hoje=_dt.date(2026, 4, 20))
    assert a["atrasado"] is True
    assert a["dias_atraso"] == 5, a


def test_quem_nao_e_pagante_nao_tem_vencimento(usuario):
    a = db.assinatura(db.get_user(usuario["id"]))
    assert a["plano"] is None and a["vence_em"] is None
    assert a["atrasado"] is False


def test_plano_invalido_e_recusado(usuario):
    with pytest.raises(ValueError):
        db.aprovar_pagamento(usuario["id"], "vitalicio")


def test_aprovar_fica_no_log_de_admin(usuario):
    db.aprovar_pagamento(usuario["id"], "anual", por="kevin")
    acoes = db.acoes_administrativas("aprovar_pagamento")
    assert acoes and acoes[0]["por"] == "kevin", acoes
    assert "anual" in (acoes[0]["detalhe"] or "")


# ---------------------------------------------------------------------------
# quem pediu o link e ainda nao foi aprovado
# ---------------------------------------------------------------------------

def test_pedido_de_link_entra_na_fila_de_aprovacao(usuario, monkeypatch):
    import wa_bot
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True, "via": "texto",
                                         "motivo": ""})
    wa_bot._handle_commands(usuario, usuario["telefone"], "assinar")
    pend = db.aguardando_aprovacao()
    assert [p["id"] for p in pend] == [usuario["id"]], pend
    assert pend[0]["pediu_ha_dias"] == 0


def test_aprovado_sai_da_fila(usuario, monkeypatch):
    import wa_bot
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True, "via": "texto",
                                         "motivo": ""})
    wa_bot._handle_commands(usuario, usuario["telefone"], "assinar")
    db.aprovar_pagamento(usuario["id"], "mensal")
    assert db.aguardando_aprovacao() == []


def test_nada_cobra_sozinho(usuario):
    """A REGRA: vencimento nao gera disparo. So o dono cobra.

    O motor proativo nao pode ganhar um caminho de cobranca de mensalidade —
    ele nao sabe se o cartao passou, e cobrar quem ja pagou (ou quem pagou o
    anual) e o jeito mais rapido de perder um dos 11.
    """
    import scheduler
    db.aprovar_pagamento(usuario["id"], "mensal", em="2026-01-01")
    for nome in dir(scheduler):
        if nome.startswith("check_"):
            fn = getattr(scheduler, nome)
            try:
                saida = fn()
            except TypeError:
                continue
            for d in (saida or []):
                assert "cobran" not in (d.get("kind") or ""), d
                assert "mensalidade" not in (d.get("message") or "").lower(), d


# ---------------------------------------------------------------------------
# as acoes do painel
# ---------------------------------------------------------------------------

def _painel(monkeypatch):
    from fastapi.testclient import TestClient
    import wa_bot
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    return TestClient(wa_bot.app)


def _acao(c, uid, acao, **extra):
    return c.post("/painel/acao?k=tok",
                  json={"user_id": uid, "acao": acao, **extra})


def test_painel_aprova_com_plano(usuario, monkeypatch):
    c = _painel(monkeypatch)
    r = _acao(c, usuario["id"], "aprovar", plano="anual")
    assert r.status_code == 200 and r.json().get("ok"), r.text
    u = db.get_user(usuario["id"])
    assert u["status"] == "ativo" and u["plano"] == "anual"


def test_painel_recusa_plano_invalido(usuario, monkeypatch):
    c = _painel(monkeypatch)
    r = _acao(c, usuario["id"], "aprovar", plano="vitalicio")
    assert not r.json().get("ok"), r.text
    assert db.get_user(usuario["id"])["status"] != "ativo"


def test_painel_estende_o_numero_de_dias_que_eu_mandar(usuario, monkeypatch):
    c = _painel(monkeypatch)
    antes = db.trial_days_left(db.get_user(usuario["id"]))
    assert _acao(c, usuario["id"], "estender", dias=21).json().get("ok")
    depois = db.trial_days_left(db.get_user(usuario["id"]))
    assert depois - antes == 21, (antes, depois)


def test_painel_bloqueia_e_reativa(usuario, monkeypatch):
    c = _painel(monkeypatch)
    _acao(c, usuario["id"], "bloquear")
    assert db.get_user(usuario["id"])["status"] == "bloqueado"
    _acao(c, usuario["id"], "liberar")
    assert db.get_user(usuario["id"])["status"] == "trial"


def test_painel_manda_template_aprovado(usuario, monkeypatch):
    import wa_bot
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: (enviados.append((tel, kw.get("template")))
                                or {"enviado": True, "via": "template",
                                    "motivo": ""}))
    c = _painel(monkeypatch)
    r = _acao(c, usuario["id"], "enviar_template",
              template="resolveai_fim_de_trial_aviso")
    assert r.json().get("ok"), r.text
    assert enviados and enviados[0][1] == "resolveai_fim_de_trial_aviso"


def test_template_sem_dado_pra_variavel_nao_sai(usuario, monkeypatch):
    """Variavel vazia e recusa da Meta — e reputacao do numero queimada."""
    import wa_bot
    chamou = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: chamou.append(1) or {"enviado": True})
    c = _painel(monkeypatch)
    r = _acao(c, usuario["id"], "enviar_template",       # sem item pendente
              template="resolveai_reengajamento_pendentes")
    assert not r.json().get("ok"), r.text
    assert not chamou, "template com variavel vazia chegou ao envio"


def test_painel_recusa_template_fora_do_catalogo(usuario, monkeypatch):
    import wa_bot
    chamou = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: chamou.append(1) or {"enviado": True})
    c = _painel(monkeypatch)
    r = _acao(c, usuario["id"], "enviar_template", template="promocao_50_off")
    assert not r.json().get("ok"), r.text
    assert not chamou, "template inventado chegou ao envio"


def test_envio_manual_fica_no_log_de_admin(usuario, monkeypatch):
    import wa_bot
    # o reengajamento fala de UM item: sem item pendente o envio e recusado
    # antes de sair (variavel vazia e recusa da Meta e reputacao queimada)
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta de luz", valor_reais=90.0, status="pendente")
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True, "via": "template",
                                         "motivo": ""})
    c = _painel(monkeypatch)
    _acao(c, usuario["id"], "enviar_template",
          template="resolveai_reengajamento_pendentes")
    acoes = db.acoes_administrativas("enviar_template")
    assert acoes, "envio manual sem rastro no log"


def test_acao_do_painel_exige_token(usuario):
    from fastapi.testclient import TestClient
    import wa_bot
    c = TestClient(wa_bot.app)
    r = c.post("/painel/acao?k=errado",
               json={"user_id": usuario["id"], "acao": "aprovar",
                     "plano": "anual"})
    assert r.status_code != 200 or not r.json().get("ok")
    assert db.get_user(usuario["id"])["status"] != "ativo"


# ---------------------------------------------------------------------------
# a tela de controle
# ---------------------------------------------------------------------------

def test_dash_traz_os_controles(usuario, monkeypatch):
    """JS quebrado aqui deixa o painel BRANCO — sem log, sem stack."""
    c = _painel(monkeypatch)
    t = c.get("/dash?k=tok").text
    for marca in ("function filtro(", "function mandar(", "function dias(",
                  "function cobrar(", "Pediram o link", "Clientes",
                  "'aprovar',{plano:'mensal'}", "'aprovar',{plano:'anual'}"):
        assert marca in t, "sumiu da tela: %r" % marca


def test_pulso_traz_assinatura_fila_e_templates(usuario, monkeypatch):
    db.aprovar_pagamento(usuario["id"], "mensal", em="2026-01-10")
    c = _painel(monkeypatch)
    j = c.get("/api/pulso?k=tok").json()
    assert "aprovacoes" in j and "templates" in j, sorted(j)
    assert j["templates"], "nenhum template ofertado pro envio manual"
    eu = [u for u in j["usuarios"] if u["id"] == usuario["id"]][0]
    assert eu["assinatura"]["plano"] == "mensal"
    assert eu["assinatura"]["atrasado"] is True


def test_so_oferece_template_que_sabe_preencher(monkeypatch):
    """Botao que so falha depois de clicado e pior que botao ausente."""
    import wa_bot
    import templates as _cat
    # A lista vem do CODIGO, nao copiada aqui: uma copia no teste vira uma
    # segunda verdade que diverge na primeira variavel nova — foi assim que
    # `trial_estendido` ficou de fora do painel sem ninguem perceber.
    #
    # As LIVRES entraram junto: sao as que o DONO digita no painel na hora do
    # envio (o nome da novidade e a explicacao). Continuam preenchiveis — o
    # que muda e QUEM preenche.
    sei = wa_bot.VARIAVEIS_QUE_SEI_PREENCHER | wa_bot.VARIAVEIS_LIVRES
    for nome in wa_bot._templates_manuais():
        faltando = set(_cat.CATALOGO[nome].variaveis or []) - sei
        assert not faltando, (nome, faltando)


def test_template_que_pede_texto_avisa_o_painel():
    """A garantia do teste acima so vale se o painel ABRIR os campos.

    Uma variavel livre que o painel nao sabe que precisa pedir e pior que o
    caso original: o botao aparece, o dono clica, e a recusa vem depois —
    exatamente o "botao que so falha depois de clicado" que este arquivo
    existe pra impedir.
    """
    import wa_bot
    import templates as _cat
    for t in wa_bot._templates_com_rotulo():
        livres = (set(_cat.CATALOGO[t["nome"]].variaveis or [])
                  & wa_bot.VARIAVEIS_LIVRES)
        assert set(t["pede_texto"]) == livres, t["nome"]


def test_reenviar_link_fora_da_janela_explica_o_motivo(usuario, monkeypatch):
    """Fora da janela nao existe template pra cobranca — e nao se inventa um.

    A recusa tem que chegar na tela com o motivo, senao o Kevin clica, nada
    acontece e ele acha que cobrou.
    """
    import wa_bot
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda *a, **k: {"enviado": False, "via": None,
                         "motivo": "fora_da_janela_sem_template"})
    c = _painel(monkeypatch)
    r = _acao(c, usuario["id"], "reenviar_link")
    j = r.json()
    assert not j.get("ok")
    assert "24h" in (j.get("erro") or ""), j
