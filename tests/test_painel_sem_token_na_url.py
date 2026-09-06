# -*- coding: utf-8 -*-
"""O token do painel sai da URL.

O painel e protegido por uma senha unica e eterna que viajava na barra de
endereco. Nao precisa de invasor pra isso vazar: o link do relatorio diario
chega todo dia no WhatsApp, o dono tira print da tela, e quem vir a imagem
entra no painel — ve os telefones da base, estende trial, dispara mensagem
em lote pra todo mundo.

Aqui o token vira cookie no primeiro acesso e o link do relatorio passa a
levar um token que vence sozinho. O que estes testes protegem:
  - o token nao aparece mais em URL nenhuma;
  - o cookie nao pode ser lido por JS nem viajar em requisicao de fora;
  - o token temporario vence, e cookie nao dura mais que o token que guarda;
  - e, acima de tudo, que continuar entrando ainda funciona.
"""
import base64
import re
import time

import pytest
from fastapi.testclient import TestClient

import wa_bot


MESTRE = "token-mestre-de-teste"


@pytest.fixture(autouse=True)
def _com_token(monkeypatch):
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", MESTRE)
    yield


@pytest.fixture
def cli():
    return TestClient(wa_bot.app)


def _cookie_cru(resp):
    return resp.headers.get("set-cookie", "")


def _max_age(resp):
    m = re.search(r"[Mm]ax-[Aa]ge=(\d+)", _cookie_cru(resp))
    return int(m.group(1)) if m else None


# --- o token sai da URL ------------------------------------------------

def test_token_na_url_vira_cookie_e_a_url_fica_limpa(cli):
    r = cli.get(f"/dash?k={MESTRE}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/dash"
    assert MESTRE not in r.headers["location"]
    assert MESTRE in _cookie_cru(r)


def test_a_segunda_visita_entra_sem_token_nenhum(cli):
    cli.get(f"/dash?k={MESTRE}")
    assert cli.get("/dash").status_code == 200


def test_o_painel_antigo_nao_reescreve_o_token_no_destino(cli):
    """Ele redirecionava pro /dash?k=... — devolvendo o token pra URL."""
    r = cli.get(f"/painel?k={MESTRE}", follow_redirects=False)
    assert r.status_code == 302
    assert "k=" not in r.headers["location"], r.headers["location"]


def test_a_saida_de_emergencia_tambem_limpa(cli):
    r = cli.get(f"/painel?antigo=1&k={MESTRE}", follow_redirects=False)
    assert r.status_code == 302
    assert "k=" not in r.headers["location"]
    assert MESTRE in _cookie_cru(r)
    assert cli.get("/painel?antigo=1").status_code == 200


def _relatorio_de_verdade(monkeypatch):
    """Monta o relatorio matinal INTEIRO e devolve o texto que sairia.

    A primeira versao deste teste lia a linha do link como texto do codigo
    e trocava `{token_temporario()}` por um token gerado. Com isso ela
    passava tambem quando o codigo dizia `{PAINEL_TOKEN}` — a linha
    literal nao contem o valor do token. Ou seja: o unico teste do
    vazamento que a gente esta consertando nao testava nada, e so o patch
    reverso denunciou. Agora ele gera a mensagem real.
    """
    enviados = []
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "https://x.example")
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999999999")
    # O relatorio so sai entre 8h e 12h, e SO UMA VEZ POR DIA — e o "uma vez"
    # fica gravado no banco, que sobrevive entre rodadas. Sem soltar as duas
    # travas, este teste passava na primeira execucao do dia e falhava em
    # todas as seguintes, parecendo defeito do codigo.
    manha = wa_bot.tempo.agora().replace(hour=9, minute=0)
    monkeypatch.setattr(wa_bot.tempo, "agora", lambda *a, **k: manha)
    monkeypatch.setattr(wa_bot.db, "dispatched_today", lambda *a, **k: False)
    monkeypatch.setattr(wa_bot.db, "log_dispatch", lambda *a, **k: None)
    monkeypatch.setattr(
        wa_bot, "_enviar_com_botao",
        lambda tel, txt, *a, **k: (enviados.append(txt), True)[1])
    wa_bot.relatorio_matinal()
    assert enviados, "o relatorio matinal nao chegou a montar mensagem"
    return enviados[0]


def _token_do_link(texto):
    achou = re.search(r"/dash\?k=(\S+)", texto)
    assert achou, f"link do painel nao encontrado em: {texto[:400]}"
    return achou.group(1)


def test_o_relatorio_diario_nao_leva_mais_o_token_mestre(monkeypatch):
    """O vazamento concreto: a mensagem chega todo dia no WhatsApp do dono
    e ele tira print dela. Quem vir a imagem entrava no painel pra sempre.
    """
    texto = _relatorio_de_verdade(monkeypatch)
    assert MESTRE not in texto, "o token mestre voltou pro link do dia"
    assert "/dash?k=" in texto, "o link do painel sumiu do relatorio"


def test_o_link_do_relatorio_abre_o_painel_de_verdade(monkeypatch):
    """Tirar o token e facil; o risco e o link do dia parar de abrir."""
    tok = _token_do_link(_relatorio_de_verdade(monkeypatch))
    assert TestClient(wa_bot.app).get(
        f"/dash?k={tok}", follow_redirects=False).status_code == 302


def test_o_link_do_relatorio_vence(monkeypatch):
    resta = wa_bot._vida_do_cookie(
        _token_do_link(_relatorio_de_verdade(monkeypatch)))
    assert 0 < resta <= wa_bot.LINK_DIAS * 86400, resta


# --- o cookie ---------------------------------------------------------

def test_o_cookie_nao_pode_ser_lido_por_javascript(cli):
    """Sem HttpOnly, um XSS no painel leva o token junto."""
    r = cli.get(f"/dash?k={MESTRE}", follow_redirects=False)
    assert "httponly" in _cookie_cru(r).lower()


def test_o_cookie_nao_viaja_em_requisicao_de_fora(cli):
    """Trocar header por cookie abre CSRF. SameSite=Strict e o que fecha.

    Sem isto, um site qualquer que o dono abrisse poderia postar em
    /painel/lote e disparar mensagem pra base inteira usando o cookie
    dele — sem nunca ver o token.
    """
    assert "strict" in _cookie_cru(
        cli.get(f"/dash?k={MESTRE}", follow_redirects=False)).lower()


def test_em_https_o_cookie_so_anda_em_https(cli):
    r = TestClient(wa_bot.app, base_url="https://x.example").get(
        f"/dash?k={MESTRE}", follow_redirects=False)
    assert "secure" in _cookie_cru(r).lower()


def test_em_http_local_o_cookie_ainda_funciona(cli):
    """Marcar Secure em http faria o navegador descartar o cookie."""
    r = cli.get(f"/dash?k={MESTRE}", follow_redirects=False)
    assert "secure" not in _cookie_cru(r).lower()


def test_cookie_nao_dura_mais_que_o_token_que_guarda(cli):
    """Cookie vivo com token morto vira "o painel parou de abrir"."""
    curto = wa_bot.token_temporario(3)
    r = cli.get(f"/dash?k={curto}", follow_redirects=False)
    assert _max_age(r) <= 3 * 86400 + 60
    mestre = TestClient(wa_bot.app).get(
        f"/dash?k={MESTRE}", follow_redirects=False)
    assert _max_age(mestre) == wa_bot.COOKIE_DIAS * 86400


def test_cookie_com_token_vencido_nao_entra(cli):
    cli.cookies.set(wa_bot.COOKIE_PAINEL, wa_bot.token_temporario(-1))
    assert cli.get("/dash").status_code == 401


def test_cookie_forjado_nao_entra(cli):
    cli.cookies.set(wa_bot.COOKIE_PAINEL, "eu-inventei-esse")
    assert cli.get("/dash").status_code == 401


def test_o_cookie_vale_no_painel_inteiro(cli):
    """path=/ ou as rotas de acao ficariam de fora e o painel quebra."""
    assert "path=/" in _cookie_cru(
        cli.get(f"/dash?k={MESTRE}", follow_redirects=False)).lower()


# --- o token temporario ------------------------------------------------

def test_o_temporario_abre_o_painel(cli):
    r = cli.get(f"/dash?k={wa_bot.token_temporario(8)}",
                follow_redirects=False)
    assert r.status_code == 302


def test_o_temporario_vence(cli):
    assert not wa_bot._token_temporario_valido(wa_bot.token_temporario(-1))
    cli.cookies.clear()
    assert cli.get(
        f"/dash?k={wa_bot.token_temporario(-1)}").status_code == 401


def test_adulterar_a_assinatura_invalida(cli):
    bom = wa_bot.token_temporario(8)
    assert not wa_bot._token_temporario_valido(bom[:-6] + "AAAAAA")


def test_nao_da_pra_esticar_a_validade(cli):
    """Trocar a data sem a chave tem que quebrar a assinatura."""
    n = wa_bot.TAM_ASSINATURA
    bom = base64.urlsafe_b64decode(wa_bot.token_temporario(1).encode())
    assinatura = bom[-n:]
    longe = str(int(time.time()) + 999 * 86400).encode()
    forjado = base64.urlsafe_b64encode(longe + assinatura).decode()
    assert not wa_bot._token_temporario_valido(forjado)


def test_o_temporario_nao_revela_o_mestre(cli):
    t = wa_bot.token_temporario(8)
    assert MESTRE not in t
    assert MESTRE not in base64.urlsafe_b64decode(t.encode()).decode(
        "latin-1")


def test_temporario_de_outra_chave_nao_serve(cli, monkeypatch):
    """Se o token mestre for trocado, os links antigos morrem juntos."""
    alheio = wa_bot.token_temporario(8)
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "outro-token-completamente")
    assert not wa_bot._token_temporario_valido(alheio)


def test_lixo_no_lugar_do_token_nao_levanta(cli):
    for porcaria in ("", "abc", "!!!", "a" * 5000, "../../etc/passwd"):
        assert not wa_bot._token_temporario_valido(porcaria)


# --- o que nao pode ter mudado ----------------------------------------

def test_sem_token_nenhum_continua_barrado(cli):
    assert cli.get("/dash").status_code == 401
    assert cli.get("/painel").status_code == 401


def test_token_errado_continua_barrado(cli):
    assert cli.get("/dash?k=chute").status_code == 401


def test_o_header_do_js_continua_valendo(cli):
    """O painel ja aberto chama a API por header. Se isso quebrar, todos
    os botoes da tela param — e o teste tem que gritar antes do deploy."""
    r = cli.get("/api/pulso", headers={"X-Painel-Token": MESTRE})
    assert r.status_code == 200


def test_o_js_manda_k_vazio_e_o_cookie_salva(cli):
    """Depois da limpeza da URL o JS monta `?k=` vazio. E o caso real."""
    cli.get(f"/dash?k={MESTRE}")
    assert cli.get("/api/pulso?k=").status_code == 200


def test_sem_PAINEL_TOKEN_tudo_fecha(cli, monkeypatch):
    """Fail-closed: sem senha configurada, ninguem entra — nem por cookie."""
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "")
    cli.cookies.set(wa_bot.COOKIE_PAINEL, MESTRE)
    assert cli.get("/dash").status_code == 401
    assert wa_bot.token_temporario() == ""


def test_todo_token_gerado_e_valido():
    """O que quase escapou: assinatura e bytes crus, e o separador que eu
    usava (`.`) podia cair DENTRO dela. Cerca de 6% dos tokens nasciam
    invalidos — um em cada quinze links do relatorio diario nao abriria,
    sem sintoma nenhum que apontasse pra causa. Um unico token testado
    passa quase sempre; so o volume denuncia.
    """
    ruins = [t for t in (wa_bot.token_temporario(8) for _ in range(3000))
             if not wa_bot._token_temporario_valido(t)]
    assert not ruins, f"{len(ruins)} de 3000 tokens nasceram invalidos"


def test_a_vida_do_cookie_nunca_falha_por_causa_do_token():
    """Mesmo defeito, outra porta: o calculo do prazo lia o token igual."""
    for _ in range(2000):
        t = wa_bot.token_temporario(8)
        assert 7 * 86400 < wa_bot._vida_do_cookie(t) <= 8 * 86400


# --- o que a auditoria pegou ------------------------------------------

def test_link_velho_nao_derruba_a_sessao_boa(cli):
    """O BLOQUEADOR da auditoria, e a regressao mais cara possivel.

    O dono rola o WhatsApp, toca no link do relatorio de nove dias atras.
    A primeira versao gravava esse `?k=` vencido no cookie sem conferir —
    e a entrada ja tinha sido autorizada PELO cookie. Resultado: Max-Age=0,
    cookie apagado, painel em 401 sem pista nenhuma de por que.
    """
    cli.get(f"/dash?k={MESTRE}")
    assert cli.get("/dash").status_code == 200
    cli.get(f"/dash?k={wa_bot.token_temporario(-1)}")
    assert cli.get("/dash").status_code == 200, "o link velho matou a sessao"


def test_k_lixo_nao_derruba_a_sessao_boa(cli):
    """Pior que o vencido: lixo virava cookie de 30 dias guardando lixo."""
    cli.get(f"/dash?k={MESTRE}")
    for porcaria in ("LIXO", "", "a" * 3000, "../../etc/passwd"):
        cli.get(f"/dash?k={porcaria}")
        assert cli.get("/dash").status_code == 200, porcaria


def test_k_invalido_nem_encosta_no_cookie(cli):
    r = cli.get("/dash?k=nao-vale-nada", follow_redirects=False)
    assert "set-cookie" not in r.headers, r.headers.get("set-cookie")


def test_token_com_acento_nao_vira_500(cli):
    """`compare_digest` com duas str levanta TypeError em nao-ASCII.

    O /health e publico: bastava `?k=é` pra virar 500 e simular "bot
    caido" pro monitoramento, sem token nenhum.
    """
    for feio in ("é", "café", "ção", "日本"):
        # A query e a unica via em que nao-ASCII chega de verdade: header e
        # cookie sao latin-1 e o proprio cliente recusa antes de enviar.
        assert cli.get("/dash", params={"k": feio}).status_code == 401, feio
        assert cli.get("/health", params={"k": feio}).status_code == 200
        # E a funcao em si, que e onde o TypeError nascia.
        assert wa_bot._credencial_vale(feio) is False, feio


def test_token_mestre_com_acento_ainda_abre(cli, monkeypatch):
    """Fechar o TypeError nao pode ter quebrado quem usa acento na senha."""
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "senha-com-açã0")
    c = TestClient(wa_bot.app)
    assert c.get("/dash?k=senha-com-açã0",
                 follow_redirects=False).status_code == 302
    assert c.get("/dash").status_code == 200


def test_k_errado_nao_esconde_header_valido(cli):
    """Encadear as tres fontes com `or` fazia o `?k=` mascarar o header."""
    r = cli.get("/api/pulso?k=", headers={"X-Painel-Token": MESTRE})
    assert r.status_code == 200
    r2 = cli.get("/api/pulso?k=errado", headers={"X-Painel-Token": MESTRE})
    assert r2.status_code == 200, "o ?k= errado escondeu o header bom"


def test_atras_do_proxy_https_o_cookie_sai_secure(cli):
    """Em producao o proxy e outro container, entao o uvicorn ignora o
    X-Forwarded-Proto e o scheme chega "http" — o cookie com o token sairia
    sem Secure mesmo o dono acessando por https."""
    r = cli.get(f"/dash?k={MESTRE}", follow_redirects=False,
                headers={"X-Forwarded-Proto": "https"})
    assert "secure" in _cookie_cru(r).lower()
    r2 = cli.get(f"/dash?k={MESTRE}", follow_redirects=False,
                 headers={"X-Forwarded-Proto": "https, http"})
    assert "secure" in _cookie_cru(r2).lower()
    r3 = cli.get(f"/dash?k={MESTRE}", follow_redirects=False,
                 headers={"X-Forwarded-Proto": "http"})
    assert "secure" not in _cookie_cru(r3).lower()


def test_o_watchdog_exige_token(cli):
    """A unica rota GET que ESCREVE, e a unica sem teste nenhum.

    Apagar a trava dela deixava a suite inteira verde. Ela grava falhas no
    banco e dispara WhatsApp pro dono.
    """
    for metodo in (cli.get, cli.post):
        assert metodo("/watchdog").status_code == 401
        assert metodo("/watchdog?k=errado").status_code == 401
    assert cli.get(f"/watchdog?k={MESTRE}").status_code == 200


def test_dias_do_ambiente_nao_derruba_o_bot(monkeypatch):
    """Variavel torta no EasyPanel matava o import do processo inteiro —
    o bot ficaria mudo pra 15 pessoas por causa de config de painel."""
    for ruim in ("", "  ", "trinta", "30 dias", "0", "-5", "3,5"):
        monkeypatch.setenv("X_TESTE_DIAS", ruim)
        assert wa_bot._dias_do_ambiente("X_TESTE_DIAS", 8) == 8, ruim
    monkeypatch.setenv("X_TESTE_DIAS", " 12 ")
    assert wa_bot._dias_do_ambiente("X_TESTE_DIAS", 8) == 12


def _alerta_de_verdade(monkeypatch):
    """Dispara o alerta do motor e devolve a mensagem que sairia."""
    enviadas = []
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999999999")
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "")
    monkeypatch.setattr(wa_bot, "PAINEL_URL_DICA",
                        f"https://x.example/painel?k={MESTRE}")
    monkeypatch.setattr(wa_bot.wasender, "send_text",
                        lambda tel, txt, *a, **k: enviadas.append(txt))
    wa_bot._ALERTAS_ENVIADOS.clear()
    wa_bot._alertar_dono("motor caiu no teste", "5511988887777", "oi")
    assert enviadas, "o alerta nao chegou a montar mensagem"
    return enviadas[0]


def test_o_alerta_de_falha_nao_leva_o_token_mestre(monkeypatch):
    """A segunda porta do MESMO vazamento.

    O alerta do motor tambem manda link do painel, e ele saia da variavel
    `PAINEL_URL` — que, se tiver `?k=<token mestre>`, entrega a senha
    eterna por WhatsApp do mesmo jeito que o relatorio entregava.

    Este teste checava `link_do_painel()` isolada, e por isso passava mesmo
    com o alerta ainda usando a variavel crua. Agora dispara o alerta.
    """
    msg = _alerta_de_verdade(monkeypatch)
    assert MESTRE not in msg, "o token mestre sai no alerta de falha"
    assert "/dash?k=" in msg, msg


def test_o_link_do_alerta_abre_o_painel(monkeypatch):
    tok = _token_do_link(_alerta_de_verdade(monkeypatch))
    assert TestClient(wa_bot.app).get(
        f"/dash?k={tok}", follow_redirects=False).status_code == 302


def test_sem_endereco_nenhum_o_alerta_nao_inventa_link(monkeypatch):
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "")
    monkeypatch.setattr(wa_bot, "PAINEL_URL_DICA", "veja /painel?k=SEU_TOKEN")
    assert wa_bot.link_do_painel() == "veja /painel?k=SEU_TOKEN"


# --- nada publico por padrao ------------------------------------------

def test_health_sem_credencial_nao_entrega_reconhecimento(cli):
    """O /health devolvia um mapa da operacao pra qualquer um.

    Nada ali e credencial, e tudo ali e reconhecimento: tamanho e ritmo da
    base, nomes dos templates, quais recursos estao ligados, se existe
    alerta armado, qual build esta rodando. Quem prepara um ataque comeca
    exatamente por isso.
    """
    corpo = cli.get("/health").json()
    proibidos = ("envio", "freio", "reativacao", "ciclo", "sem_responder",
                 "templates", "templates_detalhe", "audio", "painel",
                 "alerta_dono", "podcast", "trial_days", "instance",
                 "v8_ultima_falha", "llm", "memoria", "contexto")
    vazou = [c for c in proibidos if c in corpo]
    assert not vazou, f"o /health publico ainda entrega: {vazou}"


def test_health_publico_ainda_serve_pro_monitor(cli):
    """Se o monitor externo parar de ver 200, ele acusa bot caido."""
    r = cli.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
    # `build` fica de proposito: responder "o deploy subiu?" sem abrir nada
    # ja evitou tempo perdido, e nao diz nada sobre a operacao.
    assert r.json().get("build")


def test_health_com_credencial_continua_completo(cli):
    """Tirar do publico nao pode ter tirado do dono."""
    corpo = cli.get(f"/health?k={MESTRE}").json()
    for c in ("envio", "ciclo", "templates", "audio", "painel"):
        assert c in corpo, c


def test_o_cookie_ja_abre_o_health_completo(cli):
    """O navegador do dono ja manda credencial depois da primeira visita —
    entao pra ELE o /health continua completo sem fazer nada."""
    cli.get(f"/dash?k={MESTRE}")
    assert "ciclo" in cli.get("/health").json()


def test_o_health_nao_muda_de_forma_conforme_quem_pergunta(cli):
    """`templates` tinha dois esquemas: com credencial virava outro dicio-
    nario, e quem lia a chave publica levava KeyError."""
    publico = cli.get(f"/health?k={MESTRE}").json()["templates"]
    assert "faltando" in publico and "liberados" in publico


# --- alarme e freio ----------------------------------------------------

@pytest.fixture(autouse=True)
def _freio_zerado():
    """O freio conta por par TCP, e no teste TODO mundo e "testclient".

    Sem zerar entre os testes, qualquer um que erre o token de proposito
    deixa a contagem (ou o bloqueio) de heranca pro seguinte, e o proximo
    falha por 429 sem ter nada a ver com o que ele testa.
    """
    wa_bot._RECUSAS.clear()
    wa_bot._BLOQUEADOS.clear()
    yield
    wa_bot._RECUSAS.clear()
    wa_bot._BLOQUEADOS.clear()


@pytest.fixture
def limpo_o_freio():
    wa_bot._RECUSAS.clear()
    wa_bot._BLOQUEADOS.clear()
    yield
    wa_bot._RECUSAS.clear()
    wa_bot._BLOQUEADOS.clear()


@pytest.fixture
def avisos(monkeypatch):
    caixa = []
    # Guarda motivo E texto: a origem saiu do `motivo` pra nao deixar o
    # atacante escolher a chave de repeticao do alerta.
    monkeypatch.setattr(
        wa_bot, "_alertar_dono",
        lambda motivo, tel, txt: caixa.append(f"{motivo} | {txt}"))
    return caixa


def test_ninguem_era_avisado_de_que_batem_na_porta(cli, limpo_o_freio, avisos):
    """As tentativas viravam uma linha de log dentro do container do
    EasyPanel, que e canvas e ninguem le. Descobrir que estao batendo na
    porta depois de terem entrado nao serve pra nada.
    """
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR):
        cli.get("/dash?k=errado")
    assert len(avisos) == 1, avisos
    assert "painel" in avisos[0].lower()


def test_o_alarme_nao_toca_por_dedo_errado(cli, limpo_o_freio, avisos):
    """Alarme que toca a toa e alarme que a pessoa silencia."""
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR - 1):
        cli.get("/dash?k=errado")
    assert avisos == []


def test_o_alarme_nao_repete_a_cada_tentativa(cli, limpo_o_freio, avisos):
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR * 3):
        cli.get("/dash?k=errado")
    assert len(avisos) == 1, f"tocou {len(avisos)} vezes"


def test_o_alarme_nao_leva_o_token_tentado(cli, limpo_o_freio, avisos):
    """Registrar o que tentaram e criar um arquivo de senhas quase certas."""
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR):
        cli.get("/dash?k=quase-o-token-certo-123")
    assert "quase-o-token-certo" not in " ".join(avisos)


def test_insistir_demais_fecha_a_porta(cli, limpo_o_freio, avisos):
    """Forca bruta sairia de graca: o token e a UNICA barreira do painel,
    e o painel dispara mensagem pra base inteira."""
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
        cli.get("/dash?k=errado")
    assert cli.get("/dash?k=outro-chute").status_code == 429


def test_o_dono_nunca_fica_trancado_do_lado_de_fora(cli, limpo_o_freio,
                                                    avisos):
    """O RISCO REAL desta protecao.

    O freio conta por origem, e atras do proxy pode nao haver origem
    separada — sem `X-Forwarded-For` o mundo inteiro vira um IP so. Se o
    bloqueio valesse antes da credencial, o primeiro atacante trancaria o
    DONO junto: perder o painel por causa da protecao do painel.
    """
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR * 2):
        cli.get("/dash?k=errado")
    assert cli.get("/dash?k=outro-chute").status_code == 429
    assert cli.get(f"/dash?k={MESTRE}",
                   follow_redirects=False).status_code == 302


def test_acertar_NAO_zera_o_contador(cli, limpo_o_freio, avisos):
    """O contrario do que esta funcao pedia antes, e de proposito.

    Zerar ao acertar parecia gentileza com o dono. Atras do proxy a origem
    e a MESMA pra todo mundo, entao o dono entrando destrancava o atacante
    junto — o freio virava "20 tentativas por login do dono". E zerar
    nunca foi necessario: a credencial e conferida ANTES do bloqueio, logo
    o dono nao depende disso pra entrar.
    """
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR - 1):
        cli.get("/dash?k=errado")
    antes = len(wa_bot._RECUSAS.get("testclient", []))
    cli.get(f"/dash?k={MESTRE}")
    assert len(wa_bot._RECUSAS.get("testclient", [])) == antes


def test_girar_o_header_nao_escapa_do_freio(cli, limpo_o_freio, avisos):
    """O achado da auditoria, e o mais importante deste bloco.

    A chave do freio era `X-Forwarded-For`, que o CLIENTE escolhe. Girando
    o header a cada tentativa, toda origem ficava com uma recusa so: nunca
    chegava nas 5 que avisam o dono nem nas 20 que bloqueiam. Forca bruta
    ilimitada e silenciosa — o freio e o alarme caiam pelo mesmo one-liner,
    e a suite inteira continuava verde.

    Chave que o cliente escolhe nao e chave.
    """
    for i in range(wa_bot.RECUSAS_ATE_BLOQUEAR + 5):
        cli.get("/dash?k=errado",
                headers={"X-Forwarded-For": f"1.2.3.{i}"})
    assert cli.get("/dash?k=x",
                   headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 429
    assert avisos, "girando o header, o dono nao era avisado de nada"


def test_o_alerta_diz_de_onde_o_sujeito_alega_vir(cli, limpo_o_freio,
                                                  avisos):
    """O header nao decide nada, mas o dono precisa de por onde comecar."""
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR):
        cli.get("/dash?k=errado", headers={"X-Forwarded-For": "203.0.113.7"})
    assert "203.0.113.7" in " ".join(avisos), avisos


def test_o_contador_nao_cresce_sem_fim(cli, limpo_o_freio, avisos):
    """Memoria de processo com chave vinda de fora e vazamento de memoria
    esperando acontecer."""
    for i in range(wa_bot.MAX_ORIGENS_LEMBRADAS + 300):
        cli.get("/dash?k=x",
                headers={"X-Forwarded-For": f"10.0.{i // 255}.{i % 255}"})
    # Expiracao sozinha nao segura: num burst nenhuma origem venceu ainda.
    assert len(wa_bot._RECUSAS) <= wa_bot.MAX_ORIGENS_LEMBRADAS + 1, len(
        wa_bot._RECUSAS)


def test_encher_de_origens_nao_solta_quem_esta_bloqueado(cli, limpo_o_freio,
                                                        avisos):
    """A limpeza nao pode virar a saida do bloqueio: bastaria mandar 500
    origens falsas pra apagar a propria ficha e voltar a tentar."""
    meu = {"X-Forwarded-For": "1.2.3.4"}
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
        cli.get("/dash?k=errado", headers=meu)
    for i in range(wa_bot.MAX_ORIGENS_LEMBRADAS + 300):
        cli.get("/dash?k=x",
                headers={"X-Forwarded-For": f"10.0.{i // 255}.{i % 255}"})
    assert cli.get("/dash?k=errado", headers=meu).status_code == 429


def test_o_alarme_quebrado_nao_derruba_a_resposta(cli, limpo_o_freio,
                                                  monkeypatch):
    """Se avisar o dono falhar, o 401 tem que sair do mesmo jeito."""
    def _explode(*a, **k):
        raise RuntimeError("sem whatsapp")
    monkeypatch.setattr(wa_bot, "_alertar_dono", _explode)
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR):
        assert cli.get("/dash?k=errado").status_code == 401


# --- a lista de clientes nao sai inteira -------------------------------

def test_o_pulso_nao_manda_mais_a_lista_de_telefones(cli, limpo_o_freio):
    """O dado mais sensivel que existe aqui — a lista de clientes de um
    produto de WhatsApp — saia COMPLETA a cada 20 segundos, e a tela nunca
    usou: o JS faz `slice(-4)` e mostra so o final.
    """
    texto = cli.get(f"/api/pulso?k={MESTRE}").text
    inteiros = re.findall(r"\b(?:55)?\d{10,13}\b", texto)
    assert not inteiros, f"telefone completo no pulso: {inteiros[:3]}"


def test_o_final_do_numero_continua_aparecendo(cli, limpo_o_freio,
                                              monkeypatch):
    """Mascarar nao pode ter cegado o dono: e assim que ele reconhece quem
    e quem na lista.

    A primeira versao exigia "…" em TODO usuario. Passava sozinha e falhava
    na suite completa, porque outro teste deixa usuario sem telefone no
    banco — e ai o campo e vazio, nao mascarado. Teste dependente de ordem
    e teste que mente: acusa defeito onde nao existe.
    """
    # O usuario e posto na mao: o banco de teste comeca vazio, entao ler o
    # que estiver la faria o teste passar sem testar nada (foi o que
    # aconteceu — a lista vinha vazia e o `for` nao rodava nenhuma vez).
    monkeypatch.setattr(
        wa_bot.db, "admin_list_users",
        lambda *a, **k: [{"id": 1, "nome": "Fulano",
                          "telefone": "5511988887777", "status": "trial",
                          "dias_trial_restantes": 3, "n_itens": 2}])
    u = cli.get(f"/api/pulso?k={MESTRE}").json()["usuarios"][0]
    assert u["telefone"] == "…7777", u
    assert "5511988887777" not in str(u)


def test_mascarar_nao_mexe_no_resto(cli, limpo_o_freio):
    j = cli.get(f"/api/pulso?k={MESTRE}").json()
    for c in ("build", "metricas", "serie", "financeiro", "usuarios"):
        assert c in j, c


def test_a_mascara_aguenta_dado_torto():
    """Telefone nulo, numero em vez de texto, dicionario aninhado."""
    assert wa_bot._sem_telefone_inteiro(
        {"telefone": None})["telefone"] is None
    assert wa_bot._sem_telefone_inteiro(
        {"telefone": 5511988887777})["telefone"] == "…7777"
    fundo = wa_bot._sem_telefone_inteiro(
        {"a": [{"b": {"telefone": "5511988887777"}}]})
    assert fundo["a"][0]["b"]["telefone"] == "…7777"


def test_a_lista_de_bloqueados_tambem_tem_teto(cli, limpo_o_freio, avisos):
    """Mesma memoria escolhida pelo cliente, so que mais cara de encher."""
    for i in range(60):
        origem = {"X-Forwarded-For": f"7.7.{i // 255}.{i % 255}"}
        for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
            cli.get("/dash?k=errado", headers=origem)
    assert len(wa_bot._BLOQUEADOS) <= wa_bot.MAX_ORIGENS_LEMBRADAS + 1
    for k, v in wa_bot._BLOQUEADOS.items():
        assert v > 0, k


# --- toda rota protegida tem que ser VISTA negando --------------------

#: Toda rota que exige credencial, com o verbo pra bater nela de verdade.
#: `test_toda_rota_ou_e_publica_declarada_ou_esta_na_lista_testada` cobra
#: que nada fique de fora desta lista sem estar declarado em `PUBLICAS`.
_ROTAS_PROTEGIDAS = [
    ("POST", "/cron/proactive"),
    ("GET", "/api/pulso"),
    ("POST", "/painel/custos"),
    ("POST", "/painel/metas"),
    ("POST", "/painel/resgatar"),
    ("POST", "/painel/conselho"),
    ("POST", "/painel/acao"),
    ("POST", "/painel/lote"),
    ("POST", "/painel/jornada/reabrir"),
    ("GET", "/watchdog"),
    ("POST", "/watchdog"),
    ("GET", "/dash"),
    ("GET", "/painel"),
]


@pytest.mark.parametrize("metodo,rota", _ROTAS_PROTEGIDAS)
def test_rota_protegida_recusa_sem_credencial(cli, limpo_o_freio, metodo,
                                              rota):
    """A auditoria instrumentou `_negado` numa rodada inteira e descobriu
    que SEIS rotas protegidas nunca eram vistas negando por teste nenhum —
    entre elas `/cron/proactive`, um POST que roda `dispatch_proactive()` e
    faz o bot mandar mensagem pra base toda. Apagar a trava dela deixava a
    suite inteira verde.

    As guardas existiam e funcionavam; o buraco era de teste. Guarda sem
    teste e guarda que some na proxima refatoracao sem ninguem notar.
    """
    chamar = cli.get if metodo == "GET" else cli.post
    assert chamar(rota).status_code == 401, f"{metodo} {rota} entrou sem token"
    assert chamar(f"{rota}?k=chute").status_code == 401


#: As rotas que podem ser abertas sem credencial, e o porque de cada uma.
PUBLICAS = {
    "/": "landing",
    "/health": "o monitor externo bate aqui sem token, de proposito",
    "/webhook": "a Meta chama; a porta e a assinatura HMAC dela",
}


def test_toda_rota_ou_e_publica_declarada_ou_esta_na_lista_testada():
    """Rota nova sem trava nao pode passar batido.

    A versao anterior lia o CODIGO da rota e aceitava a palavra
    "assinatura" em qualquer lugar dela — comentario servia, ramo morto
    servia. Foi exatamente por isso que o `/webhook` passou verde enquanto
    o ramo que recusa assinatura levantava NameError e devolvia 500.
    Palavra no fonte nao e prova de comportamento.

    Agora a prova e a resposta HTTP: toda rota nao-publica precisa estar
    na lista de `test_rota_protegida_recusa_sem_credencial`, que bate nela
    de verdade e exige 401. E o `re` cobre todos os verbos, nao so
    get/post — `put`/`delete`/`patch`/`api_route` passariam despercebidos.
    """
    import inspect
    fonte = inspect.getsource(wa_bot)
    rotas = set(re.findall(
        r'@app\.(?:get|post|put|patch|delete|head|options|api_route|'
        r'websocket)\(\s*"([^"]+)"', fonte))
    assert len(rotas) > 10, "nao achei as rotas; o padrao de decorador mudou"

    testadas = {r for _, r in _ROTAS_PROTEGIDAS}
    faltando = rotas - set(PUBLICAS) - testadas
    assert not faltando, (
        f"rotas sem prova de trava: {sorted(faltando)}. "
        f"Ou poe em _ROTAS_PROTEGIDAS, ou declara em PUBLICAS com o motivo.")


def test_nenhuma_publica_virou_fantasma():
    """Rota removida do app tem que sair das listas, senao elas incham com
    excecoes que nao valem mais pra nada."""
    import inspect
    fonte = inspect.getsource(wa_bot)
    rotas = set(re.findall(
        r'@app\.(?:get|post|put|patch|delete|head|options|api_route|'
        r'websocket)\(\s*"([^"]+)"', fonte))
    for r in PUBLICAS:
        assert r in rotas, f"{r} esta em PUBLICAS mas nao existe mais"


# --- os tetos, testados na funcao (o HTTP so tem uma origem) ----------

class _Falso:
    """Requisicao de mentira com IP escolhido.

    Pelo HTTP nao da pra testar isto: agora que a chave e o par TCP de
    verdade, TODO cliente de teste e "testclient" e o dicionario nunca
    passa de uma entrada. Simular varias origens exige falar direto com a
    funcao — que e exatamente o que um botnet faria.
    """

    def __init__(self, ip):
        self.client = type("c", (), {"host": ip})()
        self.headers = {}
        self.query_params = {}
        self.cookies = {}


def test_o_contador_de_origens_tem_teto(_freio_zerado):
    """Sem teto, muitas origens enchem a memoria do processo — a protecao
    viraria a forma de derrubar o bot."""
    for i in range(wa_bot.MAX_ORIGENS_LEMBRADAS + 400):
        wa_bot._anotar_recusa(_Falso(f"10.{i // 65536}.{i // 256 % 256}."
                                     f"{i % 256}"))
    assert len(wa_bot._RECUSAS) <= wa_bot.MAX_ORIGENS_LEMBRADAS + 1, len(
        wa_bot._RECUSAS)


def test_a_lista_de_bloqueados_tem_teto(_freio_zerado, monkeypatch):
    monkeypatch.setattr(wa_bot, "_alertar_dono", lambda *a, **k: None)
    for i in range(wa_bot.MAX_ORIGENS_LEMBRADAS + 200):
        req = _Falso(f"11.{i // 65536}.{i // 256 % 256}.{i % 256}")
        for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
            wa_bot._anotar_recusa(req)
    assert len(wa_bot._BLOQUEADOS) <= wa_bot.MAX_ORIGENS_LEMBRADAS + 1, len(
        wa_bot._BLOQUEADOS)


def test_encher_de_origens_nao_solta_quem_ja_estava_bloqueado(_freio_zerado,
                                                              monkeypatch):
    """A limpeza nao pode virar a saida do bloqueio."""
    monkeypatch.setattr(wa_bot, "_alertar_dono", lambda *a, **k: None)
    alvo = _Falso("1.2.3.4")
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
        wa_bot._anotar_recusa(alvo)
    assert wa_bot._esta_bloqueado(alvo)
    for i in range(wa_bot.MAX_ORIGENS_LEMBRADAS + 400):
        wa_bot._anotar_recusa(_Falso(f"12.{i // 256 % 256}.{i % 256}.1"))
    assert wa_bot._esta_bloqueado(alvo), "encheu a lista e escapou"


# --- os dois defeitos menores da auditoria ----------------------------

def test_token_fora_do_latin1_nao_derruba_o_login(cli, monkeypatch):
    """O header `Set-Cookie` e serializado em latin-1.

    Um token com € ou emoji levantava UnicodeEncodeError DEPOIS de a
    autenticacao ter passado: 500 na cara do dono no exato momento em que
    ele acertou a senha. `_credencial_vale` ja tinha sido blindado contra
    nao-ASCII; este era o passo seguinte, que ficou de fora.
    """
    for tok in ("token-com-€", "token-com-🔒"):
        monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", tok)
        c = TestClient(wa_bot.app)
        # Sem redirecionar: tirar o token da URL sem por cookie no lugar
        # levaria o dono direto pro 401 depois de ele ACERTAR a senha.
        # Fica pior pra privacidade e melhor pra nao perder o painel.
        assert c.get(f"/dash?k={tok}").status_code == 200, tok


def test_PAINEL_URL_torta_nao_monta_link_quebrado(monkeypatch):
    """`startswith("http")` aceitava a string "http" e montava ":///dash"."""
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "")
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", MESTRE)
    for torta in ("http", "https", "httpsy", "/painel", ""):
        monkeypatch.setattr(wa_bot, "PAINEL_URL_DICA", torta)
        link = wa_bot.link_do_painel()
        assert not link.startswith(":"), f"{torta!r} -> {link!r}"
        assert "///" not in link, f"{torta!r} -> {link!r}"


def test_PAINEL_URL_boa_continua_virando_link(monkeypatch):
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "")
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", MESTRE)
    monkeypatch.setattr(wa_bot, "PAINEL_URL_DICA",
                        "https://bot.example/painel?k=qualquer")
    link = wa_bot.link_do_painel()
    assert link.startswith("https://bot.example/dash?k="), link
    assert "qualquer" not in link


# --- os dois bloqueadores da auditoria final --------------------------

def test_o_health_conta_tentativa_de_adivinhar_o_token(cli, _freio_zerado,
                                                       avisos):
    """O /health era ORACULO de forca bruta, fora do caminho do freio.

    Ele confere credencial mas nunca chamava `_negado` — entao 200 chutes
    ali deixavam `_RECUSAS` vazio, zero alerta e zero linha de log. E o
    oraculo era perfeito: token errado devolve 3 campos, token certo
    devolve 21. Era o caminho mais barato pra atacar a unica barreira do
    painel, e o unico que ficou de fora da protecao.
    """
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR):
        cli.get("/health?k=chute")
    assert avisos, "chutar o token no /health nao avisa ninguem"


def test_o_health_para_de_responder_a_quem_insiste(cli, _freio_zerado,
                                                   avisos):
    """A propriedade que o nome deste teste sempre prometeu.

    A versao anterior chamava-se "bloqueia quem insiste" e media outra
    coisa: que o /health ALIMENTA o bloqueio do /dash. Ninguem verificava
    que o /health recusa — e ele nao recusava. Contava a tentativa e
    respondia assim mesmo, entao dava pra chutar o token daqui em
    velocidade total, pra sempre, com o oraculo perfeito (3 campos pra
    errado, 21 pro certo).

    Mesma armadilha das outras duas guardas que passaram: o teste tem o
    nome da propriedade e mede a do vizinho.
    """
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
        cli.get("/health?k=chute")
    # Com a origem bloqueada, nem a credencial CERTA abre o corpo inteiro.
    assert set(cli.get(f"/health?k={MESTRE}").json()) == set(
        wa_bot.ABERTO_NO_HEALTH)


def test_o_health_bloqueado_ainda_alimenta_o_freio_do_painel(cli,
                                                             _freio_zerado,
                                                             avisos):
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
        cli.get("/health?k=chute")
    assert TestClient(wa_bot.app).get("/dash?k=outro").status_code == 429


def test_o_monitor_nao_perde_o_health_durante_um_ataque(cli, _freio_zerado,
                                                        avisos):
    """O monitor bate sem token: nunca conta recusa, nunca e bloqueado.

    Se ele passasse a ver erro por causa de um ataque, o dono receberia
    "bot caido" no meio de um incidente que nao derrubou nada.
    """
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR * 2):
        cli.get("/health?k=chute")
    r = cli.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_o_dono_nao_perde_o_painel_durante_um_ataque(cli, _freio_zerado,
                                                     avisos):
    """O /health enriquecido some por 15 min numa origem compartilhada —
    custo aceito. O /dash, que e onde ele olha, continua inteiro."""
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
        cli.get("/health?k=chute")
    assert TestClient(wa_bot.app).get(
        f"/dash?k={MESTRE}", follow_redirects=False).status_code == 302


def test_o_monitor_batendo_no_health_nao_dispara_alarme(cli, _freio_zerado,
                                                        avisos):
    """O monitor externo bate aqui SEM token o tempo todo, de proposito.

    Se isso contasse recusa, o alarme tocaria sozinho a cada poucos
    minutos — e alarme que toca a toa e alarme que a pessoa silencia.
    """
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR * 2):
        assert cli.get("/health").status_code == 200
    assert avisos == []
    assert not wa_bot._RECUSAS


def test_assinatura_forjada_no_webhook_devolve_403(cli, monkeypatch):
    """O ramo que RECUSA a assinatura levantava NameError e virava 500.

    `JSONResponse` nao existe no namespace do modulo. A consequencia nao e
    o status errado: a Meta trata 5xx como falha de entrega, repete, e com
    falha sustentada DESATIVA a assinatura do webhook — o bot fica mudo
    pra base inteira sem nada explicando. E qualquer anonimo dispara isso
    de fora, so mandando um POST com header forjado.
    """
    monkeypatch.setattr(wa_bot.wasender, "OFICIAL", True, raising=False)
    monkeypatch.setattr(wa_bot.meta_cloud, "assinatura_valida",
                        lambda corpo, cab: False)
    for cab in ({"x-hub-signature-256": "sha256=00"}, {}):
        r = cli.post("/webhook", json={"entry": []}, headers=cab)
        assert r.status_code == 403, f"{cab} -> {r.status_code}"


def test_assinatura_boa_no_webhook_continua_passando(cli, monkeypatch):
    """Fechar o 403 nao pode ter fechado a porta da Meta."""
    monkeypatch.setattr(wa_bot.wasender, "OFICIAL", True, raising=False)
    monkeypatch.setattr(wa_bot.meta_cloud, "assinatura_valida",
                        lambda corpo, cab: True)
    assert cli.post("/webhook", json={"entry": []}).status_code == 200


# --- o alarme nao pode virar a forma de calar o alarme ----------------

def test_o_atacante_nao_escolhe_a_chave_de_repeticao(cli, _freio_zerado,
                                                     monkeypatch):
    """`_alertar_dono` deriva a trava de 30 min do `motivo`, e so guarda 80
    caracteres. Com o `X-Forwarded-For` ali dentro, o atacante escolhia
    parte da chave: variando o header ele furava a trava e consumia o teto
    de alertas por hora. Dali pra frente, FALHA DE VERDADE do motor era
    engolida em silencio — o alarme novo calando o alarme antigo.
    """
    motivos = []
    monkeypatch.setattr(wa_bot, "_alertar_dono",
                        lambda motivo, tel, txt: motivos.append(motivo))
    for volta in range(4):
        wa_bot._RECUSAS.clear()
        for _ in range(wa_bot.RECUSAS_ATE_ALERTAR):
            cli.get("/dash?k=x",
                    headers={"X-Forwarded-For": f"{volta}.{volta}.{volta}.1"})
    assert len(set(motivos)) == 1, f"o motivo variou: {set(motivos)}"


def test_o_texto_do_atacante_nao_chega_cru_no_whatsapp(cli, _freio_zerado,
                                                       monkeypatch):
    """O alerta e assinado "Resolve AI" e leva o link do painel logo
    abaixo. Um canal confiavel carregando texto de terceiro deixa de ser
    confiavel."""
    recado = []
    monkeypatch.setattr(wa_bot, "_alertar_dono",
                        lambda motivo, tel, txt: recado.append(motivo + txt))
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR):
        cli.get("/dash?k=x", headers={
            "X-Forwarded-For": "pague em bit.ly/xx URGENTE"})
    junto = " ".join(recado)
    assert "bit.ly" not in junto and "URGENTE" not in junto, junto
    assert "pague" not in junto.lower(), junto


def test_o_ip_alegado_ainda_chega_pro_dono(cli, _freio_zerado, monkeypatch):
    """Higienizar nao pode ter apagado a unica pista util."""
    recado = []
    monkeypatch.setattr(wa_bot, "_alertar_dono",
                        lambda motivo, tel, txt: recado.append(txt))
    for _ in range(wa_bot.RECUSAS_ATE_ALERTAR):
        cli.get("/dash?k=x", headers={"X-Forwarded-For": "203.0.113.7"})
    assert "203.0.113.7" in " ".join(recado), recado


# --- o cookie nao pode ser REBAIXADO ----------------------------------

def test_link_quase_vencido_nao_encurta_a_sessao_boa(cli, _freio_zerado):
    """A primeira correcao so barrou token INVALIDO. Um temporario ainda
    valido e quase vencido tambem estraga: o dono toca no link do relatorio
    no ultimo dia do prazo e a sessao de 30 dias vira uma de segundos.
    """
    cli.get(f"/dash?k={MESTRE}")
    r = cli.get(f"/dash?k={wa_bot.token_temporario(1)}",
                follow_redirects=False)
    assert "set-cookie" not in r.headers, r.headers.get("set-cookie")
    assert cli.get("/dash").status_code == 200


def test_link_melhor_ainda_substitui_o_cookie(cli, _freio_zerado):
    """Nao rebaixar nao pode virar "nunca mais troca"."""
    c = TestClient(wa_bot.app)
    c.get(f"/dash?k={wa_bot.token_temporario(1)}")
    r = c.get(f"/dash?k={MESTRE}", follow_redirects=False)
    assert _max_age(r) == wa_bot.COOKIE_DIAS * 86400, _cookie_cru(r)


def test_o_dono_entrando_nao_destranca_o_atacante(cli, _freio_zerado,
                                                  avisos):
    """Atras do proxy a origem e a mesma pra todo mundo: zerar o contador
    ao acertar fazia o freio virar "20 tentativas por login do dono"."""
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
        cli.get("/dash?k=errado")
    cli.get(f"/dash?k={MESTRE}")
    # Cliente novo: o do dono ficou com cookie e entraria por ele, o que
    # esconderia o que este teste mede.
    assert TestClient(wa_bot.app).get(
        "/dash?k=outro-chute").status_code == 429


def test_o_health_nao_roda_no_event_loop():
    """`def`, nao `async def` — e a diferenca importa em producao.

    Esta rota e publica e faz I/O que BLOQUEIA: `_instance_state()` e um
    `httpx.get(timeout=8)` sincrono, mais consultas ao banco. Dentro de uma
    corotina isso trava o event loop inteiro por ate 8 segundos por
    chamada, e qualquer anonimo provoca isso em rajada — o bot para de
    atender todo mundo enquanto espera.

    Como `def`, o FastAPI roda no threadpool. Trocar de volta pra
    `async def` nao quebra teste nenhum de comportamento, entao o teste e
    esse: a decisao fica escrita.
    """
    import inspect
    rota = next(r for r in wa_bot.app.routes
                if getattr(r, "path", "") == "/health")
    assert not inspect.iscoroutinefunction(rota.endpoint), (
        "/health virou async: I/O bloqueante numa rota publica trava o "
        "event loop e o bot para de responder a todo mundo")
