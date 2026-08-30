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
    """Voz configurada e sintese dublada.

    `PODCAST_ATIVO` hoje ja e verdadeiro por default (o Kevin aprovou em
    30/08/2026), mas a fixture continua fixando o valor: assim um teste de
    comportamento nao vira, sem querer, um teste da variavel de ambiente.
    """
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(voz, "sintetizar", lambda t: b"OggS-audio-falso")
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)
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

def _pending_de_documento():
    """O payload EXATO que a foto de documento arma em producao.

    Sem a chave `quando`, `_proposta_viva` e fail-closed e a mensagem cai
    noutro handler — um teste montado assim mede um caminho que nao existe.
    """
    return {"tipo": "confirmar_documento",
            "doc": {"descricao": "energia", "valor": 187.0,
                    "vencimento": "2026-09-20"},
            "quando": tempo.agora()}


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
    assert d[0]["kind"] == "podcast-convite"
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
    db.podcast_marcar_convite(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
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
    db.podcast_marcar_convite(
        usuario["id"], quando=base - _dt.timedelta(days=8))
    assert scheduler.check_podcast(), ("nao alcancou no dia %d" % dia)


def test_o_teto_de_uma_por_semana_continua_valendo(usuario, horario_util,
                                                   com_voz):
    """"Mais alcance" nao pode virar "mais ruido": e o teto que segura."""
    _com_nicho(usuario)
    db.podcast_marcar_convite(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
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
    # O CONVITE DE 1a VEZ e a PERGUNTA DO DIA vivem dentro da janela: a
    # pessoa esta conversando com o bot naquele momento.
    assert "podcast-convite" in T.KINDS_SEM_TEMPLATE
    assert "podcast-dia" in T.KINDS_SEM_TEMPLATE
    # O LEMBRETE SEMANAL tem template — e e ele que faz o dia escolhido
    # valer. Sem ele, "toda segunda" so alcancava quem por acaso tivesse
    # falado com o bot nas ultimas 24h.
    assert T.KIND_TEMPLATE.get("podcast") == "resolveai_podcast_pronto"
    assert T.CATALOGO["resolveai_podcast_pronto"].botoes[0] == "Quero ouvir"
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
    db.podcast_marcar_convite(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
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


# ---------------------------------------------------------------------------
# 9. auditoria M4.5: o buraco ENTRE DIAS
# ---------------------------------------------------------------------------
# O M4.4 fechou a repeticao dentro do dia (`dispatched_today`) e o M4.5 abriu
# a repeticao entre dias, no mesmo commit em que tirou o dia fixo. Nenhum
# teste avancava o calendario — por isso passou.

def test_o_convite_nao_volta_todO_dia_pra_quem_nao_toca(usuario, com_voz,
                                                        monkeypatch):
    """O defeito media 14 convites por pessoa em 14 dias.

    `podcast_ultimo` so muda quando a pessoa TOCA no botao. Quem recebeu e
    nao respondeu ficava com o campo parado, e sem dia fixo segurando o
    convite renascia todo dia — assinatura de ritmo, que e o que ja rendeu
    duas restricoes neste numero.
    """
    base = _dt.datetime(2026, 8, 18, 10, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda: base)
    monkeypatch.setattr(tempo, "hoje", lambda: base.date())
    _com_nicho(usuario)
    db.podcast_marcar_envio(usuario["id"], quando=base - _dt.timedelta(days=8))
    db.podcast_marcar_convite(usuario["id"],
                              quando=base - _dt.timedelta(days=8))
    saiu = _cron_pronto(monkeypatch, usuario)

    convites = 0
    for dia in range(14):
        agora = base + _dt.timedelta(days=dia)
        monkeypatch.setattr(tempo, "agora", lambda a=agora: a)
        monkeypatch.setattr(tempo, "hoje", lambda a=agora: a.date())
        with db.get_conn() as c:      # janela de 24h aberta todo dia
            c.execute("DELETE FROM dispatches")
        db.log_message(None, usuario["telefone"], "in", "texto", "oi")
        antes = len(saiu)
        wa_bot.dispatch_proactive()
        convites += len([t for t in saiu[antes:] if "podcast" in t.lower()])

    assert convites <= 2, (
        "o convite voltou %d vezes em 14 dias (o teto e 1 por semana)"
        % convites)


def test_a_amostra_e_so_do_dono(usuario, monkeypatch):
    """Cinco audios + cinco chamadas pagas de LLM e TTS por invocacao.

    Liberar isso pra qualquer um dos 11 clientes nao quebrava um unico teste
    (auditoria M4.5, placebo P8).
    """
    import voz
    chamou = []
    monkeypatch.setattr(voz, "disponivel",
                        lambda: chamou.append("voz") or True)
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999998888")

    r = wa_bot._handle_commands(usuario, usuario["telefone"],
                                "amostra do podcast")
    assert not chamou, "cliente comum disparou a amostra do dono"
    assert r is None or "amostra" not in (r or "").lower(), r

    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "")
    chamou.clear()
    wa_bot._handle_commands(usuario, usuario["telefone"],
                            "amostra do podcast")
    assert not chamou, "sem ADMIN_PHONE tem que ficar fechado, nao aberto"


def test_a_amostra_espaca_os_envios(monkeypatch):
    """Cinco nichos sao ate dez mensagens; de enfiada e a assinatura de
    ritmo que ja rendeu 3h de restricao neste numero."""
    import inspect
    fonte = inspect.getsource(wa_bot._amostra_de_podcast)
    assert "time.sleep" in fonte, (
        "a amostra manda em rajada, sem o freio de espacamento")


def test_a_feature_nao_dispara_sem_o_dono_ligar(usuario, horario_util,
                                                monkeypatch):
    """Deploy nao e lancamento.

    O codigo pode estar no ar com a feature desligada — e enquanto o Kevin
    nao ouvir as amostras e aprovar, nenhum cliente recebe convite nenhum.
    Feature que se liga sozinha ao subir e feature que estreia sem ninguem
    ter decidido.
    """
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", False)
    _com_nicho(usuario)
    assert scheduler.check_podcast() == []

    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)
    assert scheduler.check_podcast(), "ligou e continuou mudo"


def test_o_dia_escolhido_e_respeitado(usuario, com_voz, monkeypatch):
    """O Kevin: "tem que respeitar o que o cliente quiser, no dia certo que
    ele selecionar". Com o template, isso passou a ser possivel."""
    _com_nicho(usuario)
    db.update_user_fields(usuario["id"], podcast_dia="Sexta")
    for dia, esperado in ((17, False), (18, False), (21, True), (22, False)):
        agora = _dt.datetime(2026, 8, dia, 10, 0, 0)   # 21/08/2026 e sexta
        monkeypatch.setattr(tempo, "agora", lambda a=agora: a)
        monkeypatch.setattr(tempo, "hoje", lambda a=agora: a.date())
        db.podcast_marcar_envio(usuario["id"],
                                quando=agora - _dt.timedelta(days=8))
        db.podcast_marcar_convite(usuario["id"],
                                  quando=agora - _dt.timedelta(days=8))
        with db.get_conn() as c:
            c.execute("DELETE FROM dispatches")
        assert bool(scheduler.check_podcast()) is esperado, (
            "dia %d/08: esperava %s" % (dia, esperado))


def test_o_lembrete_semanal_usa_o_template(usuario, com_voz, monkeypatch):
    """E o template que faz o dia valer: fora da janela de 24h, texto livre
    nao sai, e ai "toda sexta" seria promessa quebrada."""
    import templates as T
    _com_nicho(usuario)
    db.podcast_marcar_envio(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
    db.podcast_marcar_convite(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
    d = scheduler.check_podcast()
    assert d and d[0]["kind"] == "podcast", d
    assert T.KIND_TEMPLATE["podcast"] == "resolveai_podcast_pronto"


def test_o_audio_nunca_sai_junto_com_o_lembrete(usuario, com_voz):
    """A regra que o Kevin repetiu: "nunca mandaremos o audio sem a
    permissao". O lembrete e so texto+botao; o audio so no toque."""
    _com_nicho(usuario)
    db.podcast_marcar_envio(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
    db.podcast_marcar_convite(
        usuario["id"], quando=tempo.agora() - _dt.timedelta(days=8))
    d = scheduler.check_podcast()[0]
    assert "audio" not in d, d
    assert isinstance(d.get("message"), str) and d["message"]
    assert d["botoes"][0] == "Quero ouvir"


# ---------------------------------------------------------------------------
# 10. os 11 testers: escolher o assunto DENTRO da conversa
# ---------------------------------------------------------------------------
# Eles se cadastraram antes de a landing ter selecao de nicho, entao nenhum
# tem assunto guardado. Alcanca-los fora da janela exigiria template novo, e
# o Kevin foi direto: "se precisar usar templates, use os aprovados".
# Entao a novidade chega quando eles voltam a falar com o bot.

def test_quem_nao_tem_assunto_e_perguntado_em_vez_de_adivinhado(usuario,
                                                                com_voz):
    """Escolher por eles seria mandar audio de um tema que ninguem pediu."""
    r = responder("quero o áudio")
    assert "futebol" in r.lower() and "games" in r.lower(), r
    # slot proprio, nao `PENDING`: a pergunta nao pode atropelar decisao viva
    assert TELEFONE in wa_bot.PODCAST_PERGUNTA
    assert not wa_bot.PENDING.get(TELEFONE)


def test_a_resposta_com_o_assunto_guarda_o_nicho(usuario, com_voz):
    responder("quero o áudio")
    r = responder("games")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "games", r
    assert "games" in r.lower()


def test_assunto_solto_sem_a_pergunta_nao_vira_assinatura(usuario):
    """"futebol" numa conversa qualquer nao pode virar assinatura de audio."""
    responder("futebol")
    assert not db.get_user(usuario["id"])["podcast_nicho"]


def test_assunto_que_nao_existe_segue_pro_motor_normal(usuario, com_voz):
    """SEM catch-all (auditoria M5.4, P0-1). O que nao e assunto conhecido
    cai fora do bloco e o resto do bot responde — a alternativa era uma
    jaula de 24h que engolia lembrete, baixa e ate o botao de reativacao."""
    responder("quero o áudio")
    responder("criptomoeda")
    assert not db.get_user(usuario["id"])["podcast_nicho"]
    # a pergunta continua de pe: ela pode responder certo na sequencia
    responder("moda")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "moda"


def test_numero_solto_nunca_vira_assinatura_de_podcast(usuario, com_voz):
    """O menu 1/2 custou a FASE 1 inteira. Um digito e resposta de MENU —
    do menu de baixa, do menu de confirmacao — e nunca do podcast. A lista
    de assuntos nem numera as opcoes; o caminho numerico so tinha downside."""
    responder("quero o áudio")
    for digito in ("1", "2", "3", "4", "5"):
        responder(digito)
        assert not db.get_user(usuario["id"])["podcast_nicho"], digito


def test_recusa_na_escolha_nao_mexe_nos_lembretes(usuario, com_voz):
    responder("quero o áudio")
    r = responder("não quero")
    assert "lembretes continuam" in r.lower(), r
    assert not db.get_user(usuario["id"])["podcast_nicho"]


def test_a_novidade_vai_junto_na_resposta_de_reativacao(usuario):
    """Sem gastar mensagem nova e sem esperar template: a noticia viaja na
    resposta que o botao do `reativar_boas_vindas` ja provocava."""
    r = responder("Quero comecar")
    # as tres partes: que existe, o que e, e como pedir. Faltando qualquer
    # uma, o tester le a mensagem e nao sabe o que fazer com ela.
    assert "novidade" in r.lower(), r
    assert "resumo em áudio" in r.lower(), r
    assert "quero o áudio" in r.lower(), r





# ---------------------------------------------------------------------------
# 11. "pra renovar, mande que renovou"
# ---------------------------------------------------------------------------
# `resolveai_trial_estendido` ainda esta em analise na Meta, entao o aviso
# proprio nao sai. Mas o botao do `reativar_boas_vindas` (aprovado) abre a
# janela de 24h — e dentro dela texto livre passa.

def test_a_reativacao_diz_quantos_dias_de_teste_sobraram(usuario):
    db.update_user_fields(usuario["id"], status="trial")
    r = responder("Quero comecar")
    assert "valendo até" in r.lower(), r
    # o numero tem que ser o REAL, nao um chute bonito
    esperado = db.trial_days_left(db.get_user(usuario["id"]))
    assert ("(%d dias)" % esperado) in r, (esperado, r)


def test_quem_ja_paga_nao_ouve_falar_de_teste(usuario):
    """Dizer "renovei seu teste" pra quem assinou e desfazer a venda."""
    db.update_user_fields(usuario["id"], status="ativo")
    r = responder("Quero comecar")
    assert "teste" not in r.lower().split("🎧")[0], r
    # mas a novidade do audio continua indo pra ele
    assert "novidade" in r.lower(), r


def test_trial_vencido_nao_promete_dia_nenhum(usuario, monkeypatch):
    """Sem dias de verdade, a linha some — mentir seria pior que calar."""
    db.update_user_fields(usuario["id"], status="trial")
    monkeypatch.setattr(db, "trial_days_left", lambda u, *a, **k: 0)
    r = responder("Quero comecar")
    assert "renovei" not in r.lower(), r
    assert "novidade" in r.lower(), r


def test_falha_ao_ler_o_trial_nao_derruba_a_reativacao(usuario, monkeypatch):
    """A pessoa voltou depois de semanas: essa resposta nao pode morrer."""
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "trial_days_left", explode)
    r = responder("Quero comecar")
    assert "luz 187" in r, r
    assert "renovei" not in r.lower(), r


# ---------------------------------------------------------------------------
# 12. AUDITORIA M5.2 — a pergunta do nicho nao pode virar jaula
# ---------------------------------------------------------------------------
# O auditor mediu: com a pergunta de pe, "luz 187 vence dia 20" respondia
# "Nao peguei o assunto" e o lembrete sumia calado, por 24h. E o mesmo modo
# de falha que o bloco de BAIXA chama de "o pior bug do produto".

def test_pergunta_do_nicho_nao_engole_lembrete(usuario, com_voz):
    responder("quero o áudio")
    antes = len(db.list_items(usuario["id"]))
    r = responder("luz 187 vence dia 20")
    assert len(db.list_items(usuario["id"])) == antes + 1, r
    assert "não peguei o assunto" not in r.lower(), r


def test_pergunta_do_nicho_nao_engole_baixa(usuario, com_voz):
    """DOIS itens de proposito: com um so, "paguei a luz" da baixa direta e
    o teste nunca chega no menu numerado — que e onde o defeito mora."""
    responder("luz 187 vence dia 20")
    responder("agua 90 vence dia 22")
    responder("quero o áudio")
    r = responder("paguei a conta")
    assert "não peguei o assunto" not in r.lower(), r
    # o menu numerado tem que ser DO BOT, nao do podcast
    if "1" in r and "2" in r:
        r2 = responder("2")
        assert "🎧" not in r2, ("o numero do menu de baixa virou "
                                "assinatura de podcast: %s" % r2)
    pagos = [i for i in db.list_items(usuario["id"])
             if (i.get("status") or "") != "pendente"]
    assert pagos, "a baixa sumiu dentro da pergunta do nicho"


def test_pergunta_do_nicho_nao_engole_o_botao_de_reativacao(usuario, com_voz):
    responder("quero o áudio")
    r = responder("Quero comecar")
    assert "luz 187" in r, r


def test_pergunta_do_nicho_nao_sequestra_pedido_de_cancelar(usuario, com_voz):
    """`_RECUSA_RE` casa "cancela": sem porta de saida, "cancela o lembrete
    da luz" respondia "seus lembretes continuam normais" e nao cancelava."""
    responder("luz 187 vence dia 20")
    responder("quero o áudio")
    r = responder("cancela o lembrete da luz")
    assert "lembretes continuam normais" not in r.lower(), r


def test_pergunta_do_nicho_expira_em_20_min_nao_em_24h(usuario, com_voz,
                                                       monkeypatch):
    """Pergunta aberta usa `AJUSTE_TTL_S`. 24h e jaula."""
    responder("quero o áudio")
    depois = tempo.agora() + _dt.timedelta(minutes=25)
    monkeypatch.setattr(tempo, "agora", lambda a=depois: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=depois: a.date())
    responder("games")
    assert not db.get_user(usuario["id"])["podcast_nicho"], \
        "pendencia de 25 min atras ainda respondeu"


def test_a_pergunta_do_nicho_nao_atropela_pendencia_viva(usuario, com_voz):
    """O `doc` do boleto fotografado nao pode evaporar porque a pessoa
    tocou "Quero ouvir" no meio."""
    wa_bot._armar_pending(TELEFONE, _pending_de_documento())
    responder("quero o áudio")
    guardado = wa_bot.PENDING.get(TELEFONE) or {}
    assert guardado.get("tipo") == "confirmar_documento", guardado


def test_a_pergunta_do_nicho_e_armada_com_carimbo(usuario, com_voz):
    """Sem hora, decisao sem prazo vira jaula — a licao do `_armar_pending`
    vale igual pro slot proprio."""
    responder("quero o áudio")
    assert wa_bot.PODCAST_PERGUNTA.get(TELEFONE) is not None


# ---------------------------------------------------------------------------
# 13. AUDITORIA M5.2 — a chave de emergencia tem que desligar TUDO
# ---------------------------------------------------------------------------

def _pronto_pra_pergunta_do_dia(usuario):
    """Estado EXATO em que a pergunta do dia dispara: ouviu o primeiro
    episodio ha 20 min e ainda nao escolheu dia."""
    _com_nicho(usuario)
    db.update_user_fields(
        usuario["id"], podcast_dia=None, podcast_dia_perguntado=None,
        podcast_ultimo=(tempo.agora() - _dt.timedelta(minutes=20)
                        ).strftime("%Y-%m-%d %H:%M:%S"))


def test_desligar_a_chave_cala_tambem_a_pergunta_do_dia(usuario, monkeypatch):
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    _pronto_pra_pergunta_do_dia(usuario)
    # primeiro a prova de que a fila NAO esta vazia por outro motivo
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)
    assert scheduler.check_podcast_dia(), "a fila ja estava vazia sozinha"

    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", False)
    assert scheduler.check_podcast_dia() == []


def test_desligar_a_chave_cala_o_audio_reativo(usuario, com_voz,
                                               com_noticia, monkeypatch):
    """"quero ouvir" com a chave desligada nao pode gerar TTS pago.

    A prova e o CONTADOR de sintese, nao o texto da resposta: sem feed o
    fluxo ja falha sozinho, e um teste que so olha a resposta ficaria verde
    com a chave completamente ignorada.
    """
    _com_nicho(usuario)

    chamadas = []
    monkeypatch.setattr(voz, "sintetizar",
                        lambda *a, **k: chamadas.append(1) or b"audio")

    responder("quero ouvir")
    assert chamadas, "a chave ligada nao gerou audio — o teste nao mede nada"

    chamadas.clear()
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", False)
    r = responder("quero ouvir")
    assert not chamadas, "chave desligada e TTS pago rodou assim mesmo"
    assert "fora do ar" in r.lower(), r


def test_valor_estranho_na_chave_desliga_em_vez_de_ligar(monkeypatch):
    """O Kevin so toca nessa variavel sob pressao. "desligado" digitado as
    pressas nao pode LIGAR a feature."""
    import importlib
    for valor, esperado in (("", True), ("sim", True), ("1", True),
                            ("0", False), ("nao", False),
                            ("desligado", False), ("OFF!", False),
                            ("n", False)):
        monkeypatch.setenv("PODCAST_ATIVO", valor)
        assert importlib.reload(scheduler).PODCAST_ATIVO is esperado, valor
    monkeypatch.delenv("PODCAST_ATIVO", raising=False)
    assert importlib.reload(scheduler).PODCAST_ATIVO is True


# ---------------------------------------------------------------------------
# 14. AUDITORIA M5.2 — quem disse NAO nao ouve a oferta de novo
# ---------------------------------------------------------------------------
# Re-oferta depois de "nao" e exatamente o que a regua da Meta pune num
# numero ja restringido duas vezes.

def test_quem_cancelou_nao_e_repitchado_na_reativacao(usuario, com_voz):
    _com_nicho(usuario)
    responder("não quero mais o podcast")
    r = responder("Quero comecar")
    assert "novidade" not in r.lower(), r
    assert "luz 187" in r, r


def test_quem_cancelou_ainda_consegue_voltar_se_pedir(usuario, com_voz):
    """Recusa nao e banimento: se ELA pedir, o bot atende."""
    _com_nicho(usuario)
    responder("não quero mais o podcast")
    responder("quero o áudio")
    responder("games")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "games"


def test_recusa_carimbada_tira_da_fila_do_convite_mesmo_com_nicho(usuario):
    """Contrato da CONSULTA, medido direto.

    Pelo fluxo de hoje cancelar tambem zera o nicho, entao a query ja
    excluiria a pessoa pelo `podcast_nicho IS NULL` — e um teste pelo fluxo
    ficaria verde com o filtro de recusa apagado. Aqui o estado e montado na
    mao: nicho preenchido E recusa carimbada. E o unico jeito de o filtro
    ser realmente medido, e ele existe pra que nenhum caminho futuro que
    preserve o nicho consiga re-convidar quem disse nao.
    """
    _com_nicho(usuario)
    assert usuario["id"] in [u["id"] for u in db.podcast_a_convidar()]

    db.update_user_fields(usuario["id"],
                          podcast_recusado_em=tempo.agora().isoformat())
    assert usuario["id"] not in [u["id"] for u in db.podcast_a_convidar()]


def test_o_slot_da_pergunta_do_nicho_e_limpo_entre_testes():
    """Estado de processo que sobrevive a um teste vira medida do anterior.

    Ja aconteceu neste arquivo: sem `PODCAST_PERGUNTA` na fixture `limpo`, a
    pergunta de um teste respondia o texto do seguinte. Nao da pra medir
    isso de dentro de um teste (a fixture roda antes de cada um), entao o
    que se mede e o contrato: o dicionario esta na lista de limpeza.
    """
    import ast
    import inspect
    import textwrap

    import conftest

    # Le a LISTA de verdade, via AST: procurar a string no fonte inteiro
    # ficava verde com o nome escrito num comentario.
    arvore = ast.parse(textwrap.dedent(inspect.getsource(conftest.limpo)))
    limpos = {n.value for n in ast.walk(arvore)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and not isinstance(getattr(n, "parent", None), ast.Expr)}
    fonte_sem_comentario = "\n".join(
        l.split("#")[0] for l in inspect.getsource(conftest.limpo).splitlines())
    for nome in ("PODCAST_PERGUNTA",):
        assert nome in limpos and nome in fonte_sem_comentario, (
            "%s guarda estado por telefone e ficou fora da fixture `limpo`"
            % nome)
        assert isinstance(getattr(wa_bot, nome), dict)


# ---------------------------------------------------------------------------
# 15. AUDITORIA M5.5 — o extra nunca passa na frente da decisao
# ---------------------------------------------------------------------------

def test_botao_esquece_do_documento_nao_vira_cancelamento_de_podcast(usuario,
                                                                     com_voz):
    """"Esquece" e titulo de botao do documento E casa `_RECUSA_RE`. Com a
    pergunta do nicho de pe, o toque virava "cancelei o podcast", o `PENDING`
    ficava intacto, e 20 min depois o resgate criava o lembrete descartado."""
    wa_bot._armar_pending(TELEFONE, _pending_de_documento())
    responder("quero o áudio")
    r = responder("Esquece")
    assert "áudio era só um extra" not in r.lower(), r
    assert not db.get_user(usuario["id"])["podcast_recusado_em"], r
    # e o documento nao pode ter ficado pendurado esperando resgate
    assert not wa_bot.PENDING.get(TELEFONE), wa_bot.PENDING
    # A RESPOSTA TEM QUE SER A DO BOTAO DE DOCUMENTO, nao a de outro handler.
    # Sem esta linha o teste ficava verde com um `PENDING` sem `quando`, que
    # `_proposta_viva` recusa — ou seja, medindo um caminho que producao nao
    # tem. E o mesmo defeito que este arquivo ja pegou duas vezes: teste que
    # passa pelo estado inicial em vez de passar pelo comportamento.
    assert "não guardei nada" in r.lower(), r


def test_a_pergunta_do_nicho_espera_o_menu_de_baixa(usuario, com_voz):
    """`BAIXA_ESCOLHA` armado na mao de proposito: o menu ambiguo depende de
    duas descricoes que empatam no placar, e amarrar o teste a essa
    heuristica mediria o parser, nao a guarda.

    Contrato desde o M5.6: a baixa que chega no meio DESCARTA a pergunta —
    deixa-la viva fazia a resposta cair no resgate de pendencia. A pessoa
    nao fica presa: e so pedir de novo depois de fechar a de cima.
    """
    responder("luz 187 vence dia 20")
    responder("quero o áudio")
    ids = [i["id"] for i in db.list_items(usuario["id"], status="pendente")]
    wa_bot.BAIXA_ESCOLHA[TELEFONE] = {"ids": ids, "quando": tempo.agora()}
    responder("futebol")
    assert not db.get_user(usuario["id"])["podcast_nicho"]

    wa_bot.BAIXA_ESCOLHA.pop(TELEFONE, None)
    responder("quero o áudio")
    responder("futebol")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "futebol"


def test_cancelar_o_podcast_mata_a_pergunta_pendente(usuario, com_voz):
    """Sem isto, um "games" solto dentro de 20 min re-assinava quem tinha
    acabado de cancelar E apagava o carimbo da recusa."""
    responder("quero o áudio")
    responder("não quero mais o podcast")
    assert TELEFONE not in wa_bot.PODCAST_PERGUNTA
    responder("games")
    u = db.get_user(usuario["id"])
    assert not u["podcast_nicho"], "re-assinou quem acabou de cancelar"
    assert u["podcast_recusado_em"], "o carimbo da recusa foi apagado"


def test_a_reativacao_diz_o_prazo_e_nao_afirma_ter_renovado(usuario):
    """`_COMECAR_RE` casa "bora" solto, e quem renova e o painel — nao este
    caminho. Afirmar "renovei" aqui era mentir pra quem so digitou "bora"."""
    db.update_user_fields(usuario["id"], status="trial")
    r = responder("bora")
    assert "renovei" not in r.lower(), r
    assert "valendo até" in r.lower(), r
    esperado = db.trial_days_left(db.get_user(usuario["id"]))
    assert ("(%d dias)" % esperado) in r, (esperado, r)


# ---------------------------------------------------------------------------
# 16. AUDITORIA M5.6 — nao pergunta o que nao vai poder ouvir
# ---------------------------------------------------------------------------

def test_nao_pergunta_o_assunto_com_decisao_na_mesa(usuario, com_voz):
    """Perguntar e depois ignorar a resposta e a mesma jaula com outro nome:
    medido, a pessoa respondia "futebol" e levava "nao identifiquei conta,
    data nem valor" — de novo e de novo, por ate 10 min."""
    responder("luz 187 vence dia 20")
    ids = [i["id"] for i in db.list_items(usuario["id"], status="pendente")]
    wa_bot.BAIXA_ESCOLHA[TELEFONE] = {"ids": ids, "quando": tempo.agora()}

    r = responder("quero o áudio")
    assert "futebol" not in r.lower(), r
    assert TELEFONE not in wa_bot.PODCAST_PERGUNTA, "perguntou assim mesmo"

    # resolvida a de cima, o pedido volta a funcionar
    wa_bot.BAIXA_ESCOLHA.pop(TELEFONE, None)
    r = responder("quero o áudio")
    assert "futebol" in r.lower(), r


def test_decisao_que_chega_depois_descarta_a_pergunta(usuario, com_voz):
    """A pergunta ficar de pe era pior: a resposta dela caia fora do bloco,
    chegava no resgate de pendencia e matava a decisao do documento."""
    responder("quero o áudio")
    wa_bot._armar_pending(TELEFONE, _pending_de_documento())
    responder("games")
    assert not db.get_user(usuario["id"])["podcast_nicho"]
    assert TELEFONE not in wa_bot.PODCAST_PERGUNTA


def test_decisao_vencida_nao_tranca_o_pedido_de_audio(usuario, com_voz,
                                                      monkeypatch):
    """VIVA, nao so presente (autoauditoria M5.7).

    A primeira versao da guarda lia presenca crua. Uma decisao ja MORTA —
    `PENDING` fora do `PENDING_TTL_S`, baixa fora do `BAIXA_ESCOLHA_TTL_S` —
    continuava trancando a pessoa fora do recurso, sem que existisse
    pergunta nenhuma na tela dela pra responder.
    """
    wa_bot._armar_pending(TELEFONE, _pending_de_documento())
    ids = [i["id"] for i in db.list_items(usuario["id"], status="pendente")]
    wa_bot.BAIXA_ESCOLHA[TELEFONE] = {"ids": ids, "quando": tempo.agora()}

    # com os dois vivos, o extra espera
    r = responder("quero o áudio")
    assert "só um instante" in r.lower(), r

    # o relogio anda alem dos dois prazos: nada mais esta na mesa
    depois = tempo.agora() + _dt.timedelta(
        seconds=max(wa_bot.PENDING_TTL_S, wa_bot.BAIXA_ESCOLHA_TTL_S) + 60)
    monkeypatch.setattr(tempo, "agora", lambda a=depois: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=depois: a.date())
    r = responder("quero o áudio")
    assert "futebol" in r.lower(), ("decisao morta continuou trancando: %s" % r)


def test_carimbo_de_baixa_ilegivel_nao_tranca_a_pessoa(usuario, com_voz):
    """Na duvida a pessoa NAO fica presa — o mesmo criterio que o
    `_escolha_de_baixa` ja usa pra tratar carimbo podre como estado morto."""
    wa_bot.BAIXA_ESCOLHA[TELEFONE] = {"ids": [], "quando": "ontem de manha"}
    r = responder("quero o áudio")
    assert "futebol" in r.lower(), r


def test_a_guarda_nao_olha_estado_que_nunca_esta_vivo_ali(usuario):
    """`CONFERIR_FILA` e `AUDIO_ESPERADO` sao armados DEPOIS do
    `_handle_commands` e esvaziados no mesmo ciclo: na hora em que a guarda
    le, os dois estao sempre vazios. Incluir os dois nao protegia nada e,
    como nenhum tem TTL, uma entrada orfa trancaria o recurso pra sempre."""
    import inspect
    fonte = inspect.getsource(wa_bot._decisao_de_conversa_viva)
    corpo = fonte.split('"""')[-1]
    assert "CONFERIR_FILA" not in corpo, corpo
    assert "AUDIO_ESPERADO" not in corpo, corpo


# ---------------------------------------------------------------------------
# 17. AUDITORIA M5.6 — quem nao tem acesso nao gasta TTS pago
# ---------------------------------------------------------------------------

def test_quem_nao_tem_acesso_nao_e_convidado_pro_audio(usuario):
    for status in ("bloqueado", "cancelado", "vencido"):
        db.update_user_fields(usuario["id"], status=status)
        r = responder("Quero comecar")
        assert "novidade" not in r.lower(), (status, r)


def test_quem_nao_tem_acesso_nao_gera_tts_pago(usuario, com_voz, com_noticia,
                                               monkeypatch):
    """O envio ja falharia no canal — mas so DEPOIS de a conta paga ser
    gasta, e a resposta de falha convida a tentar de novo."""
    _com_nicho(usuario)
    chamadas = []
    monkeypatch.setattr(voz, "sintetizar",
                        lambda *a, **k: chamadas.append(1) or b"audio")

    responder("quero ouvir")
    assert chamadas, "com acesso nao gerou audio — o teste nao mede nada"

    chamadas.clear()
    db.update_user_fields(usuario["id"], status="bloqueado")
    r = responder("quero ouvir")
    assert not chamadas, "queimou TTS pago pra quem nao tem acesso"
    assert "teste terminou" in r.lower(), r


# ---------------------------------------------------------------------------
# 18. AUDITORIA M5.6 — a data do trial usa o mesmo prazo do gate de acesso
# ---------------------------------------------------------------------------

def test_a_data_do_trial_segue_o_trial_days_configurado(usuario, monkeypatch):
    """`trial_days_left(user)` sem argumento cai no default 14 da funcao.
    Com `TRIAL_DAYS` diferente, a mensagem prometia uma validade que o gate
    de acesso nao honra."""
    monkeypatch.setattr(wa_bot, "TRIAL_DAYS", 7)
    db.update_user_fields(usuario["id"], status="trial")
    r = responder("bora")
    assert "(7 dias)" in r, r


def test_o_ultimo_dia_de_teste_nao_fala_1_dias(usuario, monkeypatch):
    monkeypatch.setattr(db, "trial_days_left", lambda u, *a, **k: 1)
    db.update_user_fields(usuario["id"], status="trial")
    r = responder("bora")
    assert "(1 dia)" in r, r


# ---------------------------------------------------------------------------
# 19. o contrato com a landing OFICIAL (resolveai.ia.br)
# ---------------------------------------------------------------------------
# O formulario de https://resolveai.ia.br manda, na primeira mensagem, a
# frase "E quero o resumo semanal de <assunto>." — e o `_NICHO_DA_LANDING_RE`
# le dali. Sao dois repositorios diferentes (o site vive em
# `kevinromaoribeiro/resolveai-site`), entao nada avisa quando um dos lados
# muda. Este teste e o aviso.
#
# Ja pegou um defeito real: com o valor "ia" (2 letras) a frase NAO casava o
# regex, que exige 3+, e o assunto escolhido se perdia em silencio.

_OPCOES_DA_LANDING = ("futebol", "games", "inteligência artificial",
                      "moda", "varejo online")


@pytest.mark.parametrize("escolha", _OPCOES_DA_LANDING)
def test_o_assunto_escolhido_na_landing_chega_no_bot(usuario, escolha):
    frase = ("Oi! Quero começar meus 14 dias grátis do Resolve AI 🚀\n\n"
             "E quero o resumo semanal de %s." % escolha)
    m = wa_bot._NICHO_DA_LANDING_RE.search(frase)
    assert m, "a frase da landing nao casa o parser: %r" % escolha
    assert podcast.nicho_valido(m.group(1).strip()), escolha


def test_toda_opcao_da_landing_e_um_nicho_que_existe(usuario):
    """Opcao no site que o bot nao conhece vira pessoa sem assunto, calada."""
    for escolha in _OPCOES_DA_LANDING:
        assert podcast.nicho_valido(escolha), escolha
    # e os cinco nichos do produto estao todos oferecidos no site
    oferecidos = {podcast.nicho_valido(o) for o in _OPCOES_DA_LANDING}
    assert oferecidos == set(podcast.NICHOS), (oferecidos, set(podcast.NICHOS))


def test_quem_escolheu_na_landing_nao_e_perguntado_de_novo(usuario, com_voz):
    """A ida e volta a mais no primeiro minuto e onde as pessoas desistem."""
    responder("Oi! Quero começar meus 14 dias grátis do Resolve AI 🚀\n\n"
              "E quero o resumo semanal de games.")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "games"
    r = responder("quero o áudio")
    assert "é um destes" not in r.lower(), r
