# -*- coding: utf-8 -*-
"""Ate 3 assuntos, um episodio pra cada, na janela que a pessoa escolheu.

O Kevin, 31/08/2026: "vamos limitar ate 3 categorias por cliente e no maximo
a cada 5 dias, e se tiver mais de uma categoria mandamos as 3 audios no mesmo
dia, claro nomeando do que se trata" — e, na mesma leva, "se a pessoa pedir a
cada 5 dias, precisa entao ler as mensagens de 5 dias pra tras".

Os testes de ponta a ponta do podcast moram em `test_podcast_ponta_a_ponta`.
Este arquivo cobre so o que o M9.x mudou: catalogo de 16, multiplos assuntos,
janela = frequencia, e os tres farois do dash.
"""
import datetime as _dt

import pytest

import db
import noticias
import podcast
import scheduler
import tempo
import voz
import wa_bot
from conftest import TELEFONE, responder


@pytest.fixture
def com_voz(monkeypatch):
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(voz, "sintetizar", lambda *a, **k: b"OggS" + b"x" * 8000)
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)
    return True


@pytest.fixture(autouse=True)
def farol_limpo():
    """`podcast_log` e estado global e nao esta na fixture `limpo`.

    Sem zerar aqui, o farol de um teste conta os episodios do teste anterior
    — foi exatamente assim que a contagem por semana passou a mentir na
    primeira rodada."""
    def zera():
        try:
            with db.get_conn() as c:
                c.execute("DELETE FROM podcast_log")
        except Exception:
            pass          # a tabela so nasce no primeiro registro
    zera()
    yield
    zera()


def _pronto(usuario, nichos="futebol", horas_atras=7):
    """Pessoa dentro do produto, com assunto escolhido e ja fora das 6h."""
    db.update_user_fields(usuario["id"], podcast_nicho=nichos)
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=horas_atras)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))


def _uma_noticia(*a, **k):
    return [{"titulo": "Noticia do dia", "resumo": "resumo",
             "fonte": "Fonte", "link": "http://x", "data": None}]


# ---------------------------------------------------------------------------
# 1. o catalogo: 16 assuntos, 3 fontes cada, todos com filtro
# ---------------------------------------------------------------------------

def test_dezesseis_assuntos():
    """"nao exclua nenhuma, apenas adicione" — 5 originais + 11 novos."""
    assert len(podcast.NICHOS) == 16


def test_tres_fontes_por_assunto():
    """"valide 3 fontes extremamente confiaveis pra cada categoria". Fonte
    unica quebrando = assunto mudo, e mudo nao gera reclamacao, gera
    cancelamento."""
    for k, d in podcast.NICHOS.items():
        assert len(d["fontes"]) == 3, (k, d["fontes"])


def test_nenhuma_fonte_repetida_dentro_do_assunto():
    for k, d in podcast.NICHOS.items():
        urls = [f[1] if isinstance(f, (tuple, list)) else f
                for f in d["fontes"]]
        assert len(set(urls)) == 3, (k, urls)


def test_todo_assunto_sabe_filtrar_o_proprio_tema():
    """"garanta que vamos entregar o que a pessoa pedir e nada mais". Sem
    termo no filtro, o assunto aceita qualquer materia da fonte."""
    for k in podcast.NICHOS:
        assert podcast._ASSUNTO.get(k), k


@pytest.mark.parametrize("tema,titulo", [
    ("futebol", "Palmeiras vence o classico e assume a lideranca"),
    ("economia", "Dolar fecha em alta com Copom e Selic a 11%"),
    ("gastronomia", "Restaurante estrelado abre com menu degustacao"),
    ("horoscopo", "Lua cheia em Aries: previsao para os signos"),
    ("ciencia", "Nasa divulga imagem de galaxia captada por telescopio"),
    ("carros", "Fiat lanca nova picape com motor flex"),
    ("musica", "Anitta lanca album novo e anuncia turne"),
    ("viagens", "Novo voo direto liga Recife a Lisboa"),
])
def test_cada_tema_reconhece_a_propria_noticia(tema, titulo):
    assert podcast.e_do_assunto(tema, titulo, "")


@pytest.mark.parametrize("tema,titulo", [
    # falsos positivos que a rodada de validacao pegou:
    ("musica", "Fiat lanca nova picape com motor flex"),
    ("ciencia", "Chef estrelado abre restaurante em Sao Paulo"),
    ("viagens", "ONU aprova cessar-fogo apos acordo entre paises"),
])
def test_o_filtro_nao_deixa_passar_assunto_dos_outros(tema, titulo):
    """Cada um destes ja tinha entrado no episodio errado: "lanca" sozinho
    pegava lancamento de carro em musica; "estrela" pegava chef estrelado;
    "pais" pegava geopolitica em viagens."""
    assert not podcast.e_do_assunto(tema, titulo, "")


# ---------------------------------------------------------------------------
# 2. ate tres assuntos por pessoa
# ---------------------------------------------------------------------------

def test_o_teto_e_tres():
    assert podcast.MAX_ASSUNTOS == 3


def test_guarda_no_maximo_tres(usuario, com_voz):
    responder("quero o audio")
    responder("1, 6, 12, 4, 5")
    assert len(podcast.nichos_da_pessoa(db.get_user(usuario["id"]))) == 3


def test_nao_repete_assunto_escolhido_duas_vezes(usuario, com_voz):
    """"1, 1, 6" nao pode virar dois episodios de futebol no mesmo dia."""
    responder("quero o audio")
    responder("1, 1, 6")
    assert podcast.nichos_da_pessoa(
        db.get_user(usuario["id"])) == ["futebol", "economia"]


def test_quem_tinha_um_assunto_so_continua_valendo(usuario):
    """Compatibilidade: a coluna guardava UMA chave ate o M9.3, e tem gente
    assinada assim na base agora."""
    db.update_user_fields(usuario["id"], podcast_nicho="ia")
    assert podcast.nichos_da_pessoa(db.get_user(usuario["id"])) == ["ia"]


def test_assunto_que_saiu_do_catalogo_nao_derruba_o_resto(usuario):
    db.update_user_fields(usuario["id"], podcast_nicho="futebol,extinto,moda")
    assert podcast.nichos_da_pessoa(
        db.get_user(usuario["id"])) == ["futebol", "moda"]


# ---------------------------------------------------------------------------
# 3. um audio por assunto, com o nome na frente
# ---------------------------------------------------------------------------

def test_manda_um_audio_por_assunto(usuario, com_voz, monkeypatch):
    monkeypatch.setattr(noticias, "buscar", _uma_noticia)
    monkeypatch.setattr(podcast, "locucao",
                        lambda *a, **k: "BIA: oi.\nLEO: oi.")
    audios = []
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: audios.append(a) or {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})

    _pronto(usuario, "futebol,economia,moda")
    responder("quero ouvir")
    assert len(audios) == 3, audios


def test_cada_audio_vai_com_o_nome_do_assunto_antes(usuario, com_voz,
                                                    monkeypatch):
    """Tres notas de voz seguidas sem legenda e a pessoa nao sabe qual e
    qual — o WhatsApp nao mostra titulo de audio."""
    monkeypatch.setattr(noticias, "buscar", _uma_noticia)
    monkeypatch.setattr(podcast, "locucao",
                        lambda *a, **k: "BIA: oi.\nLEO: oi.")
    textos = []
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: textos.append(t) or {"enviado": True})

    _pronto(usuario, "futebol,economia,moda")
    responder("quero ouvir")
    assert any("Futebol" in t and "1 de 3" in t for t in textos), textos
    assert any("Economia" in t and "2 de 3" in t for t in textos), textos
    assert any("Moda" in t and "3 de 3" in t for t in textos), textos


def test_com_um_assunto_so_nao_manda_legenda(usuario, com_voz, monkeypatch):
    """Com um assunto a legenda vira ruido: ela sabe o que pediu."""
    monkeypatch.setattr(noticias, "buscar", _uma_noticia)
    monkeypatch.setattr(podcast, "locucao",
                        lambda *a, **k: "BIA: oi.\nLEO: oi.")
    textos = []
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: textos.append(t) or {"enviado": True})

    _pronto(usuario, "futebol")
    responder("quero ouvir")
    assert not any("1 de 1" in t for t in textos), textos


def test_um_assunto_sem_noticia_nao_cancela_os_outros(usuario, com_voz,
                                                      monkeypatch):
    """A fonte de moda cair nao pode calar o episodio de futebol: era o
    comportamento antigo, um `return` no meio do caminho."""
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: [] if n == "moda" else _uma_noticia())
    monkeypatch.setattr(podcast, "locucao",
                        lambda k, itens, **kw: "BIA: oi.\nLEO: oi." if itens else "")
    audios = []
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: audios.append(a) or {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})

    _pronto(usuario, "moda,futebol")
    responder("quero ouvir")
    assert len(audios) == 1, audios


# ---------------------------------------------------------------------------
# 4. a janela de noticia e a frequencia escolhida
# ---------------------------------------------------------------------------

def test_o_teto_de_frequencia_e_cinco_dias():
    """"no maximo a cada 5 dias" — mais que isso multiplica proativa num
    numero que ja foi restringido duas vezes pela Meta."""
    assert min(db.FREQUENCIAS) == 5


def test_a_frequencia_padrao_e_semanal():
    assert db.FREQUENCIA_PADRAO == 7


@pytest.mark.parametrize("freq", [5, 7, 15, 30])
def test_a_janela_de_noticia_segue_a_frequencia(usuario, com_voz, monkeypatch,
                                                freq):
    """Quem ouve de mes em mes com janela de 8 dias perde tres semanas; quem
    ouve a cada 5 com a mesma janela ouve repetido."""
    visto = {}
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: visto.update(dias=k.get("dias")) or [])
    _pronto(usuario, "futebol")
    db.update_user_fields(usuario["id"], podcast_frequencia=str(freq))
    responder("quero ouvir")
    assert visto.get("dias") == freq, visto


def test_sem_escolha_a_janela_e_a_padrao(usuario):
    assert db.frequencia_do_podcast(
        db.get_user(usuario["id"])) == db.FREQUENCIA_PADRAO


@pytest.mark.parametrize("lixo", ["", None, "abc", "0", "-3", "999"])
def test_frequencia_invalida_cai_na_padrao(usuario, lixo):
    """A coluna e TEXT e passa por update administrativo. Valor estranho tem
    que virar semanal, nunca janela zero (que entregaria audio vazio)."""
    db.update_user_fields(usuario["id"], podcast_frequencia=lixo)
    assert db.frequencia_do_podcast(
        db.get_user(usuario["id"])) == db.FREQUENCIA_PADRAO


def test_a_janela_nunca_e_menor_que_um_dia(usuario, com_voz, monkeypatch):
    visto = {}
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: visto.update(dias=k.get("dias")) or [])
    _pronto(usuario, "futebol")
    responder("quero ouvir")
    assert (visto.get("dias") or 0) >= 1, visto


def test_o_parser_corta_pela_janela_pedida():
    """A janela e aplicada no corte por data, e o corte mora no parser — foi
    onde a primeira tentativa errou (parametro no `buscar`, uso no parser)."""
    agora = _dt.datetime(2026, 9, 1, 12, 0, 0)
    def item(dias_atras):
        d = agora - _dt.timedelta(days=dias_atras)
        return ("<item><title>N%d</title><link>http://x/%d</link>"
                "<pubDate>%s</pubDate></item>"
                % (dias_atras, dias_atras,
                   d.strftime("%a, %d %b %Y %H:%M:%S +0000")))
    xml = ("<rss><channel>" + item(2) + item(9) + item(25) + "</channel></rss>")

    curto = noticias.parse_feed(xml, "F", agora=agora, dias=5)
    longo = noticias.parse_feed(xml, "F", agora=agora, dias=30)
    assert len(curto) == 1, curto
    assert len(longo) == 3, longo


# ---------------------------------------------------------------------------
# 5. os tres farois do dash
# ---------------------------------------------------------------------------

def test_o_farol_conta_os_enviados_da_semana(usuario):
    """Farol 3: "quantos ja foram enviados por semana"."""
    for _ in range(4):
        db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    assert db.podcast_farois()["na_semana"] == 4


def test_o_farol_mede_o_tempo_medio(usuario):
    """Farol 2: "tempo medio dos audios"."""
    db.podcast_registrar_episodio(usuario["id"], "futebol", 120.0, True)
    db.podcast_registrar_episodio(usuario["id"], "moda", 140.0, True)
    assert db.podcast_farois()["segundos_medio"] == 130


def test_falha_nao_entra_na_media_de_tempo(usuario):
    """Episodio que nao saiu tem duracao 0 — deixar entrar puxaria a media
    pra baixo e o farol de tempo viraria um segundo farol de falha."""
    db.podcast_registrar_episodio(usuario["id"], "futebol", 120.0, True)
    db.podcast_registrar_episodio(usuario["id"], "moda", 0, False, "sem fonte")
    assert db.podcast_farois()["segundos_medio"] == 120


def test_tudo_saindo_acende_verde(usuario):
    """Farol 1: "se estao conseguindo puxar das fontes e gerar
    perfeitamente"."""
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    assert db.podcast_farois()["estado"] == "ok"


def test_uma_falha_ja_tira_do_verde(usuario):
    """Falha aqui e assunto que nao chegou, e a pessoa nao reclama de audio
    que nao veio — ela so cancela."""
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    db.podcast_registrar_episodio(usuario["id"], "moda", 0, False, "403")
    assert db.podcast_farois()["estado"] == "atencao"


def test_maioria_falhando_acende_vermelho(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    for _ in range(3):
        db.podcast_registrar_episodio(usuario["id"], "moda", 0, False, "403")
    assert db.podcast_farois()["estado"] == "quebrado"


def test_sem_episodio_nenhum_o_farol_diz_sem_dados():
    """"sem dados" e diferente de "quebrado": no primeiro dia depois do
    deploy o dash nao pode gritar vermelho sem motivo."""
    assert db.podcast_farois()["estado"] == "sem dados"


def test_episodio_velho_nao_conta_na_semana(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    with db.get_conn() as c:
        c.execute("UPDATE podcast_log SET quando=?",
                  ((tempo.agora() - _dt.timedelta(days=20)
                    ).strftime("%Y-%m-%d %H:%M:%S"),))
    assert db.podcast_farois(dias=7)["na_semana"] == 0


def test_o_farol_nao_carrega_dado_pessoal():
    """O /health e o /dash ja foram auditados por isso: farol e contagem,
    nao lista de gente."""
    for chave, valor in db.podcast_farois().items():
        assert isinstance(valor, (int, float, str)), (chave, valor)
        assert TELEFONE not in str(valor), chave


def test_a_geracao_alimenta_o_farol(usuario, com_voz, monkeypatch):
    """Farol so vale se o caminho real escreve nele — o resto seria um
    numero bonito medindo os proprios testes."""
    monkeypatch.setattr(noticias, "buscar", _uma_noticia)
    monkeypatch.setattr(podcast, "locucao",
                        lambda *a, **k: "BIA: oi.\nLEO: oi.")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})

    _pronto(usuario, "futebol,economia")
    responder("quero ouvir")
    assert db.podcast_farois()["na_semana"] == 2


def test_fonte_caida_aparece_como_falha_no_farol(usuario, com_voz,
                                                 monkeypatch):
    """O sintoma de fonte seca era ausencia de audio — e ausencia nao
    aparece em lugar nenhum. Agora aparece."""
    def caiu(n, **k):
        rel = k.get("relatorio")
        if rel is not None:
            rel["fontes"], rel["falharam"] = 3, 3
        return []
    monkeypatch.setattr(noticias, "buscar", caiu)
    _pronto(usuario, "futebol")
    responder("quero ouvir")
    assert db.podcast_farois()["falhas"] >= 1, db.podcast_farois()


def test_semana_quieta_nao_acende_o_farol(usuario, com_voz, monkeypatch):
    """"Prefiro nao te mandar audio so pra cumprir tabela" e o produto se
    comportando. Se isso acender laranja, o farol grita lobo toda semana e o
    Kevin aprende a ignora-lo — o oposto do motivo dele existir."""
    def quieta(n, **k):
        rel = k.get("relatorio")
        if rel is not None:
            rel["fontes"], rel["falharam"] = 3, 0
        return []
    monkeypatch.setattr(noticias, "buscar", quieta)
    _pronto(usuario, "futebol")
    responder("quero ouvir")
    f = db.podcast_farois()
    assert f["falhas"] == 0, f
    assert f["estado"] != "quebrado", f


def test_uma_fonte_de_tres_caindo_nao_e_assunto_mudo(usuario, com_voz,
                                                     monkeypatch):
    """Tres fontes existem pra isso. Uma caindo e o desenho funcionando."""
    def parcial(n, **k):
        rel = k.get("relatorio")
        if rel is not None:
            rel["fontes"], rel["falharam"] = 3, 1
        return []
    monkeypatch.setattr(noticias, "buscar", parcial)
    _pronto(usuario, "futebol")
    responder("quero ouvir")
    assert db.podcast_farois()["falhas"] == 0, db.podcast_farois()


def test_telemetria_quebrada_nao_derruba_nada(usuario, monkeypatch):
    """Derrubar a entrega do audio pra registrar que o audio foi entregue
    seria o cumulo. Farol e telemetria: nunca levanta."""
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "get_conn", explode)
    db.podcast_registrar_episodio(usuario["id"], "futebol", 10, True)
    assert db.podcast_farois()["estado"] == "sem dados"


# ---------------------------------------------------------------------------
# 6. os rotulos que ja tem "e" dentro
# ---------------------------------------------------------------------------

def test_rotulo_com_e_dentro_nao_vira_assunto_a_mais():
    """"Economia e seu bolso" e UM rotulo. Com a conjuncao da juncao a lista
    saía "futebol, economia e seu bolso e moda" — tres assuntos que o olho
    le como quatro."""
    texto = podcast.rotulos_da_pessoa("futebol,economia,moda")
    assert texto == "futebol, economia e seu bolso, moda", texto


def test_sem_rotulo_composto_a_conjuncao_continua():
    assert podcast.rotulos_da_pessoa("futebol,moda") == "futebol e moda"


def test_um_assunto_so_nao_ganha_virgula():
    assert podcast.rotulos_da_pessoa("futebol") == "futebol"


def test_a_landing_pode_mandar_rotulo_composto():
    """Quebrar por " e " antes de tentar o texto inteiro transformava o
    assunto mais escolhido da landing em NENHUM assunto, calado."""
    assert podcast.nichos_do_texto("Economia e seu bolso") == ["economia"]
    assert podcast.nichos_do_texto("Ciência e espaço") == ["ciencia"]
    assert podcast.nichos_do_texto("Saúde e bem-estar") == ["saude"]


@pytest.mark.parametrize("texto,esperado", [
    ("futebol, economia e moda", ["futebol", "economia", "moda"]),
    ("Economia e seu bolso, Futebol", ["economia", "futebol"]),
    ("Futebol e Moda", ["futebol", "moda"]),
    ("nada disso", []),
])
def test_a_landing_le_lista_de_assuntos(texto, esperado):
    assert podcast.nichos_do_texto(texto) == esperado


def test_a_landing_respeita_o_teto_de_tres():
    assert len(podcast.nichos_do_texto(
        "futebol, moda, carros, viagens, música")) == 3


def test_o_convite_de_quem_tem_tres_avisa_que_sao_tres(usuario):
    """Tres notas de voz seguidas sem aviso e um susto — o convite e o unico
    lugar onde da pra dizer isso antes."""
    c = podcast.convite("futebol,economia,moda", nome="Ana")
    assert "*3*" in c["texto"], c["texto"]
    assert "futebol" in c["texto"] and "moda" in c["texto"]


def test_o_convite_de_um_assunto_continua_no_singular():
    c = podcast.convite("futebol", nome="Ana")
    assert "mini podcast de *futebol*" in c["texto"], c["texto"]


def test_o_convite_some_sem_assunto():
    assert podcast.convite("", nome="Ana") is None
    assert podcast.convite("extinto", nome="Ana") is None


# ---------------------------------------------------------------------------
# 7. as fronteiras de palavra que estavam mortas (M9.13)
# ---------------------------------------------------------------------------
# Cada uma delas tinha o `\b` trocado por um byte de backspace: a alternativa
# nunca casava, e o sintoma era episodio com assunto trocado — sem erro, sem
# log. A guarda de classe mora em `test_fonte_sem_lixo.py`; aqui fica o
# COMPORTAMENTO de cada termo, que e o que a pessoa ouve.

def test_ciencia_reconhece_estrela():
    assert podcast.e_do_assunto(
        "ciencia", "Astrônomos observam estrela que explodiu na Via Láctea", "")


def test_ciencia_continua_recusando_chef_estrelado():
    """A fronteira existe POR CAUSA deste caso — e ela estava morta."""
    assert not podcast.e_do_assunto(
        "ciencia", "Chef estrelado abre restaurante em São Paulo", "")


def test_musica_reconhece_ep():
    assert podcast.e_do_assunto(
        "musica", "Banda lança EP com quatro faixas inéditas", "")


def test_musica_reconhece_feat():
    assert podcast.e_do_assunto(
        "musica", "Cantora grava feat com rapper americano", "")


def test_viagens_reconhece_ilha():
    assert podcast.e_do_assunto(
        "viagens", "Ilha de Fernando de Noronha limita visitantes", "")


def test_o_filtro_e_de_verdade_e_nao_aceita_tudo():
    """Se `e_do_assunto` passasse a devolver True pra qualquer coisa, todos
    os testes acima ficariam verdes sem medir nada."""
    assert not podcast.e_do_assunto(
        "ciencia", "Palmeiras vence o clássico e assume a liderança", "")


# ---------------------------------------------------------------------------
# 8. o fecho nomeia o que ela acabou de ouvir
# ---------------------------------------------------------------------------
# Achado na verificacao reversa: desfazer isto deixava a suite VERDE, e o
# fecho de quem tem tres assuntos saía "Seu resumo de ** está aí em cima" —
# rotulo vazio em negrito, na cara do cliente, sem erro nenhum. O `rotulo()`
# le chave unica e devolve "" pra "futebol,economia,moda".

def _fecho(usuario, monkeypatch, nichos, freq="7"):
    monkeypatch.setattr(noticias, "buscar", _uma_noticia)
    monkeypatch.setattr(podcast, "locucao",
                        lambda *a, **k: "BIA: oi.\nLEO: oi.")
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: {"enviado": True})
    _pronto(usuario, nichos)
    db.update_user_fields(usuario["id"], podcast_frequencia=freq)
    return responder("quero ouvir")


def test_o_fecho_nomeia_os_tres_assuntos(usuario, com_voz, monkeypatch):
    r = _fecho(usuario, monkeypatch, "futebol,economia,moda")
    for nome in ("futebol", "economia e seu bolso", "moda"):
        assert nome in r, (nome, r)


def test_o_fecho_nunca_sai_com_rotulo_vazio(usuario, com_voz, monkeypatch):
    """"Seu resumo de ** está aí em cima" e o sintoma exato do portao que
    le chave unica numa coluna que virou lista."""
    r = _fecho(usuario, monkeypatch, "futebol,economia,moda")
    assert "**" not in r, r
    assert "de *" in r, r


def test_o_fecho_de_um_assunto_continua_certo(usuario, com_voz, monkeypatch):
    r = _fecho(usuario, monkeypatch, "futebol")
    assert "*futebol*" in r, r


def test_o_fecho_lista_as_fontes_sem_repetir(usuario, com_voz, monkeypatch):
    """Com tres assuntos a lista de fontes dobrava e vinha com nome
    repetido."""
    r = _fecho(usuario, monkeypatch, "futebol,economia,moda")
    trecho = r.split("_Fontes: ")[1].split("._")[0]
    nomes = [x.strip() for x in trecho.split(",")]
    assert len(nomes) == len(set(nomes)), nomes


# ---------------------------------------------------------------------------
# 9. a contagem da legenda e dos que SAIRAM
# ---------------------------------------------------------------------------

def test_a_contagem_pula_o_assunto_que_nao_teve_noticia(usuario, com_voz,
                                                        monkeypatch):
    """Numerando pela posicao na lista, um assunto sem noticia fazia a pessoa
    receber "2 de 3" primeiro e procurar um "1 de 3" que nunca existiu."""
    monkeypatch.setattr(noticias, "buscar",
                        lambda n, **k: [] if n == "futebol" else _uma_noticia())
    monkeypatch.setattr(podcast, "locucao",
                        lambda k, itens, **kw: "BIA: oi.\nLEO: oi." if itens else "")
    textos = []
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: textos.append(t) or {"enviado": True})

    _pronto(usuario, "futebol,economia,moda")
    responder("quero ouvir")
    legendas = [t for t in textos if " de " in t and "*" in t]
    assert any("1 de" in t for t in legendas), legendas
    assert any("Economia" in t and "1 de" in t for t in legendas), legendas


def test_a_contagem_comeca_em_um_com_tudo_saindo(usuario, com_voz, monkeypatch):
    monkeypatch.setattr(noticias, "buscar", _uma_noticia)
    monkeypatch.setattr(podcast, "locucao",
                        lambda *a, **k: "BIA: oi.\nLEO: oi.")
    textos = []
    monkeypatch.setattr(wa_bot.wasender, "falar_audio",
                        lambda tel, a, **k: {"enviado": True})
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, t, **k: textos.append(t) or {"enviado": True})

    _pronto(usuario, "futebol,economia,moda")
    responder("quero ouvir")
    assert any("Futebol" in t and "1 de 3" in t for t in textos), textos
    assert any("Moda" in t and "3 de 3" in t for t in textos), textos
