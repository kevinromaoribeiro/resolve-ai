# -*- coding: utf-8 -*-
"""BOTAO NO LUGAR DE DIGITACAO.

O Kevin: "as decisoes dos clientes devem ser via botoes, o minimo de escrita
possivel, ninguem quer ficar digitando".

A metade receptora ja existia: `meta_cloud.to_evolution_shape` converte
`button_reply.title` em texto, entao clique de botao entra no mesmo parser
de sempre. Faltava ENVIAR com botao — e o titulo do botao TEM que ser uma
palavra que o bot ja entende, senao o clique vira "nao entendi".
"""
import re

import pytest

import canal
import meta_cloud
import wa_bot


# ---------------------------------------------------------------------------
# o payload que vai pra Meta
# ---------------------------------------------------------------------------

def test_monta_payload_interativo(monkeypatch):
    capturado = {}

    class _Resp:
        status_code = 200
        text = '{"messages":[{"id":"wamid.X"}]}'

        def json(self):
            return {"messages": [{"id": "wamid.X"}]}

    def _post(url, **kw):
        capturado["url"] = url
        capturado["json"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(meta_cloud, "configurado", lambda: True)
    import httpx
    monkeypatch.setattr(httpx, "post", _post)
    ok = meta_cloud.send_buttons("5511999998888", "Pagou a luz?",
                                 ["Paguei", "Adiar"])
    assert ok is True
    j = capturado["json"]
    assert j["type"] == "interactive"
    inter = j["interactive"]
    assert inter["type"] == "button"
    assert inter["body"]["text"] == "Pagou a luz?"
    titulos = [b["reply"]["title"] for b in inter["action"]["buttons"]]
    assert titulos == ["Paguei", "Adiar"]
    # id != title de proposito? nao: o webhook le o TITLE. Mas o id nao pode
    # faltar, a Meta rejeita sem ele.
    assert all(b["reply"]["id"] for b in inter["action"]["buttons"])


def test_no_maximo_tres_botoes():
    """A Meta aceita 3. Mandar 4 e erro 400 e mensagem que nao sai."""
    with pytest.raises(ValueError):
        meta_cloud._validar_botoes(["a", "b", "c", "d"])


def test_titulo_de_botao_tem_limite_de_20_chars():
    with pytest.raises(ValueError):
        meta_cloud._validar_botoes(["esse titulo aqui e comprido demais"])


def test_botao_vazio_e_recusado():
    with pytest.raises(ValueError):
        meta_cloud._validar_botoes(["Paguei", "  "])


# ---------------------------------------------------------------------------
# a regra que importa: o clique tem que virar comando conhecido
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("titulo", ["Paguei", "Feito", "Adiar", "Ver tudo",
                                    "Assinar"])
def test_todo_botao_que_usamos_e_comando_que_o_bot_entende(titulo, usuario,
                                                           monkeypatch):
    """Botao que o bot nao entende e pior que botao nenhum.

    O webhook manda o TITULO como se a pessoa tivesse digitado. Se o titulo
    nao casar com um comando, o clique cai no "nao entendi" — e a pessoa que
    escolheu o caminho mais facil e a que leva a pior resposta.
    """
    ent = wa_bot.entende_comando(titulo)
    assert ent, "o titulo %r nao vira comando nenhum" % titulo


def test_botoes_declarados_no_codigo_passam_todos(usuario):
    """Varre os botoes que o motor realmente usa."""
    for kind, botoes in wa_bot.BOTOES_POR_KIND.items():
        assert 1 <= len(botoes) <= 3, (kind, botoes)
        for b in botoes:
            assert wa_bot.entende_comando(b), (kind, b)


# ---------------------------------------------------------------------------
# a porta unica continua sendo a porta unica
# ---------------------------------------------------------------------------

def test_fora_da_janela_botao_nao_abre_excecao(usuario, monkeypatch):
    """Botao e mensagem interativa: fora da janela, ela NAO sai.

    Se `falar` deixasse passar por ser "interativa", teriamos aberto uma
    porta nova pra texto livre fora da janela — o que rendeu duas restricoes
    neste numero.
    """
    chamou = []
    monkeypatch.setattr(canal, "send_buttons",
                        lambda *a, **k: chamou.append(1) or True)
    monkeypatch.setattr(canal, "send_text",
                        lambda *a, **k: chamou.append(1) or True)
    res = canal.falar("5511900001111", "oi", user_id=usuario["id"],
                      botoes=["Paguei"])
    assert not res.get("enviado")
    assert res.get("motivo") == "fora_da_janela_sem_template"
    assert not chamou, "mensagem interativa escapou da janela de 24h"


def test_dentro_da_janela_sai_com_botao(usuario, monkeypatch):
    import db
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    vistos = {}
    monkeypatch.setattr(canal, "send_buttons",
                        lambda tel, txt, bts, **k: vistos.update(
                            {"tel": tel, "txt": txt, "bts": bts}) or True)
    res = canal.falar(usuario["telefone"], "Pagou a luz?",
                      user_id=usuario["id"], botoes=["Paguei", "Adiar"])
    assert res["enviado"] and res["via"] == "botoes", res
    assert vistos["bts"] == ["Paguei", "Adiar"]


def test_sem_botoes_continua_texto(usuario, monkeypatch):
    import db
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    monkeypatch.setattr(canal, "send_text", lambda *a, **k: True)
    res = canal.falar(usuario["telefone"], "oi", user_id=usuario["id"])
    assert res["via"] == "texto", res


# ---------------------------------------------------------------------------
# o motor manda os botoes junto
# ---------------------------------------------------------------------------

def test_lembrete_de_vencimento_sai_com_botao(usuario, monkeypatch):
    import datetime as dt
    import db
    import tempo
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta de luz", valor_reais=120.0,
                data_vencimento=(tempo.hoje() + dt.timedelta(days=1)
                                 ).isoformat(), status="pendente")
    visto = {}
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: visto.update(kw) or {"enviado": True,
                                                    "via": "botoes",
                                                    "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()
    assert visto.get("botoes") == ["Paguei", "Adiar", "Ver tudo"], visto


def test_kind_sem_botao_definido_nao_quebra(usuario, monkeypatch):
    """Kind novo sem entrada na tabela manda texto normal, nao explode."""
    assert wa_bot.BOTOES_POR_KIND.get("kind-que-nao-existe") is None
    res = wa_bot._botoes_do_disparo({"kind": "kind-que-nao-existe"})
    assert res is None


# ---------------------------------------------------------------------------
# botoes DECLARADOS NOS TEMPLATES (valem fora da janela)
# ---------------------------------------------------------------------------

def test_botao_de_template_tambem_e_comando_conhecido():
    """Fora da janela o botao vem do template — mesma regra vale.

    O clique num botao de template chega como `type: button` e o webhook
    converte pro texto do botao. Titulo que o parser nao reconhece = pessoa
    clica e leva "nao entendi", com o agravante de estar fora da janela.
    """
    import templates as _cat
    for nome, t in _cat.CATALOGO.items():
        for b in (t.botoes or []):
            assert wa_bot.entende_comando(b), (nome, b)


def test_template_respeita_limites_da_meta():
    import templates as _cat
    for nome, t in _cat.CATALOGO.items():
        if t.botoes:
            meta_cloud._validar_botoes(t.botoes)   # levanta se estiver fora


def test_todo_kind_mapeado_existe_no_catalogo():
    import templates as _cat
    for kind, nome in _cat.KIND_TEMPLATE.items():
        assert nome in _cat.CATALOGO, (kind, nome)


# ---------------------------------------------------------------------------
# dar mais dias avisa a pessoa
# ---------------------------------------------------------------------------

def test_estender_trial_avisa_o_cliente_com_o_novo_prazo(usuario, monkeypatch):
    """O Kevin: "toda vez que eu der mais dias, solte a mensagem do novo
    trial e o prazo". Silencio aqui e um presente que ninguem fica sabendo.
    """
    from fastapi.testclient import TestClient
    import db
    visto = {}
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: visto.update(kw, texto=txt) or
        {"enviado": True, "via": "template", "motivo": ""})
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    c = TestClient(wa_bot.app)
    r = c.post("/painel/acao?k=tok",
               json={"user_id": usuario["id"], "acao": "estender", "dias": 5})
    assert r.json().get("ok"), r.text
    assert visto.get("template") == "resolveai_trial_estendido", visto
    assert visto["variaveis"][1] == "5", visto["variaveis"]


def test_se_o_aviso_falhar_os_dias_continuam_dados(usuario, monkeypatch):
    """O credito e do cliente, nao da mensagem.

    Se o envio falhasse e a extensao fosse desfeita junto, o Kevin clicaria
    de novo — e daria dias em dobro sem saber. Os dias valem; o aviso e
    melhor esforco e a falha aparece na resposta.
    """
    from fastapi.testclient import TestClient
    import db
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": False, "via": None,
                                         "motivo": "fora_da_janela"})
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    antes = db.trial_days_left(db.get_user(usuario["id"]))
    c = TestClient(wa_bot.app)
    j = c.post("/painel/acao?k=tok",
               json={"user_id": usuario["id"], "acao": "estender",
                     "dias": 9}).json()
    depois = db.trial_days_left(db.get_user(usuario["id"]))
    assert depois - antes == 9, (antes, depois)
    assert j.get("ok"), j
    assert j.get("aviso"), "a falha do aviso tem que aparecer pro dono"


# ---------------------------------------------------------------------------
# a tela nao pode nascer quebrada
# ---------------------------------------------------------------------------

def test_nenhuma_string_js_atravessa_linha(monkeypatch):
    """String JS com quebra de linha literal = SyntaxError = TELA BRANCA.

    Aconteceu de verdade em 28/08: escrevi '...tudo.\n' dentro do HTML, que
    e uma string Python NORMAL — o \n virou quebra de linha de verdade, o
    JS ficou com uma string aberta atravessando a linha, e o painel INTEIRO
    parou de renderizar. Sem erro no servidor, sem log: so a tela vazia.

    A varredura e boba de proposito: conta aspas nao escapadas por linha
    dentro do <script>. Impar = string aberta atravessando linha.
    """
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    html = TestClient(wa_bot.app).get("/dash?k=tok").text
    corpo = html.split("<script>")[-1].split("</script>")[0]
    ruins = []
    for n, linha in enumerate(corpo.split("\n"), 1):
        sem_comentario = linha.split("//")[0] if "//" in linha else linha
        # aspas simples NAO escapadas
        aspas = sum(1 for i, ch in enumerate(sem_comentario)
                    if ch == "'" and (i == 0 or sem_comentario[i - 1] != "\\"))
        if aspas % 2:
            ruins.append((n, linha.strip()[:70]))
    assert not ruins, (
        "string JS aberta atravessando linha (tela branca garantida): %s"
        % ruins)


def test_o_dash_renderiza_os_cards_novos(monkeypatch):
    """Os blocos que o Kevin pediu tem que estar no HTML servido."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    html = TestClient(wa_bot.app).get("/dash?k=tok").text
    for marca in ("Mandar pra uma lista", "function lote(", "function zerar(",
                  "segLote", "tplLote", "já automático"):
        assert marca in html, "sumiu da tela: %r" % marca


# ---------------------------------------------------------------------------
# avisar quem ganhou dias (reset ou extensao)
# ---------------------------------------------------------------------------

def test_trial_estendido_e_ofertado_no_painel():
    """Sem isso o botao de lote nao consegue avisar quem teve o trial
    resetado — que e exatamente pra isso que o template foi criado."""
    nomes = [t["nome"] for t in wa_bot._templates_com_rotulo()]
    assert "resolveai_trial_estendido" in nomes, nomes


def test_preenche_dias_extras_e_nova_data(usuario):
    """As duas variaveis que faltavam."""
    import datetime as dt
    import db
    import tempo
    ok, motivo = wa_bot._variaveis_do_template(
        "resolveai_trial_estendido", db.get_user(usuario["id"]))
    assert ok, motivo
    nome, dias, data = motivo
    assert nome
    assert dias.isdigit() and int(dias) > 0, dias
    # a data tem que bater com os dias que a pessoa ainda tem
    esperada = (tempo.hoje() + dt.timedelta(days=int(dias))).strftime("%d/%m/%Y")
    assert data == esperada, (data, esperada)


def test_nao_promete_dias_a_quem_nao_tem(usuario):
    """Trial vencido: o template diria "liberei mais 0 dias", que e piada."""
    import db
    import tempo
    import datetime as dt
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=? WHERE id=?",
                  ((tempo.agora() - dt.timedelta(days=40)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    ok, motivo = wa_bot._variaveis_do_template(
        "resolveai_trial_estendido", db.get_user(usuario["id"]))
    assert not ok, "prometeu dias pra quem tem trial vencido: %r" % (motivo,)
