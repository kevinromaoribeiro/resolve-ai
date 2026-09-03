# -*- coding: utf-8 -*-
"""O aviso de novidade precisa de um botao que o dispare.

O `resolveai_novidade` ficou num limbo: aprovado na Meta, e invisivel no
painel. `_templates_manuais` so oferecia o que sabe preencher sozinho, e o
nome da novidade e a explicacao sao texto que so o dono sabe — nenhum dado
do banco responde "o que voce lancou hoje".

Agora o painel pede esses dois textos na hora do envio. O que estes testes
guardam e o RISCO desse campo livre: ele vai pra base inteira de uma vez.
"""
import wa_bot


def _u(usuario):
    import db
    return db.get_user(usuario["id"])


# --- o botao existe ---------------------------------------------------

def test_novidade_aparece_na_lista_do_painel():
    nomes = [t["nome"] for t in wa_bot._templates_com_rotulo()]
    assert "resolveai_novidade" in nomes


def test_so_a_novidade_pede_texto():
    """Campo livre em template que nao precisa dele seria porta aberta."""
    pedem = {t["nome"] for t in wa_bot._templates_com_rotulo()
             if t["pede_texto"]}
    assert pedem == {"resolveai_novidade"}


def test_o_painel_diz_quais_textos_pede():
    t = next(t for t in wa_bot._templates_com_rotulo()
             if t["nome"] == "resolveai_novidade")
    assert t["pede_texto"] == ["nome_da_novidade", "o_que_ela_faz"]


# --- vazio e RECUSA, nao string vazia ---------------------------------

def test_sem_texto_nao_monta_a_mensagem(usuario):
    """"novidade no Resolve AI: **." pra base inteira e pior que nao mandar."""
    ok, motivo = wa_bot._variaveis_do_template(
        "resolveai_novidade", _u(usuario), {})
    assert ok is False
    assert "falta" in motivo.lower()


def test_texto_so_de_espaco_tambem_e_recusa(usuario):
    ok, _m = wa_bot._variaveis_do_template(
        "resolveai_novidade", _u(usuario),
        {"nome_da_novidade": "   ", "o_que_ela_faz": "algo"})
    assert ok is False


def test_extras_ausente_nao_estoura(usuario):
    """Quem chama sem `extras` (todo o resto do sistema) segue funcionando."""
    ok, _m = wa_bot._variaveis_do_template("resolveai_novidade", _u(usuario))
    assert ok is False


# --- o que entra na mensagem ------------------------------------------

def test_com_os_dois_textos_a_mensagem_fecha(usuario):
    ok, vals = wa_bot._variaveis_do_template(
        "resolveai_novidade", _u(usuario),
        {"nome_da_novidade": "mini podcast em áudio",
         "o_que_ela_faz": "Um áudio curto com as notícias dos temas que "
                          "você escolher."})
    assert ok is True
    assert len(vals) == 3
    assert vals[1] == "mini podcast em áudio"


def test_quebra_de_linha_vira_espaco(usuario):
    """A Meta recusa variavel com \\n, e a recusa viria com o lote em curso."""
    ok, vals = wa_bot._variaveis_do_template(
        "resolveai_novidade", _u(usuario),
        {"nome_da_novidade": "podcast", "o_que_ela_faz": "linha um\nlinha dois"})
    assert ok is True
    assert "\n" not in vals[2]
    assert vals[2] == "linha um linha dois"


def test_texto_gigante_e_barrado(usuario):
    ok, motivo = wa_bot._variaveis_do_template(
        "resolveai_novidade", _u(usuario),
        {"nome_da_novidade": "podcast",
         "o_que_ela_faz": "x" * (wa_bot.LIMITE_VARIAVEL_LIVRE + 1)})
    assert ok is False
    assert str(wa_bot.LIMITE_VARIAVEL_LIVRE) in motivo


def test_no_limite_ainda_passa(usuario):
    ok, _v = wa_bot._variaveis_do_template(
        "resolveai_novidade", _u(usuario),
        {"nome_da_novidade": "podcast",
         "o_que_ela_faz": "x" * wa_bot.LIMITE_VARIAVEL_LIVRE})
    assert ok is True


# --- o campo livre nao vira porta -------------------------------------

def test_extra_desconhecido_nao_preenche_variavel(usuario):
    """So as variaveis LIVRES leem `extras`. O resto vem do banco.

    PLACEBO CORRIGIDO: sem item pendente o template era recusado e o
    `if ok:` fazia o teste passar sem medir nada. Agora a pessoa tem item,
    o preenchimento acontece de verdade, e o texto de fora tem que ficar
    de fora.
    """
    import db
    import tempo
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="casa",
                descricao="conta de luz", valor_reais=10.0,
                data_vencimento=tempo.hoje().strftime("%Y-%m-%d"),
                status="pendente")
    ok, vals = wa_bot._variaveis_do_template(
        "resolveai_lembrete_hora", _u(usuario),
        {"primeiro_nome": "INVASOR", "hora": "23:59", "descricao": "INVASOR"})
    assert ok is True, vals
    assert "INVASOR" not in vals
    assert "23:59" not in vals


def test_variaveis_livres_sao_so_as_duas():
    assert wa_bot.VARIAVEIS_LIVRES == {"nome_da_novidade", "o_que_ela_faz"}
    assert not (wa_bot.VARIAVEIS_LIVRES
                & wa_bot.VARIAVEIS_QUE_SEI_PREENCHER)


# --- a rota recusa ANTES de o lote comecar ----------------------------

def _cliente(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    return TestClient(wa_bot.app)


def test_lote_sem_os_textos_nao_manda_pra_ninguem(usuario, monkeypatch):
    """A validacao mora ANTES do laco de propósito.

    O lote espaca os envios por minutos. Se a recusa acontecesse dentro do
    laco, metade da base receberia e metade nao, com o disparo em curso e
    ninguem entendendo por que.
    """
    chamou = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: chamou.append(1) or {"enviado": True})
    c = _cliente(monkeypatch)
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "todos", "template": "resolveai_novidade",
                     "confirmo": True})
    j = r.json()
    assert not j.get("ok"), j
    assert "falta" in (j.get("erro") or "").lower()
    assert not chamou, "mandou mensagem sem o texto da novidade"


def test_lote_com_texto_gigante_recusa_antes_de_enviar(usuario, monkeypatch):
    chamou = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: chamou.append(1) or {"enviado": True})
    c = _cliente(monkeypatch)
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "todos", "template": "resolveai_novidade",
                     "confirmo": True,
                     "extras": {"nome_da_novidade": "podcast",
                                "o_que_ela_faz": "x" * 5000}})
    assert not r.json().get("ok")
    assert not chamou


def test_lote_ignora_extra_que_nao_e_livre(usuario, monkeypatch):
    """`extras` e campo do painel, nao porta pra qualquer variavel."""
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True})
    c = _cliente(monkeypatch)
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "todos", "template": "resolveai_novidade",
                     "confirmo": True,
                     "extras": {"primeiro_nome": "INVASOR",
                                "nome_da_novidade": "podcast",
                                "o_que_ela_faz": "áudio com as notícias"}})
    assert r.json().get("ok"), r.text


def test_extras_invalido_nao_estoura_a_rota(usuario, monkeypatch):
    c = _cliente(monkeypatch)
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "todos", "template": "resolveai_novidade",
                     "confirmo": True, "extras": "sou uma string"})
    assert not r.json().get("ok")
