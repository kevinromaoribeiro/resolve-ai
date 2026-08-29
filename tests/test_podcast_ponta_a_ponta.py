# -*- coding: utf-8 -*-
"""O MINI-PODCAST DE PONTA A PONTA: landing -> convite -> audio -> dia.

O que este arquivo cobra e o CAMINHO, nao as pecas — `test_podcast.py` ja
cobre roteiro, fontes e teto. Aqui a pergunta e outra: a escolha da landing
chega no banco? O convite sai com botao? O toque gera audio DE VERDADE pelo
`canal` (que e quem respeita a janela de 24h)? A recusa desliga?

NENHUM TESTE TOCA A REDE nem chama modelo pago: o download de RSS, o LLM e a
sintese de voz sao todos injetaveis, e e o caminho de producao que roda.
"""
import datetime as _dt

import pytest

import canal
import db
import noticias
import podcast
import scheduler
import tempo
import voz
import wa_bot
from conftest import TELEFONE, responder

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Palmeiras vence o Flamengo por 2 a 1</title>
    <description>Gols de Estevao e Rony no Allianz.</description>
    <link>https://ge.globo.com/x</link>
    <pubDate>{ontem}</pubDate>
  </item>
  <item>
    <title>Corinthians anuncia meia argentino</title>
    <description>Chega por emprestimo de um ano.</description>
    <link>https://ge.globo.com/y</link>
    <pubDate>{ontem}</pubDate>
  </item>
</channel></rss>"""


def _feed_falso(agora=None):
    ref = (agora or tempo.agora()) - _dt.timedelta(days=1)
    xml = FEED.format(ontem=ref.strftime("%a, %d %b %Y %H:%M:%S +0000"))
    return lambda url: xml


@pytest.fixture
def com_voz(monkeypatch):
    """Provedor de voz configurado e sintese que devolve bytes de mentira."""
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(voz, "sintetizar", lambda t: b"OggS-audio-falso")
    return True


@pytest.fixture
def com_noticia(monkeypatch):
    monkeypatch.setattr(noticias, "_baixar", _feed_falso())
    # o LLM nao e chamado em teste: cai no roteiro deterministico
    monkeypatch.setattr(podcast, "_chamar_llm",
                        lambda p: (_ for _ in ()).throw(RuntimeError("sem LLM")))
    return True


# ---------------------------------------------------------------------------
# 1. a escolha da landing chega no banco
# ---------------------------------------------------------------------------

def test_o_nicho_da_landing_e_guardado_na_primeira_mensagem(usuario):
    """A landing monta "(e o resumo semanal de Futebol)" no link do WhatsApp.

    Perguntar de novo no chat seria o bot mostrando que nao prestou atencao
    no clique que a pessoa acabou de dar.
    """
    responder("Oi! Quero testar o Resolve AI (e o resumo semanal de Futebol)")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "futebol"


def test_nicho_que_nao_existe_nao_e_guardado(usuario):
    responder("me manda o resumo semanal de criptomoeda")
    assert not db.get_user(usuario["id"])["podcast_nicho"]


def test_a_captura_nao_engole_a_mensagem(usuario, monkeypatch):
    """A pessoa tambem esta se apresentando: o resto do fluxo tem que rodar."""
    chegou = {}

    def _viu(*a, **k):
        chegou["sim"] = True
        return None

    monkeypatch.setattr(wa_bot.motor_v8, "route", _viu, raising=False)
    responder("Oi! Quero testar (e o resumo semanal de Games)")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "games"
    assert chegou.get("sim"), "a mensagem parou na captura do nicho"


# ---------------------------------------------------------------------------
# 2. o convite: 6h depois, uma vez, com botao
# ---------------------------------------------------------------------------

def _com_nicho(usuario, nicho="futebol", horas_atras=7):
    db.update_user_fields(usuario["id"], podcast_nicho=nicho)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=horas_atras)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


def test_o_convite_sai_depois_das_seis_horas(usuario, horario_util, com_voz):
    _com_nicho(usuario, horas_atras=7)
    d = scheduler.check_podcast()
    assert len(d) == 1, d
    assert d[0]["kind"] == "podcast"
    assert "?" in d[0]["message"]
    assert d[0]["botoes"] == ["Quero ouvir", "Agora não", "Não quero mais"]


def test_antes_das_seis_horas_nao_convida(usuario, horario_util, com_voz):
    _com_nicho(usuario, horas_atras=2)
    assert not scheduler.check_podcast()


def test_sem_voz_configurada_o_bot_nem_convida(usuario, horario_util,
                                               monkeypatch):
    """Perguntar "quer ouvir?" sem ter como gerar o audio e prometer o que
    nao da pra entregar — a pessoa toca no botao e nao recebe nada."""
    _com_nicho(usuario)
    monkeypatch.setattr(voz, "disponivel", lambda: False)
    assert not scheduler.check_podcast()


def test_quem_ouviu_essa_semana_nao_e_convidado_de_novo(usuario, horario_util,
                                                        com_voz):
    """Teto DURO de 1x por semana. Audio e a mensagem mais intrusiva que
    existe no WhatsApp, e este numero ja foi restringido duas vezes."""
    _com_nicho(usuario)
    db.podcast_marcar_envio(usuario["id"])
    assert not scheduler.check_podcast()


def test_o_convite_do_disparo_leva_os_botoes_ate_o_envio(usuario,
                                                         horario_util,
                                                         com_voz):
    """Sem isto a pessoa teria que DIGITAR "quero ouvir" — o oposto do
    pedido do dono ("BOTOES pra facilitar a vida")."""
    _com_nicho(usuario)
    d = scheduler.check_podcast()[0]
    assert wa_bot._botoes_do_disparo(d) == ["Quero ouvir", "Agora não",
                                            "Não quero mais"]


# ---------------------------------------------------------------------------
# 3. o toque no botao: o audio sai pelo canal
# ---------------------------------------------------------------------------

def test_quero_ouvir_manda_audio_de_verdade(usuario, com_voz, com_noticia,
                                            monkeypatch):
    """O audio tem que sair pelo `canal.falar_audio`, que e quem respeita a
    janela de 24h. Caminho novo que chame o envio por fora e como a gente
    reabre um buraco ja fechado."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    enviados = {}

    def _falar_audio(tel, dados, **kw):
        enviados["bytes"] = dados
        enviados["tel"] = tel
        return {"enviado": True, "via": "audio", "motivo": ""}

    monkeypatch.setattr(wa_bot.wasender, "falar_audio", _falar_audio,
                        raising=False)
    r = responder("Quero ouvir")

    assert enviados.get("bytes"), "nao mandou audio nenhum"
    assert "futebol" in r.lower(), r
    assert "ge.globo" in r, "nao citou a fonte na mensagem de fecho"
    assert db.get_user(usuario["id"])["podcast_ultimo"], (
        "nao carimbou o envio — o teto semanal nao seguraria")


def test_semana_sem_noticia_nao_manda_audio(usuario, com_voz, monkeypatch):
    """Episodio fabricado pra cumprir tabela e como se perde a confianca de
    alguem de uma vez so."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    monkeypatch.setattr(noticias, "_baixar", lambda url: "<rss></rss>")
    mandou = {}
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda *a, **k: mandou.setdefault("sim", True) or
                        {"enviado": True}, raising=False)
    r = responder("Quero ouvir")
    assert not mandou, "mandou audio sem ter noticia"
    assert "não achei novidade" in r.lower(), r
    assert not db.get_user(usuario["id"])["podcast_ultimo"]


def test_se_a_voz_falhar_nao_manda_audio_quebrado(usuario, com_noticia,
                                                  monkeypatch):
    """Audio que a pessoa toca e nao sai som ensina que o produto nao
    funciona — pior que nao mandar."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(voz, "sintetizar", lambda t: None)
    mandou = {}
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda *a, **k: mandou.setdefault("sim", True) or
                        {"enviado": True}, raising=False)
    r = responder("Quero ouvir")
    assert not mandou
    assert "não consegui gerar" in r.lower(), r


def test_sem_nicho_escolhido_o_bot_explica(usuario, com_voz):
    r = responder("Quero ouvir")
    assert "assunto" in r.lower(), r


# ---------------------------------------------------------------------------
# 4. as saidas
# ---------------------------------------------------------------------------

def test_agora_nao_adia_sem_desligar(usuario, com_voz):
    db.update_user_fields(usuario["id"], podcast_nicho="games")
    r = responder("Agora não")
    assert "próxima" in r.lower(), r
    u = db.get_user(usuario["id"])
    assert u["podcast_nicho"] == "games", "desligou quem so pediu pra esperar"
    assert not u["podcast_ultimo"], "carimbou envio que nao aconteceu"


def test_nao_quero_mais_desliga_de_verdade(usuario):
    """Sem uma saida facil, a unica saida da pessoa e bloquear o numero — e
    bloqueio conta contra a qualidade na Meta."""
    db.update_user_fields(usuario["id"], podcast_nicho="moda",
                          podcast_dia="Sexta")
    r = responder("Não quero mais")
    u = db.get_user(usuario["id"])
    assert not u["podcast_nicho"] and not u["podcast_dia"], u
    assert "lembretes continuam" in r.lower(), r


def test_desligar_o_podcast_nao_mexe_nos_lembretes(usuario):
    db.update_user_fields(usuario["id"], podcast_nicho="moda")
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Outros",
                descricao="dentista", data_vencimento="2026-12-01",
                status="pendente")
    responder("Não quero mais")
    assert len(db.list_items(usuario["id"], status="pendente")) == 1


# ---------------------------------------------------------------------------
# 5. UMA VEZ POR SEMANA, PRA TODO MUNDO — no primeiro dia em que ela aparecer
# ---------------------------------------------------------------------------
# Decisao do Kevin (29/08/2026): "1x por semana pode ser, o importante e todo
# cliente ter" + "nao pode deixar de mandar". As duas juntas tornam o dia fixo
# impossivel de cumprir: o convite so sai DENTRO da janela de 24h, entao quem
# nao mandasse mensagem naquela sexta perdia a semana em silencio.
#
# A pergunta do dia saiu junto. Perguntar um dia que a gente nao honra e pior
# que nao perguntar.

def test_o_episodio_semanal_alcanca_quem_ja_ouviu(usuario, horario_util,
                                                  com_voz):
    """Sem exigir dia escolhido: quem tem nicho e ja ouviu entra."""
    _com_nicho(usuario)
    db.podcast_marcar_envio(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
    db.podcast_marcar_convite(usuario["id"])
    d = scheduler.check_podcast()
    assert len(d) == 1, ("quem ja ouviu ficou de fora: %r" % d)


@pytest.mark.parametrize("dia", [0, 1, 2, 3, 4, 5, 6])
def test_qualquer_dia_da_semana_serve(usuario, com_voz, monkeypatch, dia):
    """O dia fixo era o que fazia a pessoa perder a semana inteira."""
    base = _dt.datetime(2026, 8, 17, 10, 0, 0) + _dt.timedelta(days=dia)
    monkeypatch.setattr(tempo, "agora", lambda: base)
    monkeypatch.setattr(tempo, "hoje", lambda: base.date())
    _com_nicho(usuario)
    db.podcast_marcar_envio(
        usuario["id"], quando=base - _dt.timedelta(days=8))
    db.podcast_marcar_convite(usuario["id"])
    assert scheduler.check_podcast(), ("nao alcancou no dia %d" % dia)


def test_o_teto_de_uma_por_semana_continua_valendo(usuario, horario_util,
                                                   com_voz):
    """"Mais alcance" nao pode virar "mais ruido": e o teto que segura."""
    _com_nicho(usuario)
    db.podcast_marcar_convite(usuario["id"])
    for dias, esperado in ((8, True), (6, False), (1, False)):
        db.podcast_marcar_envio(
            usuario["id"], quando=tempo.agora() - _dt.timedelta(days=dias))
        assert bool(scheduler.check_podcast()) is esperado, dias


def test_quem_nunca_ouviu_nao_entra_pelo_caminho_semanal(usuario,
                                                         horario_util,
                                                         com_voz):
    """O caminho semanal e pra quem ja ouviu; quem nunca ouviu tem o convite
    de primeira vez, que respeita as 6h do cadastro."""
    _com_nicho(usuario, horas_atras=2)
    assert not db.podcast_assinantes()
    assert not scheduler.check_podcast()


def test_o_fecho_do_episodio_diz_a_frequencia_e_a_saida(usuario, com_voz,
                                                        com_noticia,
                                                        monkeypatch):
    """A pergunta do dia saiu; o combinado vai no fecho, sem gastar mensagem
    nova. E a saida tem que estar visivel: sem ela, a unica saida da pessoa e
    bloquear o numero."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda *a, **k: {"enviado": True, "via": "audio",
                                         "motivo": ""}, raising=False)
    r = responder("Quero ouvir")
    assert "uma vez por semana" in r.lower(), r
    assert "não quero mais" in r.lower(), r
# ---------------------------------------------------------------------------
# 6. guardrails
# ---------------------------------------------------------------------------

def test_audio_nao_sai_fora_da_janela_de_24h(usuario, monkeypatch):
    """Audio nao e excecao a janela, e nao existe template de audio."""
    monkeypatch.setattr(db, "dentro_da_janela", lambda *a, **k: False)
    monkeypatch.setattr(canal, "send_audio", lambda *a, **k: True,
                        raising=False)
    r = canal.falar_audio(TELEFONE, b"xxx", user_id=usuario["id"])
    assert not r["enviado"] and r["motivo"] == "fora_da_janela", r


def test_canal_sem_audio_recusa_em_vez_de_estourar(usuario, monkeypatch):
    """No canal reserva `send_audio` e None: recusar e o certo, estourar
    AttributeError no meio de um ciclo nao."""
    monkeypatch.setattr(canal, "send_audio", None, raising=False)
    monkeypatch.setattr(db, "dentro_da_janela", lambda *a, **k: True)
    r = canal.falar_audio(TELEFONE, b"xxx", user_id=usuario["id"])
    assert not r["enviado"] and r["motivo"] == "canal_sem_audio", r


def test_o_convite_nao_tem_template_e_isso_e_deliberado():
    """Pedir pra alguem ouvir um audio e o motivo que a Meta classifica como
    marketing — e marketing neste numero e o que ja rendeu duas restricoes."""
    import templates as T
    assert "podcast" in T.KINDS_SEM_TEMPLATE
    assert "podcast" not in T.KIND_TEMPLATE
    assert "podcast" in scheduler.KINDS_PROATIVOS


# ---------------------------------------------------------------------------
# 7. O TESTE QUE FALTAVA: dois ciclos seguidos
# ---------------------------------------------------------------------------
# A auditoria M4.2 achou tres P0 e os tres passavam por baixo de 75 testes
# pelo mesmo motivo: NENHUM rodava o cron duas vezes. Um ciclo so nao ve
# repeticao, e repeticao era o defeito.

def _cron_pronto(monkeypatch, usuario=None):
    """Deixa o cron pronto pra rodar de verdade.

    A JANELA DE 24H PRECISA ESTAR ABERTA: "podcast" esta em
    `KINDS_SEM_TEMPLATE`, entao a poda do `dispatch_proactive` corta o
    convite de quem nao falou com o bot — e isso esta CERTO. Sem esta linha
    o teste mediria a poda, nao a repeticao.
    """
    import wa_bot as w
    if usuario is not None:
        db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    saiu: list = []

    def _falar(tel, texto, **kw):
        saiu.append(texto)
        return {"enviado": True, "via": "botoes", "motivo": ""}

    monkeypatch.setattr(w.wasender, "falar", _falar)
    monkeypatch.setattr(w, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(w, "ENVIO_INTERVALO_MAX", 0.0)
    return saiu


def test_o_convite_nao_se_repete_no_ciclo_seguinte(usuario, horario_util,
                                                   com_voz, monkeypatch,
                                                   limpo):
    """Sem carimbo, o convite voltava de MINUTO EM MINUTO ate estourar o
    teto diario — e o teto e compartilhado com o aviso de vencimento."""
    _com_nicho(usuario)
    saiu = _cron_pronto(monkeypatch, usuario)

    wa_bot.dispatch_proactive()
    primeiro = len([t for t in saiu if "podcast" in t.lower()])
    assert primeiro == 1, "o convite nem saiu"

    wa_bot.dispatch_proactive()
    wa_bot.dispatch_proactive()
    total = len([t for t in saiu if "podcast" in t.lower()])
    assert total == 1, ("o convite se repetiu a cada ciclo: %d vezes" % total)
    assert db.get_user(usuario["id"])["podcast_convite_em"], (
        "o envio nao carimbou nada — nada segura a repeticao")


def test_o_convite_nao_come_a_vaga_do_aviso_de_conta(usuario, horario_util,
                                                     com_voz, monkeypatch,
                                                     limpo):
    """O extra nao pode roubar o lugar do que a pessoa pagou pra ter.

    Provado pelo auditor: seis convites de podcast comiam o teto diario e o
    aviso do IPTU nao saia.
    """
    _com_nicho(usuario)
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="IPTU", valor_reais=900.0,
                data_vencimento=(tempo.hoje() + _dt.timedelta(days=1)
                                 ).isoformat(), status="pendente")
    saiu = _cron_pronto(monkeypatch, usuario)
    for _ in range(8):
        wa_bot.dispatch_proactive()
    textos = " ".join(saiu)
    assert "IPTU" in textos, (
        "o aviso da conta nao saiu — o podcast comeu a cota do dia")

def test_dez_toques_nao_viram_dez_audios(usuario, com_voz, com_noticia,
                                         monkeypatch):
    """Dez notas de voz em segundos e o padrao que a Meta pune — e este
    numero ja foi restringido duas vezes."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar_audio",
        lambda tel, dados, **kw: enviados.append(dados) or
        {"enviado": True, "via": "audio", "motivo": ""}, raising=False)
    for _ in range(10):
        responder("Quero ouvir")
    assert len(enviados) == 1, ("mandou %d audios" % len(enviados))


def test_o_ciclo_nao_morre_na_madrugada(usuario, monkeypatch):
    """O P0 mais caro da auditoria M4.2: `podcast_conv` so existia no ramo
    de fora do silencio, e das 21h as 8h o ciclo INTEIRO estourava —
    inclusive o alarme de hora marcada, que e o unico que fura o silencio.
    """
    import scheduler as sched
    for hora in (21, 23, 3, 7):
        agora = _dt.datetime(2026, 8, 18, hora, 0, 0)
        monkeypatch.setattr(tempo, "agora", lambda a=agora: a)
        monkeypatch.setattr(tempo, "hoje", lambda a=agora: a.date())
        r = sched.run_proactive_engine()
        assert isinstance(r, dict) and "total" in r, (hora, r)


# ---------------------------------------------------------------------------
# 8. auditoria M4.3 — o caminho SEMANAL, que era o unico sem freio
# ---------------------------------------------------------------------------

def test_o_convite_SEMANAL_nao_se_repete_no_ciclo_seguinte(
        usuario, horario_util, com_voz, monkeypatch):
    """O carimbo `podcast_convite_em` cobria so a PRIMEIRA vez.

    No caminho de quem ja escolheu dia, nada segurava: `podcast_ultimo` so
    muda quando a pessoa TOCA no botao, entao enquanto ela nao tocasse o
    convite era regerado a cada ciclo do cron. Cinco, seis notas identicas
    em cinco minutos — o padrao que ja rendeu duas restricoes neste numero.
    """
    _com_nicho(usuario)
    # assinante de terca (a fixture congela terca 18/08), ouviu ha 8 dias
    db.update_user_fields(usuario["id"], podcast_dia="Terça")
    db.podcast_marcar_envio(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
    db.podcast_marcar_convite(usuario["id"])
    saiu = _cron_pronto(monkeypatch, usuario)

    for _ in range(5):
        wa_bot.dispatch_proactive()
    convites = [t for t in saiu if "podcast" in t.lower()]
    assert len(convites) == 1, (
        "o convite semanal se repetiu %d vezes" % len(convites))


def test_carimbo_que_estoura_nao_derruba_o_resto_do_ciclo(
        usuario, horario_util, com_voz, monkeypatch):
    """"database is locked" e cenario real: o cron roda em thread paralela
    ao webhook.

    A mensagem SAIU; se o carimbo estoura sem protecao, ele leva junto quem
    estava atras na fila — e o `_loop_proativo` engole com "ciclo falhou",
    que foi o silencio que escondeu o P0 da madrugada.
    """
    _com_nicho(usuario)
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="IPTU", valor_reais=900.0,
                data_vencimento=(tempo.hoje() + _dt.timedelta(days=1)
                                 ).isoformat(), status="pendente")
    saiu = _cron_pronto(monkeypatch, usuario)

    def _explode(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "podcast_marcar_convite", _explode)
    wa_bot.dispatch_proactive()          # nao pode levantar
    wa_bot.dispatch_proactive()
    assert any("IPTU" in t for t in saiu), (
        "o carimbo estourou e levou o aviso da conta junto: %r" % saiu)


def test_data_ilegivel_nao_prende_quem_pediu(usuario, com_voz, com_noticia,
                                             monkeypatch):
    """No caminho proativo, data podre conta como "acabou de enviar" — e
    esta certo, o erro seguro la e mandar de menos. Aqui e o contrario: ela
    PEDIU. Sem isto um valor corrompido tirava a unica saida manual, pra
    sempre e em silencio."""
    db.update_user_fields(usuario["id"], podcast_nicho="futebol")
    with db.get_conn() as c:
        c.execute("UPDATE users SET podcast_ultimo=? WHERE id=?",
                  ("ontem de manha", usuario["id"]))
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar_audio",
        lambda tel, dados, **kw: enviados.append(dados) or
        {"enviado": True, "via": "audio", "motivo": ""}, raising=False)
    responder("Quero ouvir")
    assert enviados, "a data corrompida trancou a pessoa pra sempre"


def test_depois_so_responde_quem_tem_podcast(usuario):
    """P2-6: "depois" e "mais tarde" sao palavras de qualquer conversa.

    Quem nunca ouviu falar do recurso nao pode receber resposta sobre
    podcast.
    """
    r = responder("depois")
    assert "podcast" not in (r or "").lower(), r
    assert "próxima semana" not in (r or "").lower(), r

    db.update_user_fields(usuario["id"], podcast_nicho="games")
    r2 = responder("agora não")
    assert "próxima" in (r2 or "").lower(), r2


def test_o_convite_nao_promete_minutagem(usuario):
    """P2-8: o audio sai entre 40s e 3 min conforme a semana. Prometer "3
    minutos" na primeira frase e errar pra menos justo onde a pessoa decide
    se toca ou nao."""
    c = podcast.convite("futebol", nome="Kevin")
    assert "3 minutos" not in c["texto"], c["texto"]
    assert "minuto" not in c["texto"].lower(), c["texto"]
