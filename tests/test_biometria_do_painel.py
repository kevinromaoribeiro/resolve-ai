# -*- coding: utf-8 -*-
"""A segunda trava do painel: biometria ligada ao aparelho do dono.

Depois de tirar o token da URL, por freio e alarme, sobrava o buraco
estrutural: UMA senha eterna, em texto na tela de variaveis do EasyPanel,
com poder total sobre a base. Quem copiasse aquilo entrava de qualquer
lugar do mundo.

Passkey muda a natureza da coisa — a chave privada nasce e morre dentro do
aparelho, e o servidor so guarda a publica, que nao serve pra entrar.

O RISCO DESTA FEATURE nao e o atacante: e trancar o DONO do lado de fora.
Metade destes testes existe por causa disso.
"""
import hashlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient

import db
import passkey as pk
import wa_bot


MESTRE = "token-mestre-de-teste"
SITE = "testserver"


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", MESTRE)
    wa_bot._RECUSAS.clear()
    wa_bot._BLOQUEADOS.clear()
    for p in db.passkeys_do_painel():
        db.passkey_apagar(p["cred_id"])
    pk._DESAFIOS.clear()
    yield
    for p in db.passkeys_do_painel():
        db.passkey_apagar(p["cred_id"])
    pk._DESAFIOS.clear()


@pytest.fixture
def cli():
    return TestClient(wa_bot.app)


# --- um aparelho de mentira, que assina de verdade --------------------

class Aparelho:
    """Simula o autenticador do dono: gera par de chaves e assina.

    Assinar so existe aqui. O servidor de producao apenas VERIFICA — e e
    por isso que ele nao precisa de biblioteca de cripto.
    """

    def __init__(self, verificado=True, presente=True, contador=1):
        self.d = secrets.randbelow(pk.N - 1) + 1
        self.Q = pk._multiplicar(self.d, (pk.GX, pk.GY))
        self.cred_id = secrets.token_bytes(16)
        self.verificado = verificado
        self.presente = presente
        self.contador = contador

    def _flags(self, com_credencial):
        f = 0
        if self.presente:
            f |= 0x01
        if self.verificado:
            f |= 0x04
        if com_credencial:
            f |= 0x40
        return f

    def _auth_data(self, site=SITE, com_credencial=False, contador=None):
        d = hashlib.sha256(site.encode()).digest()
        d += bytes([self._flags(com_credencial)])
        d += int(self.contador if contador is None else contador
                 ).to_bytes(4, "big")
        if com_credencial:
            d += b"\x00" * 16                      # AAGUID
            d += len(self.cred_id).to_bytes(2, "big")
            d += self.cred_id
            d += self._cose()
        return d

    def _cose(self):
        """Chave publica no formato COSE ES256, em CBOR."""
        def _int(v):
            return bytes([0x00 | v]) if v < 24 else bytes([0x18, v])

        def _neg(v):
            n = -1 - v
            return bytes([0x20 | n]) if n < 24 else bytes([0x38, n])

        def _bytes(b):
            return bytes([0x58, len(b)]) + b

        return (bytes([0xA5])
                + _int(1) + _int(2)      # kty: EC2
                + _int(3) + _neg(-7)     # alg: ES256
                + _neg(-1) + _int(1)     # crv: P-256
                + _neg(-2) + _bytes(self.Q[0].to_bytes(32, "big"))
                + _neg(-3) + _bytes(self.Q[1].to_bytes(32, "big")))

    def _client_data(self, tipo, desafio, origem=f"https://{SITE}"):
        return json.dumps({"type": tipo, "challenge": desafio,
                           "origin": origem}).encode()

    def registrar(self, desafio, site=SITE, origem=None):
        cd = self._client_data("webauthn.create", desafio,
                               origem or f"https://{site}")
        att = (bytes([0xA1]) + bytes([0x68]) + b"authData"
               + self._att_bytes(self._auth_data(site, True)))
        return {"clientDataJSON": pk.b64url(cd),
                "attestationObject": pk.b64url(att)}

    @staticmethod
    def _att_bytes(b):
        if len(b) < 256:
            return bytes([0x58, len(b)]) + b
        return bytes([0x59]) + len(b).to_bytes(2, "big") + b

    def entrar(self, desafio, site=SITE, origem=None, contador=None):
        cd = self._client_data("webauthn.get", desafio,
                               origem or f"https://{site}")
        if contador is None:
            # Autenticador de verdade ANDA PRA FRENTE a cada uso. E o que
            # permite detectar copia: contador que nao avanca e suspeito.
            self.contador += 1
        ad = self._auth_data(site, False, contador)
        r, s = self._assinar(ad + hashlib.sha256(cd).digest())
        return {"id": pk.b64url(self.cred_id),
                "clientDataJSON": pk.b64url(cd),
                "authenticatorData": pk.b64url(ad),
                "signature": self._der(r, s)}

    def _assinar(self, mensagem):
        z = int.from_bytes(hashlib.sha256(mensagem).digest(), "big")
        while True:
            k = secrets.randbelow(pk.N - 1) + 1
            ponto = pk._multiplicar(k, (pk.GX, pk.GY))
            r = ponto[0] % pk.N
            if not r:
                continue
            s = (pow(k, -1, pk.N) * (z + r * self.d)) % pk.N
            if s:
                return r, s

    @staticmethod
    def _der(r, s):
        def _i(v):
            b = v.to_bytes((v.bit_length() + 7) // 8 or 1, "big")
            if b[0] & 0x80:
                b = b"\x00" + b
            return bytes([0x02, len(b)]) + b
        corpo = _i(r) + _i(s)
        return pk.b64url(bytes([0x30, len(corpo)]) + corpo)


def _desafio(cli):
    return cli.post("/painel/passkey/desafio").json()["desafio"]


def _registrar(cli, ap=None):
    ap = ap or Aparelho()
    r = cli.post("/painel/passkey/registrar", json=ap.registrar(_desafio(cli)))
    return ap, r


# --- o caminho feliz, ponta a ponta -----------------------------------

def test_registrar_e_entrar_com_biometria(cli):
    cli.get(f"/dash?k={MESTRE}")
    ap, r = _registrar(cli)
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert db.tem_passkey()
    # Ja entra: registrar e sair no 401 seria crueldade.
    assert cli.get("/dash").status_code == 200

    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    # Sem digital ele NAO ve o painel — ve a tela que pede a digital.
    assert "Confirme que e voce" in outro.get("/dash").text
    e = outro.post("/painel/passkey/entrar", json=ap.entrar(_desafio(outro)))
    assert e.status_code == 200 and e.json()["ok"], e.text
    depois = outro.get("/dash")
    assert depois.status_code == 200
    assert "Confirme que e voce" not in depois.text, "ficou preso na tela"


# --- o que NAO pode entrar --------------------------------------------

def test_assinatura_de_outra_chave_nao_entra(cli):
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    impostor = Aparelho()
    impostor.cred_id = ap.cred_id          # finge ser o mesmo aparelho
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    r = outro.post("/painel/passkey/entrar",
                   json=impostor.entrar(_desafio(outro)))
    assert r.status_code == 401, r.text


def test_passkey_desconhecida_nao_entra(cli):
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    estranho = Aparelho()
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    r = outro.post("/painel/passkey/entrar",
                   json=estranho.entrar(_desafio(outro)))
    assert r.status_code == 401
    assert "desconhecida" in r.json()["erro"]


def test_desafio_nao_serve_duas_vezes(cli):
    """Gravar uma resposta boa e repetir depois e o ataque de repeticao."""
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    d = _desafio(outro)
    corpo = ap.entrar(d)
    assert outro.post("/painel/passkey/entrar", json=corpo).status_code == 200
    terceiro = TestClient(wa_bot.app)
    terceiro.get(f"/dash?k={MESTRE}")
    r = terceiro.post("/painel/passkey/entrar", json=corpo)
    assert r.status_code == 401, "o desafio foi aceito de novo"


def test_desafio_inventado_nao_serve(cli):
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    r = outro.post("/painel/passkey/entrar",
                   json=ap.entrar(pk.b64url(secrets.token_bytes(32))))
    assert r.status_code == 401


def test_pagina_falsa_nao_consegue_usar_a_digital(cli):
    """E ISTO que faz passkey nao cair em phishing.

    Mesmo que o dono seja levado a um site parecido, o navegador assina
    com a origem DAQUELE site — e aqui nao bate.
    """
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    r = outro.post("/painel/passkey/entrar", json=ap.entrar(
        _desafio(outro), origem="https://resolveai-painel.com"))
    assert r.status_code == 401
    assert "origem" in r.json()["erro"]


def test_site_errado_no_authdata_nao_entra(cli):
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    corpo = ap.entrar(_desafio(outro))
    corpo["authenticatorData"] = pk.b64url(ap._auth_data("outro.site"))
    assert outro.post("/painel/passkey/entrar",
                      json=corpo).status_code == 401


def test_sem_biometria_confirmada_nao_registra(cli):
    """Passkey que so pede um toque vira segundo fator que qualquer um com
    o aparelho na mao usa. Exigimos verificacao do usuario."""
    cli.get(f"/dash?k={MESTRE}")
    ap = Aparelho(verificado=False)
    r = cli.post("/painel/passkey/registrar", json=ap.registrar(_desafio(cli)))
    assert r.status_code == 400
    assert not db.tem_passkey()


def test_sem_biometria_confirmada_nao_entra(cli):
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    ap.verificado = False
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    assert outro.post("/painel/passkey/entrar",
                      json=ap.entrar(_desafio(outro))).status_code == 401


def test_contador_que_anda_pra_tras_denuncia_copia(cli):
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    ap.contador = 50
    assert outro.post("/painel/passkey/entrar",
                      json=ap.entrar(_desafio(outro))).status_code == 200
    terceiro = TestClient(wa_bot.app)
    terceiro.get(f"/dash?k={MESTRE}")
    r = terceiro.post("/painel/passkey/entrar",
                      json=ap.entrar(_desafio(terceiro), contador=30))
    assert r.status_code == 401
    assert "copia" in r.json()["erro"]


def test_biometria_recusada_conta_no_freio(cli):
    """Senao o segundo fator seria a unica porta do painel sem freio."""
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    impostor = Aparelho()
    impostor.cred_id = ap.cred_id
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    for _ in range(wa_bot.RECUSAS_ATE_BLOQUEAR):
        outro.post("/painel/passkey/entrar",
                   json=impostor.entrar(_desafio(outro)))
    assert wa_bot._BLOQUEADOS, "chutar biometria nao alimentava o freio"


# --- o dono nao pode ficar trancado do lado de fora -------------------

def test_sem_nenhum_aparelho_o_painel_abre_so_com_token(cli):
    """Exigir a trava antes de existir chave e trancar a porta com a chave
    dentro: o dono nunca conseguiria registrar o primeiro aparelho."""
    assert not db.tem_passkey()
    assert cli.get(f"/dash?k={MESTRE}").status_code == 200


def test_token_certo_sem_digital_recebe_TELA_e_nao_401(cli):
    """401 aqui seria porta trancada sem maçaneta."""
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    outro = TestClient(wa_bot.app)
    r = outro.get(f"/dash?k={MESTRE}")
    assert r.status_code == 200
    assert "Confirme que e voce" in r.text
    assert "navigator.credentials" in r.text


def test_sem_token_nenhum_continua_401_e_nao_a_tela(cli):
    """Quem nao tem o token nem chega a ver que existe biometria."""
    _r = TestClient(wa_bot.app).get("/dash")
    assert _r.status_code == 401
    assert "Confirme" not in _r.text


def test_a_saida_de_emergencia_existe(cli, monkeypatch):
    """Perder o aparelho nao pode significar perder o negocio."""
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    monkeypatch.setenv("PASSKEY_OBRIGATORIA", "0")
    outro = TestClient(wa_bot.app)
    assert outro.get(f"/dash?k={MESTRE}").status_code == 200


def test_banco_fora_do_ar_nao_tranca_o_painel(cli, monkeypatch):
    """O primeiro fator ja barrou quem nao tem o token; travar tudo por
    causa de uma consulta seria trocar seguranca por indisponibilidade."""
    def _explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "tem_passkey", _explode)
    assert cli.get(f"/dash?k={MESTRE}").status_code == 200


def test_revogar_vale_na_hora(cli):
    """Aparelho perdido tem que perder o acesso imediatamente, e nao
    quando o selo vencer."""
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    ap2, _ = _registrar(cli, Aparelho())
    assert cli.get("/dash").status_code == 200
    alvo = pk.b64url(ap2.cred_id)
    assert cli.post("/painel/passkey/remover",
                    json={"cred_id": alvo}).json()["ok"]
    # o selo de quem foi revogado deixa de valer na mesma hora
    selo = wa_bot._selo_do_aparelho(alvo)
    assert not wa_bot._selo_vale(selo)


def test_nao_da_pra_remover_o_ultimo_aparelho(cli):
    """Senao o dono desliga a trava sem perceber e volta pro token so."""
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    r = cli.post("/painel/passkey/remover",
                 json={"cred_id": pk.b64url(ap.cred_id)})
    assert r.status_code == 400
    assert db.tem_passkey()


def test_quem_so_tem_o_token_nao_revoga_aparelho(cli):
    """Remover aparelho e o caminho pra DESLIGAR a segunda trava."""
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    _registrar(cli, Aparelho())
    so_token = TestClient(wa_bot.app)
    so_token.get(f"/dash?k={MESTRE}")
    r = so_token.post("/painel/passkey/remover",
                      json={"cred_id": pk.b64url(ap.cred_id)})
    assert r.status_code == 401


# --- o selo -----------------------------------------------------------

def test_o_selo_nao_pode_ser_lido_por_javascript(cli):
    cli.get(f"/dash?k={MESTRE}")
    _, r = _registrar(cli)
    assert "httponly" in r.headers.get("set-cookie", "").lower()
    # `lax` pelo mesmo motivo do cookie do token: Strict nao viaja quando
    # a navegacao comeca em outro app, e o link do painel chega pelo
    # WhatsApp. O CSRF que importa continua fechado — nenhuma rota de
    # escrita e GET, e a unica que era (o /watchdog) ignora cookie.
    assert "lax" in r.headers.get("set-cookie", "").lower()


def test_selo_forjado_nao_vale(cli):
    for lixo in ("", "inventado", "a" * 500, pk.b64url(b"x" * 40)):
        assert not wa_bot._selo_vale(lixo)


def test_selo_de_outra_chave_mestra_nao_vale(cli, monkeypatch):
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    selo = wa_bot._selo_do_aparelho(pk.b64url(ap.cred_id))
    assert wa_bot._selo_vale(selo)
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "outro-token-qualquer")
    assert not wa_bot._selo_vale(selo)


def test_o_selo_vence(cli, monkeypatch):
    cli.get(f"/dash?k={MESTRE}")
    ap, _ = _registrar(cli)
    monkeypatch.setattr(wa_bot, "PASSKEY_DIAS", -1)
    assert not wa_bot._selo_vale(
        wa_bot._selo_do_aparelho(pk.b64url(ap.cred_id)))


# --- registro no diario -----------------------------------------------

def test_registrar_e_revogar_deixam_rastro(cli):
    cli.get(f"/dash?k={MESTRE}")
    antes = len(db.ultimas_acoes_admin(50)
                if hasattr(db, "ultimas_acoes_admin") else [])
    _registrar(cli)
    if hasattr(db, "ultimas_acoes_admin"):
        depois = db.ultimas_acoes_admin(50)
        assert len(depois) > antes
        assert any("passkey" in str(a.get("acao", "")) for a in depois)


# --- o que o patch reverso mostrou que nao estava coberto -------------

def test_o_desafio_e_CONSUMIDO_e_nao_so_conferido():
    """Isolado de proposito.

    `test_desafio_nao_serve_duas_vezes` passava mesmo com o desafio sendo
    reutilizavel — porque quem barrava a repeticao era o CONTADOR, nao o
    desafio. Duas protecoes diferentes conferidas por uma assercao so: se
    uma some, a outra segura o teste e o buraco fica invisivel.
    """
    d = pk.novo_desafio()
    assert pk._gastar_desafio(d) is True
    assert pk._gastar_desafio(d) is False, "o desafio sobreviveu ao uso"


def test_desafio_vencido_nao_serve(monkeypatch):
    d = pk.novo_desafio()
    pk._DESAFIOS[d] = pk.time.time() - pk.DESAFIO_S - 1
    assert pk._gastar_desafio(d) is False


def test_desafio_inventado_nunca_serve():
    assert pk._gastar_desafio("nunca-existiu") is False
    assert pk._gastar_desafio("") is False


def test_so_aceita_chave_ES256_na_curva_certa():
    """Aceitar outro tipo de chave e aceitar assinatura que a gente nao
    sabe conferir — e o `verificar_es256` interpretaria bytes de outra
    coisa como se fossem coordenadas P-256."""
    boa = {1: 2, 3: -7, -1: 1, -2: b"\x01" * 32, -3: b"\x02" * 32}
    assert pk._coordenadas(boa) is not None
    for ruim, motivo in [
            ({**boa, 1: 3}, "tipo de chave RSA"),
            ({**boa, 3: -257}, "algoritmo RS256"),
            ({**boa, -1: 2}, "outra curva"),
            ({**boa, -2: b"\x01" * 31}, "coordenada curta"),
            ({**boa, -2: "nao e bytes"}, "coordenada que nao e bytes"),
            ({}, "chave vazia"),
            (None, "nem dicionario e")]:
        assert pk._coordenadas(ruim) is None, motivo


def test_o_backup_exige_os_DOIS_fatores(cli):
    """E o arquivo mais sensivel que existe: a base inteira de clientes
    numa requisicao so."""
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    assert cli.get("/painel/backup").status_code == 200

    so_token = TestClient(wa_bot.app)
    so_token.get(f"/dash?k={MESTRE}")
    assert so_token.get("/painel/backup").status_code == 401


def test_o_backup_vem_integro(cli):
    import sqlite3
    import tempfile
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    r = cli.get("/painel/backup")
    assert r.status_code == 200 and len(r.content) > 1000
    caminho = f"{tempfile.mkdtemp()}/copia.db"
    with open(caminho, "wb") as f:
        f.write(r.content)
    conn = sqlite3.connect(caminho)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_os_cabecalhos_de_seguranca_saem_em_toda_resposta(cli):
    """Sao travas que o NAVEGADOR aplica e cobrem classes inteiras de
    ataque sem depender de bug nenhum no nosso codigo."""
    for pagina in ("/health", f"/dash?k={MESTRE}"):
        h = cli.get(pagina).headers
        assert h.get("X-Frame-Options") == "DENY", pagina
        assert h.get("X-Content-Type-Options") == "nosniff", pagina
        assert h.get("Referrer-Policy") == "no-referrer", pagina
        csp = h.get("Content-Security-Policy", "")
        # E `connect-src 'self'` que impede um XSS de mandar os dados pra
        # fora: mesmo executando, ele nao alcanca servidor de terceiro.
        assert "connect-src 'self'" in csp, pagina
        assert "frame-ancestors 'none'" in csp, pagina


def test_hsts_so_sai_em_https(cli):
    """Mandar HSTS por http nao faz nada; o que importa e sair quando a
    conexao veio segura."""
    assert "Strict-Transport-Security" not in cli.get("/health").headers
    seguro = TestClient(wa_bot.app, base_url="https://x.example")
    assert "Strict-Transport-Security" in seguro.get("/health").headers


def test_o_token_anterior_abre_durante_a_troca(cli, monkeypatch):
    """Senha que nao da pra trocar na pratica nao e trocada nunca."""
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "token-novo")
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN_ANTERIOR", "token-velho")
    for t in ("token-novo", "token-velho"):
        assert TestClient(wa_bot.app).get(
            f"/dash?k={t}", follow_redirects=False).status_code == 302, t
    assert TestClient(wa_bot.app).get("/dash?k=qualquer").status_code == 401


def test_o_token_anterior_nao_assina_nada_novo(cli, monkeypatch):
    """Ele serve pra terminar a troca sem interrupcao, e vai perdendo
    alcance sozinho — nao vira uma segunda senha permanente."""
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "token-novo")
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN_ANTERIOR", "token-velho")
    link = wa_bot.token_temporario(8)
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "token-velho")
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN_ANTERIOR", "")
    assert not wa_bot._token_temporario_valido(link)


# --- O BLOQUEADOR DA AUDITORIA ----------------------------------------

def test_so_o_token_NAO_cadastra_aparelho_quando_ja_existe_um(cli):
    """O furo que anulava a feature inteira.

    Os flags de "biometria confirmada" sao bytes que o CLIENTE monta, e a
    atestacao `none` nao prova posse de nada. Com o registro exigindo so o
    token, quem copiasse a senha do EasyPanel cadastrava um aparelho
    proprio, recebia o selo e abria o painel — inclusive o backup com a
    base dos 11. O segundo fator existia so no papel.

    A assimetria denunciava sozinha: REMOVER exigia os dois fatores,
    ADICIONAR exigia um.
    """
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)                       # o dono cadastra o dele
    assert db.tem_passkey()

    atacante = TestClient(wa_bot.app)     # tem o token, nao tem aparelho
    atacante.get(f"/dash?k={MESTRE}")
    forjado = Aparelho()                  # chave dele, flags que ele quiser
    r = atacante.post("/painel/passkey/registrar",
                      json=forjado.registrar(_desafio(atacante)))
    assert r.status_code == 401, "o token sozinho cadastrou aparelho"
    assert not db.passkey_por_id(pk.b64url(forjado.cred_id))


def test_so_o_token_nao_alcanca_o_painel_nem_o_backup(cli):
    """A consequencia do furo, medida na ponta."""
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    atacante = TestClient(wa_bot.app)
    atacante.get(f"/dash?k={MESTRE}")
    forjado = Aparelho()
    atacante.post("/painel/passkey/registrar",
                  json=forjado.registrar(_desafio(atacante)))
    assert atacante.get("/painel/backup").status_code == 401
    assert "Confirme que e voce" in atacante.get("/dash").text


def test_o_PRIMEIRO_aparelho_ainda_entra_so_com_o_token(cli):
    """Nesse instante nao existe segundo fator pra exigir — exigi-lo seria
    trancar a porta com a chave dentro."""
    assert not db.tem_passkey()
    cli.get(f"/dash?k={MESTRE}")
    _, r = _registrar(cli)
    assert r.status_code == 200 and r.json()["ok"], r.text


def test_o_dono_cadastra_o_SEGUNDO_aparelho(cli):
    """Com os dois fatores, cadastrar outro tem que funcionar — e o que
    impede 'perdi o celular, perdi o painel'."""
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    _, r2 = _registrar(cli, Aparelho())
    assert r2.status_code == 200 and r2.json()["ok"], r2.text
    assert len(db.passkeys_do_painel()) == 2


def test_cadastro_de_aparelho_avisa_o_dono(cli, monkeypatch):
    """Sobra uma janela: a do primeiro cadastro. Se alguem chegar nela
    antes do dono, ele tem que saber no mesmo minuto."""
    avisos = []
    monkeypatch.setattr(wa_bot, "_alertar_dono",
                        lambda m, t, x: avisos.append(f"{m} {x}"))
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    assert avisos, "cadastrou aparelho e ninguem soube"
    assert "PRIMEIRO" in avisos[0]
    assert "PAINEL_TOKEN" in avisos[0], "nao diz o que fazer se nao foi ele"


# --- a tela que faltava -----------------------------------------------

def test_existe_tela_pra_cadastrar_outro_aparelho(cli):
    """Sem ela a trava tinha caminho so de ida: a tela de biometria
    esconde o botao de registrar quando ja existe aparelho, e o dono
    ficava sem como cadastrar o segundo."""
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    r = cli.get("/painel/aparelhos")
    assert r.status_code == 200
    assert "Registrar este aparelho" in r.text
    assert "navigator.credentials.create" in r.text
    assert "Cadastre dois" in r.text, "nao avisa do risco de ter so um"


def test_a_tela_de_aparelhos_exige_os_dois_fatores(cli):
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    so_token = TestClient(wa_bot.app)
    so_token.get(f"/dash?k={MESTRE}")
    # Nao devolve a lista: devolve a tela pedindo a digital.
    assert "Confirme que e voce" in so_token.get("/painel/aparelhos").text
    assert TestClient(wa_bot.app).get(
        "/painel/aparelhos").status_code == 401


def test_a_tela_lista_os_aparelhos_e_deixa_remover(cli):
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli, Aparelho())
    _registrar(cli, Aparelho())
    texto = cli.get("/painel/aparelhos").text
    assert texto.count("class='tirar'") == 2 or texto.count(
        'class="tirar"') == 2, texto[:300]


def test_a_saida_de_emergencia_esta_escrita_na_tela(cli):
    """Instrucao que so existe na minha cabeca nao serve pro dia ruim."""
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    tela = TestClient(wa_bot.app)
    tela.get(f"/dash?k={MESTRE}")
    assert "PASSKEY_OBRIGATORIA" in tela.get("/dash").text


def test_cadastro_nao_sobrescreve_aparelho_existente(cli):
    """`excludeCredentials` e dica pro navegador, nao regra.

    Um cliente forjado manda o `cred_id` de um aparelho ja cadastrado com
    OUTRA chave publica. Com `INSERT OR REPLACE`, isso trocava a chave por
    baixo: o aparelho REAL do dono parava de entrar e o do atacante ficava
    no lugar — e contornava a trava do "nao da pra remover o ultimo
    aparelho", porque nao removia, substituia.

    Exige os dois fatores, entao nao e porta de entrada: e persistencia e
    lockout depois de um acesso temporario (selo de 14 dias num navegador
    emprestado) virar desativacao permanente.
    """
    cli.get(f"/dash?k={MESTRE}")
    dono, _ = _registrar(cli)
    chave_antes = db.passkey_por_id(pk.b64url(dono.cred_id))["x"]

    intruso = Aparelho()
    intruso.cred_id = dono.cred_id          # mesmo id, chave diferente
    r = cli.post("/painel/passkey/registrar",
                 json=intruso.registrar(_desafio(cli)))
    assert r.status_code == 409, r.text
    assert db.passkey_por_id(pk.b64url(dono.cred_id))["x"] == chave_antes
    assert len(db.passkeys_do_painel()) == 1


def test_o_aparelho_do_dono_continua_entrando_depois_da_tentativa(cli):
    """O que a sobrescrita causava na pratica."""
    cli.get(f"/dash?k={MESTRE}")
    dono, _ = _registrar(cli)
    intruso = Aparelho()
    intruso.cred_id = dono.cred_id
    cli.post("/painel/passkey/registrar",
             json=intruso.registrar(_desafio(cli)))
    outro = TestClient(wa_bot.app)
    outro.get(f"/dash?k={MESTRE}")
    assert outro.post("/painel/passkey/entrar",
                      json=dono.entrar(_desafio(outro))).status_code == 200


def test_o_selo_so_anda_em_https_quando_a_conexao_e_segura(cli):
    """O selo e credencial: em https ele nao pode viajar em texto puro."""
    seguro = TestClient(wa_bot.app, base_url="https://x.example")
    seguro.get(f"/dash?k={MESTRE}")
    ap = Aparelho()
    r = seguro.post("/painel/passkey/registrar",
                    json=ap.registrar(
                        seguro.post("/painel/passkey/desafio").json()["desafio"],
                        site="x.example"))
    assert r.status_code == 200, r.text
    assert "secure" in r.headers.get("set-cookie", "").lower()


def test_o_painel_antigo_tambem_pede_a_digital(cli):
    """Mesmo ramo do /dash, mas so o /dash estava testado."""
    cli.get(f"/dash?k={MESTRE}")
    _registrar(cli)
    outro = TestClient(wa_bot.app)
    outro.get(f"/painel?k={MESTRE}")
    assert "Confirme que e voce" in outro.get("/painel").text


# --- o link do painel nao pode morrer de novo -------------------------

@pytest.fixture
def endereco_limpo():
    db.set_setting("endereco_publico", "")
    wa_bot._ORIGEM_APRENDIDA["url"] = ""
    yield
    db.set_setting("endereco_publico", "")
    wa_bot._ORIGEM_APRENDIDA["url"] = ""


def test_o_bot_aprende_o_proprio_endereco(endereco_limpo, monkeypatch):
    """O QUE QUEBROU DE VERDADE em producao.

    O link do relatorio saia de `DASH_URL_BASE`, posta a mao. Quando a
    porta 8000 foi fechada, a variavel continuou apontando pra
    `http://IP:8000` e o dono recebeu no WhatsApp um link pra um endereco
    que nao existia mais — tela branca, sem explicacao.

    O defeito de fundo nao foi a porta: foi o endereco depender de
    alguem lembrar de atualizar uma variavel.
    """
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "http://177.153.58.163:8000")
    assert wa_bot.base_do_painel() == "http://177.153.58.163:8000"
    TestClient(wa_bot.app, base_url="https://bot.example.com").get(
        f"/health?k={MESTRE}")
    assert wa_bot.base_do_painel() == "https://bot.example.com"


def test_o_endereco_sobrevive_ao_deploy(endereco_limpo, monkeypatch):
    """Processo reinicia a cada deploy. Se o aprendizado morresse junto,
    o primeiro relatorio da manha sairia com o endereco velho."""
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "http://177.153.58.163:8000")
    TestClient(wa_bot.app, base_url="https://bot.example.com").get(
        f"/health?k={MESTRE}")
    wa_bot._ORIGEM_APRENDIDA["url"] = ""          # simula o restart
    assert wa_bot.base_do_painel() == "https://bot.example.com"


def test_nao_aprende_de_http_nem_de_IP(endereco_limpo, monkeypatch):
    """IP e http sao exatamente de onde estamos saindo: sem certificado,
    e com o cookie do token viajando em texto puro."""
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "http://177.153.58.163:8000")
    TestClient(wa_bot.app, base_url="http://qualquer.com").get(
        f"/health?k={MESTRE}")
    assert wa_bot.base_do_painel() == "http://177.153.58.163:8000"
    TestClient(wa_bot.app, base_url="https://177.153.58.163").get(
        f"/health?k={MESTRE}")
    assert wa_bot.base_do_painel() == "http://177.153.58.163:8000"


def test_https_configurado_a_mao_continua_mandando(endereco_limpo,
                                                   monkeypatch):
    """Quem configurou https de proposito nao pode ser sobrescrito."""
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "https://escolhido.com")
    TestClient(wa_bot.app, base_url="https://outro.com").get(
        f"/health?k={MESTRE}")
    assert wa_bot.base_do_painel() == "https://escolhido.com"


def test_o_relatorio_usa_o_endereco_aprendido(endereco_limpo, monkeypatch):
    """A prova na ponta: a mensagem que chega no WhatsApp."""
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "http://177.153.58.163:8000")
    TestClient(wa_bot.app, base_url="https://bot.example.com").get(
        f"/health?k={MESTRE}")
    enviados = []
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999999999")
    manha = wa_bot.tempo.agora().replace(hour=9, minute=0)
    monkeypatch.setattr(wa_bot.tempo, "agora", lambda *a, **k: manha)
    monkeypatch.setattr(wa_bot.db, "dispatched_today", lambda *a, **k: False)
    monkeypatch.setattr(wa_bot.db, "log_dispatch", lambda *a, **k: None)
    monkeypatch.setattr(
        wa_bot, "_enviar_com_botao",
        lambda tel, txt, *a, **k: (enviados.append(txt), True)[1])
    wa_bot.relatorio_matinal()
    assert enviados
    assert "https://bot.example.com/dash?k=" in enviados[0], enviados[0]
    assert "177.153.58.163:8000" not in enviados[0]


# --- envenenar o endereco: o BLOQUEADOR da auditoria ------------------

HOSTIS = [
    "evil.com",                        # o basico
    "dono@evil.com",                   # o navegador ignora o que vem antes
    "evil.com/painel.resolveai.ia.br",  # o dono LE o dominio dele e clica
    "evil.com?", "evil.com#",          # lixo de URL
    "evil.com x",                      # espaco
    "xn--vil-9ma.com",                 # punycode parecido
    "-evil.com", "evil.com.", "..",    # host malformado
]


@pytest.mark.parametrize("host", HOSTIS)
def test_estranho_nao_envenena_o_endereco_do_painel(endereco_limpo,
                                                    monkeypatch, host):
    """O pior desfecho possivel desta feature.

    O endereco aprendido vira LINK COM TOKEN, mandado pelo WhatsApp do
    proprio bot. Se um estranho pudesse escolher o `Host`, o dono
    receberia — do canal em que ele confia — um link pro site do
    atacante. Seria pior que o problema que isto veio resolver.

    Confiar so na porta 8000 estar fechada nao servia: o rollback dela
    esta documentado como procedimento de emergencia, entao a protecao
    sumiria justamente no dia ruim.
    """
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "")
    try:
        TestClient(wa_bot.app, base_url="https://x").get(
            "/health", headers={"host": host})
    except Exception:
        pass                       # host que nem trafega ja esta barrado
    assert wa_bot.base_do_painel() == "", f"{host!r} envenenou o endereco"


def test_nem_com_o_token_um_host_torto_passa(endereco_limpo, monkeypatch):
    """Cinto e suspensorio: se a sessao do dono for usada de um lugar
    estranho, o formato ainda barra."""
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "")
    for host in ("evil.com/x", "a@b.com", "com espaco"):
        wa_bot._ORIGEM_APRENDIDA.clear()
        db.set_setting("endereco_publico", "")
        try:
            TestClient(wa_bot.app, base_url="https://x").get(
                f"/health?k={MESTRE}", headers={"host": host})
        except Exception:
            pass
        assert wa_bot.base_do_painel() == "", host


def test_quem_tem_o_token_ensina_o_endereco_certo(endereco_limpo,
                                                  monkeypatch):
    """A trava nao pode ter matado o que a feature existe pra fazer: o
    dono abre o painel pelo dominio e o endereco se ensina sozinho."""
    monkeypatch.setattr(wa_bot, "DASH_URL_BASE", "http://177.153.58.163:8000")
    TestClient(wa_bot.app, base_url="https://x").get(
        f"/health?k={MESTRE}", headers={"host": "painel.resolveai.ia.br"})
    assert wa_bot.base_do_painel() == "https://painel.resolveai.ia.br"
