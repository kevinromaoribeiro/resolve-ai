# -*- coding: utf-8 -*-
"""A jornada de 14 dias tem que CHEGAR, e nao so existir.

O defeito, que durou desde que a regua foi escrita: nenhum `trial_d*` tinha
template da Meta. Fora da janela de 24h so sai template aprovado — entao a
jornada inteira so alcancava quem tinha falado com o bot no ultimo dia.

E quem falou com o bot no ultimo dia e justamente quem NAO precisa dela. O
publico da regua e quem esfriou. A jornada existia, rodava, gerava as
mensagens certas, e elas eram descartadas na poda sem erro e sem log.

Cada teste aqui trava uma metade: a mensagem tem template, e o template vai
preenchido com a licao daquele dia.
"""
import pytest

import scheduler
import templates
import trial_guiado


DIAS = [e["dia"] for e in trial_guiado._ETAPAS]


def _pessoa(interesses="contas,pet"):
    return {"id": 1, "nome": "Ana Paula", "telefone": "5511988887777",
            "interesses": interesses}


def _disparo(etapa, user=None):
    u = user or _pessoa()
    return trial_guiado._mk(
        u, "trial_d%d" % etapa["dia"], etapa["rico"](u),
        nudge=etapa["nudge"], capacidade=etapa["capacidade"],
        faz=etapa["faz"](u))


# --- a metade que faltava: sair da janela ------------------------------

@pytest.mark.parametrize("etapa", trial_guiado._ETAPAS,
                         ids=[e["nudge"] for e in trial_guiado._ETAPAS])
def test_cada_etapa_tem_template_pra_sair_fora_da_janela(etapa, usuario):
    nome, variaveis = templates.para_disparo(_disparo(etapa))
    assert nome, ("a etapa %s nao tem template: fora da janela ela some "
                  "calada, que e o defeito inteiro" % etapa["nudge"])
    assert len(variaveis) == 3
    assert all(str(v).strip() for v in variaveis), variaveis


def test_nenhum_kind_da_jornada_ficou_sem_template():
    for e in trial_guiado._ETAPAS:
        kind = "trial_d%d" % e["dia"]
        assert kind not in templates.KINDS_SEM_TEMPLATE, kind
        assert kind in templates.KIND_TEMPLATE, kind


def test_o_fechamento_tambem_sai_fora_da_janela():
    """E a unica mensagem que pede dinheiro. Ficar presa seria o pior caso."""
    assert templates.KIND_TEMPLATE["trial_d6"] == "resolveai_fim_de_trial_aviso"


def test_a_lista_de_kinds_do_motor_bate_com_a_regua():
    """Kind emitido e nao declarado some da poda sem ninguem ver."""
    for e in trial_guiado._ETAPAS:
        assert "trial_d%d" % e["dia"] in scheduler.KINDS_PROATIVOS


def test_nao_sobrou_kind_de_trial_declarado_e_nao_usado():
    """A regua encolheu de doze pra seis. Kind orfao faz a lista envelhecer."""
    vivos = {"trial_d%d" % e["dia"] for e in trial_guiado._ETAPAS} | {"trial_d6"}
    declarados = {k for k in scheduler.KINDS_PROATIVOS
                  if k.startswith("trial_d")}
    assert declarados == vivos, declarados - vivos


# --- o conteudo do template -------------------------------------------

def test_a_licao_vai_dentro_do_template(usuario):
    """O corpo do `novidade` e casca: o que ensina sao as variaveis."""
    etapa = next(e for e in trial_guiado._ETAPAS if e["nudge"] == "d3")
    _nome, v = templates.para_disparo(_disparo(etapa))
    assert v[1] == "foto de boleto"
    assert "codigo de barras" in v[2]


def test_sem_a_licao_o_template_nao_sai(usuario):
    """Variavel vazia e recusa da Meta — e a etapa queimaria sem ter saido."""
    d = _disparo(trial_guiado._ETAPAS[0])
    d["o_que_ela_faz"] = ""
    assert templates.para_disparo(d) == (None, [])


def test_a_licao_nao_leva_quebra_de_linha(usuario):
    """A Meta recusa variavel com quebra, e a recusa chega so no envio.

    A quebra e INJETADA aqui de proposito: nenhuma licao de hoje tem uma,
    entao afirmar sobre os textos atuais nao media nada — o teste passava
    igual com a normalizacao removida. Quem precisa ser testado e o
    `para_disparo`, nao o texto que por acaso esta limpo.
    """
    d = _disparo(trial_guiado._ETAPAS[0])
    d["o_que_ela_faz"] = "primeira linha\nsegunda linha"
    d["nome_da_novidade"] = "com\nquebra"
    _nome, v = templates.para_disparo(d)
    assert v, "a etapa nao saiu"
    for parte in v:
        assert "\n" not in str(parte), parte
    assert v[2] == "primeira linha segunda linha"


def test_as_licoes_de_hoje_estao_limpas(usuario):
    for e in trial_guiado._ETAPAS:
        _nome, v = templates.para_disparo(_disparo(e))
        for parte in v:
            assert "\n" not in str(parte), (e["nudge"], parte)


def test_a_licao_nao_leva_formatacao_de_whatsapp(usuario):
    """Asterisco dentro de variavel de template sai cru na tela da pessoa."""
    for e in trial_guiado._ETAPAS:
        _nome, v = templates.para_disparo(_disparo(e))
        assert "*" not in v[2], (e["nudge"], v[2])
        assert "_" not in v[2], (e["nudge"], v[2])


# --- personalizacao pelo cadastro -------------------------------------

def test_a_licao_muda_com_o_interesse_do_cadastro(usuario):
    """Falar de boleto pra quem marcou "pet" e ruido."""
    etapa = next(e for e in trial_guiado._ETAPAS if e["nudge"] == "d1")
    so_pet = etapa["faz"](_pessoa("pet"))
    so_contas = etapa["faz"](_pessoa("contas"))
    assert so_pet != so_contas
    assert "vacina" in so_pet.lower()


def test_sem_interesse_nenhum_ainda_sai_alguma_coisa(usuario):
    """Cadastro sem interesse e comum, e nao pode calar a jornada."""
    for e in trial_guiado._ETAPAS:
        assert e["faz"](_pessoa("")).strip()
        assert e["rico"](_pessoa("")).strip()


# --- ritmo -------------------------------------------------------------

def test_a_regua_e_dia_sim_dia_nao():
    """Cinco mensagens em cinco dias e o padrao que a Meta pune."""
    assert DIAS == sorted(DIAS)
    for anterior, seguinte in zip(DIAS, DIAS[1:]):
        assert seguinte - anterior >= 2, (anterior, seguinte)


def test_a_jornada_cabe_no_trial():
    assert max(DIAS) < trial_guiado.DIA_FECHAMENTO


def test_seis_licoes_mais_o_fechamento():
    assert len(trial_guiado._ETAPAS) == 6


def test_a_licao_cabe_no_limite_da_variavel():
    """`para_disparo` corta variavel em 200 caracteres.

    Licao cortada no meio da frase chega truncada pra pessoa, e o corte
    acontece no envio — depois de a etapa ja ter sido gasta pelo dedup.
    """
    for e in trial_guiado._ETAPAS:
        for interesses in ("", "contas", "pet", "carro,saude,datas"):
            faz = e["faz"](_pessoa(interesses))
            assert len(faz) <= 200, (e["nudge"], interesses, len(faz), faz)


def test_a_cobertura_nao_conta_aviso_como_licao():
    """`LIKE 'trial%'` pegava `trial-ending` e `trial-estendido`.

    O painel diria que a jornada chegou quando o que chegou foi o aviso de
    fim de teste — e a decisao de resetar ou nao sai desse numero.
    """
    import db
    uid = db.create_user(nome="Cobertura", telefone="5511977009911")
    quando = "2026-09-05T10:00:00"
    with db.get_conn() as c:
        for k in ("trial_d1", "trial_d11", "trial-ending", "trial-estendido"):
            c.execute("INSERT INTO dispatches (user_id,item_id,kind,sent_at) "
                      "VALUES (?,?,?,?)", (uid, None, k, quando))
    try:
        p = [x for x in db.cobertura_da_jornada()["pessoas"]
             if x["user_id"] == uid]
        assert p, "a pessoa sumiu da cobertura"
        assert p[0]["quais"] == ["trial_d1", "trial_d11"], p[0]["quais"]
    finally:
        with db.get_conn() as c:
            c.execute("DELETE FROM dispatches WHERE sent_at=?", (quando,))
            c.execute("DELETE FROM users WHERE id=?", (uid,))


def test_a_cobertura_chega_na_tela(monkeypatch):
    """O card existia e nunca renderizava: o dado nao ia no payload.

    Ele le `d.jornada`, caia no fallback vazio e o `if(pessoas.length)`
    nunca era verdade — painel publicado e desligado, sem responder a
    pergunta que originou tudo.
    """
    import wa_bot
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    dados = TestClient(wa_bot.app).get("/api/pulso?k=tok").json()
    assert "jornada" in dados, sorted(dados)
    assert "pessoas" in dados["jornada"]
    assert dados["jornada"]["etapas_possiveis"] == 7


def test_o_total_de_etapas_bate_com_a_regua():
    """Era 12, do desenho antigo. Quem recebesse tudo aparecia como 7/12."""
    import db
    assert db.cobertura_da_jornada()["etapas_possiveis"] == (
        len(trial_guiado._ETAPAS) + 1)


def test_nao_sobrou_licao_escrita_e_nao_usada():
    """Funcao de licao fora da tabela e trabalho que ninguem le."""
    usadas = {e["faz"] for e in trial_guiado._ETAPAS}
    escritas = {v for k, v in vars(trial_guiado).items()
                if k.startswith("_faz_") and callable(v)}
    assert escritas == usadas, escritas - usadas


# --- a prova de ponta a ponta -----------------------------------------
#
# Os testes acima chamam `para_disparo` direto. Isso prova que a licao TEM
# template, nao que ela SAI. O caminho real passa pela poda do
# `dispatch_proactive`, que era exatamente onde ela morria — sem erro e sem
# log. Faltando este teste, a suite ficava verde com a jornada muda.

def test_a_licao_sai_de_verdade_com_a_janela_FECHADA(usuario, monkeypatch):
    import datetime as _dt

    import db
    import tempo
    import wa_bot

    # dia 3 da regua e calada ha muito: o caso que a jornada existe pra
    # resolver, e o unico em que o defeito aparecia.
    base = tempo.agora() - _dt.timedelta(days=3)
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=?, ultima_interacao=? "
                  "WHERE id=?",
                  (base.strftime("%Y-%m-%d %H:%M:%S"),
                   (tempo.agora() - _dt.timedelta(hours=40)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))

    # A JANELA FECHADA E O PONTO. Com ela aberta o teste passaria mesmo com
    # o defeito de volta, porque texto livre sai sem template.
    monkeypatch.setattr(db, "dentro_da_janela", lambda **kw: False)

    # A REATIVACAO JA FOI, como na producao (os 14 receberam em 28/08).
    # Sem isto ela ocupa o disparo do ciclo e o teste mede o disparo
    # errado — foi o que aconteceu na primeira rodada deste teste.
    db.log_dispatch(usuario["id"], "reativacao")

    saiu = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: saiu.append(kw.get("template"))
        or {"enviado": True, "via": "template", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)

    wa_bot.dispatch_proactive()
    assert saiu, ("nada saiu com a janela fechada — e o defeito inteiro: a "
                  "jornada era gerada e descartada na poda")
    assert "resolveai_novidade" in saiu, saiu


def test_todo_card_do_painel_esta_mapeado_numa_aba():
    """Card fora do mapa cai em "Negocio" e o dono procura na aba errada.

    Aconteceu com o card da jornada: ele renderizava, mas em Negocio, e eu
    concluí que "nao renderizou" porque procurei em Clientes.
    """
    import re
    import wa_bot
    fonte = wa_bot.__file__
    with open(fonte, encoding="utf-8") as f:
        codigo = f.read()
    js = codigo.split("const ABA_DO_CARD={")[1]
    mapa = set(re.findall(r"^\s*'([^']+)':'[a-z]+',?$", js.split("};")[0],
                          re.M))
    chamadas = set(re.findall(r"card\('([^']+)'", codigo))
    # 'Erro' e o card de token invalido: ele nao passa por aba nenhuma.
    fora = {c for c in chamadas if c not in mapa} - {"Erro"}
    assert not fora, "cards sem aba declarada: %s" % sorted(fora)
