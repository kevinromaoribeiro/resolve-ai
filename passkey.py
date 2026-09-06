# -*- coding: utf-8 -*-
"""Biometria de verdade pro painel: WebAuthn, sem dependencia nova.

O QUE ISTO RESOLVE. Ate aqui o painel tinha UMA senha eterna com poder
total: ver a base, estender trial, disparar mensagem pra todo mundo. A
gente tirou ela da URL, pos freio e alarme — mas ela continuava sendo uma
string que existe em texto na tela de variaveis do EasyPanel, e quem
copiasse aquilo entrava de qualquer lugar do mundo.

Passkey muda a natureza da coisa. A chave privada nasce DENTRO do
aparelho do dono (Windows Hello, Face ID, digital do Android), nunca sai
de la, e so e usada depois que o proprio aparelho confirma que e ele. Nao
da pra copiar de uma tela, nao da pra phishar, nao da pra reusar de outra
maquina. O servidor guarda so a chave PUBLICA, que nao serve pra entrar.

POR QUE EM PYTHON PURO. Adicionar `cryptography` ao build significaria
arriscar a imagem nao subir — e imagem que nao sobe e bot mudo pra 11
pessoas. Aqui so precisamos VERIFICAR assinatura ECDSA P-256, que sao
umas poucas dezenas de linhas de aritmetica modular. Nada de gerar chave,
nada de operacao com segredo: so conta publica, entao nao ha canal de
tempo pra proteger.

O QUE ISTO NAO E. Nao substitui o token: e o SEGUNDO fator. Token sem
passkey nao entra, passkey sem token nao entra. E o caminho de emergencia
continua existindo (`PASSKEY_OBRIGATORIA=0` no ambiente), porque perder o
aparelho nao pode significar perder o negocio.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

# ---------------------------------------------------------------------
# Curva P-256 (secp256r1). Numeros publicos, do padrao FIPS 186-4.
# ---------------------------------------------------------------------
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5


def _inv(x: int, m: int) -> int:
    return pow(x, m - 2, m)


def _somar(p1, p2):
    """Soma dois pontos da curva. `None` e o ponto no infinito."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * _inv(2 * y1, P) % P
    else:
        lam = (y2 - y1) * _inv(x2 - x1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return (x3, (lam * (x1 - x3) - y1) % P)


def _multiplicar(k: int, ponto):
    """Multiplicacao escalar. Nao precisa ser de tempo constante: aqui so
    entram valores PUBLICOS (chave publica e assinatura)."""
    resultado = None
    adicionando = ponto
    while k:
        if k & 1:
            resultado = _somar(resultado, adicionando)
        adicionando = _somar(adicionando, adicionando)
        k >>= 1
    return resultado


def _no_grafico(x: int, y: int) -> bool:
    """Ponto que nao esta na curva nao e chave publica valida."""
    return (y * y - (x * x * x + A * x + B)) % P == 0


def verificar_es256(x: int, y: int, r: int, s: int, mensagem: bytes) -> bool:
    """ECDSA P-256 com SHA-256. Qualquer coisa fora do esperado = False."""
    if not (0 < r < N and 0 < s < N):
        return False
    if not (0 <= x < P and 0 <= y < P) or not _no_grafico(x, y):
        return False
    z = int.from_bytes(hashlib.sha256(mensagem).digest(), "big")
    w = _inv(s, N)
    ponto = _somar(_multiplicar(z * w % N, (GX, GY)),
                   _multiplicar(r * w % N, (x, y)))
    if ponto is None:
        return False
    return ponto[0] % N == r % N


# ---------------------------------------------------------------------
# Os formatos que o navegador manda
# ---------------------------------------------------------------------

def b64url(dado: bytes) -> str:
    return base64.urlsafe_b64encode(dado).decode().rstrip("=")


def deb64url(txt: str) -> bytes:
    """O navegador manda base64url SEM o `=` do fim."""
    txt = str(txt or "")
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def _ler_cbor(dado: bytes, i: int = 0):
    """Decodificador CBOR do SUBCONJUNTO que o WebAuthn usa.

    Nao e um decodificador completo de proposito: menos codigo, menos
    superficie. Cobre inteiro, texto, bytes, lista e mapa — que e tudo o
    que aparece num attestationObject e numa chave COSE.
    """
    inicial = dado[i]
    i += 1
    tipo, arg = inicial >> 5, inicial & 0x1F
    if arg < 24:
        valor = arg
    elif arg == 24:
        valor, i = dado[i], i + 1
    elif arg == 25:
        valor, i = int.from_bytes(dado[i:i + 2], "big"), i + 2
    elif arg == 26:
        valor, i = int.from_bytes(dado[i:i + 4], "big"), i + 4
    elif arg == 27:
        valor, i = int.from_bytes(dado[i:i + 8], "big"), i + 8
    else:
        raise ValueError("CBOR fora do subconjunto suportado")

    if tipo == 0:
        return valor, i
    if tipo == 1:
        return -1 - valor, i
    if tipo == 2:
        return dado[i:i + valor], i + valor
    if tipo == 3:
        return dado[i:i + valor].decode("utf-8", "replace"), i + valor
    if tipo == 4:
        itens = []
        for _ in range(valor):
            item, i = _ler_cbor(dado, i)
            itens.append(item)
        return itens, i
    if tipo == 5:
        mapa = {}
        for _ in range(valor):
            chave, i = _ler_cbor(dado, i)
            item, i = _ler_cbor(dado, i)
            mapa[chave] = item
        return mapa, i
    if tipo == 7:
        return {20: False, 21: True, 22: None}.get(valor, valor), i
    raise ValueError("CBOR fora do subconjunto suportado")


def _ler_dados_do_autenticador(dado: bytes) -> dict:
    """Desmonta o `authenticatorData` (padrao WebAuthn, secao 6.1)."""
    if len(dado) < 37:
        raise ValueError("authenticatorData curto demais")
    fora = {
        "rp_id_hash": dado[0:32],
        "flags": dado[32],
        "contador": int.from_bytes(dado[33:37], "big"),
    }
    fora["presente"] = bool(fora["flags"] & 0x01)   # UP: alguem tocou
    fora["verificado"] = bool(fora["flags"] & 0x04)  # UV: biometria/PIN
    if fora["flags"] & 0x40:  # veio credencial nova junto
        tam = int.from_bytes(dado[53:55], "big")
        fora["cred_id"] = dado[55:55 + tam]
        chave, _ = _ler_cbor(dado, 55 + tam)
        fora["chave"] = chave
    return fora


def _coordenadas(chave_cose: dict):
    """Tira x e y de uma chave COSE ES256. Recusa qualquer outro tipo."""
    if not isinstance(chave_cose, dict):
        return None
    if chave_cose.get(1) != 2 or chave_cose.get(3) != -7:
        return None  # 1=kty EC2, 3=alg ES256. So aceitamos este par.
    if chave_cose.get(-1) != 1:
        return None  # curva P-256
    x, y = chave_cose.get(-2), chave_cose.get(-3)
    if not isinstance(x, bytes) or not isinstance(y, bytes):
        return None
    if len(x) != 32 or len(y) != 32:
        return None
    return int.from_bytes(x, "big"), int.from_bytes(y, "big")


def _ler_assinatura_der(assinatura: bytes):
    """A assinatura vem em DER (SEQUENCE de dois INTEGER)."""
    try:
        if assinatura[0] != 0x30:
            return None
        i = 2 if assinatura[1] < 0x80 else 2 + (assinatura[1] & 0x7F)
        if assinatura[i] != 0x02:
            return None
        tam_r = assinatura[i + 1]
        r = int.from_bytes(assinatura[i + 2:i + 2 + tam_r], "big")
        j = i + 2 + tam_r
        if assinatura[j] != 0x02:
            return None
        tam_s = assinatura[j + 1]
        s = int.from_bytes(assinatura[j + 2:j + 2 + tam_s], "big")
        return r, s
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------------
# Desafios: guardados em memoria, com prazo curto
# ---------------------------------------------------------------------
DESAFIO_S = 300
_DESAFIOS: dict[str, float] = {}


def novo_desafio() -> str:
    """Numero aleatorio de uso unico. Sem isso, gravar uma resposta boa e
    repetir depois entraria — que e o ataque de repeticao."""
    agora = time.time()
    for velho in [d for d, q in _DESAFIOS.items() if agora - q > DESAFIO_S]:
        _DESAFIOS.pop(velho, None)
    d = b64url(secrets.token_bytes(32))
    _DESAFIOS[d] = agora
    return d


def _gastar_desafio(d: str) -> bool:
    """Confere E CONSOME. Desafio que continua valendo depois de usado nao
    e desafio."""
    quando = _DESAFIOS.pop(str(d or ""), None)
    return quando is not None and time.time() - quando <= DESAFIO_S


def _origem_confere(origem: str, rp_id: str) -> bool:
    """A origem tem que ser exatamente o nosso site.

    E o que impede phishing: mesmo que o dono seja levado a um site
    parecido, o navegador assina com a origem DAQUELE site, e aqui nao
    bate. E por isso que passkey nao cai em pagina falsa.
    """
    from urllib.parse import urlsplit
    if not origem:
        return False
    p = urlsplit(origem)
    if p.scheme not in ("https", "http"):
        return False
    hospedeiro = (p.hostname or "").lower()
    if p.scheme == "http" and hospedeiro not in ("localhost", "127.0.0.1"):
        return False  # WebAuthn so vale em https, fora do desenvolvimento
    return hospedeiro == str(rp_id or "").lower()


def _confere_cliente(client_data: bytes, esperado: str, rp_id: str,
                     desafio_ok) -> Optional[str]:
    """Confere o `clientDataJSON`. Devolve o motivo da recusa, ou None."""
    try:
        dados = json.loads(client_data.decode("utf-8"))
    except Exception:
        return "clientData ilegivel"
    if dados.get("type") != esperado:
        return "tipo errado"
    if not desafio_ok(dados.get("challenge", "")):
        return "desafio invalido ou ja usado"
    if not _origem_confere(dados.get("origin", ""), rp_id):
        return "origem nao confere"
    return None


def registrar(corpo: dict, rp_id: str) -> dict:
    """Guarda uma passkey nova. Devolve o que precisa ser persistido."""
    try:
        client_data = deb64url(corpo.get("clientDataJSON"))
        att, _ = _ler_cbor(deb64url(corpo.get("attestationObject")))
        dados = _ler_dados_do_autenticador(att["authData"])
    except Exception:
        return {"ok": False, "erro": "resposta do navegador ilegivel"}

    ruim = _confere_cliente(client_data, "webauthn.create", rp_id,
                            _gastar_desafio)
    if ruim:
        return {"ok": False, "erro": ruim}
    if dados["rp_id_hash"] != hashlib.sha256(rp_id.encode()).digest():
        return {"ok": False, "erro": "site nao confere"}
    if not dados["presente"]:
        return {"ok": False, "erro": "ninguem confirmou no aparelho"}
    if not dados["verificado"]:
        # SEM BIOMETRIA NAO SERVE. Uma passkey que so pede um toque vira
        # um segundo fator que qualquer um com o aparelho na mao usa.
        return {"ok": False, "erro": "o aparelho nao confirmou quem e voce"}
    xy = _coordenadas(dados.get("chave"))
    if not xy:
        return {"ok": False, "erro": "chave em formato nao aceito"}
    return {"ok": True, "cred_id": b64url(dados["cred_id"]),
            "x": xy[0], "y": xy[1], "contador": dados["contador"]}


def conferir(corpo: dict, rp_id: str, achar_chave) -> dict:
    """Confere uma tentativa de entrar com passkey ja registrada."""
    try:
        cred_id = str(corpo.get("id") or "")
        client_data = deb64url(corpo.get("clientDataJSON"))
        auth_data = deb64url(corpo.get("authenticatorData"))
        assinatura = deb64url(corpo.get("signature"))
        dados = _ler_dados_do_autenticador(auth_data)
    except Exception:
        return {"ok": False, "erro": "resposta do navegador ilegivel"}

    guardada = achar_chave(cred_id)
    if not guardada:
        return {"ok": False, "erro": "passkey desconhecida"}

    ruim = _confere_cliente(client_data, "webauthn.get", rp_id,
                            _gastar_desafio)
    if ruim:
        return {"ok": False, "erro": ruim}
    if dados["rp_id_hash"] != hashlib.sha256(rp_id.encode()).digest():
        return {"ok": False, "erro": "site nao confere"}
    if not dados["presente"] or not dados["verificado"]:
        return {"ok": False, "erro": "o aparelho nao confirmou quem e voce"}

    rs = _ler_assinatura_der(assinatura)
    if not rs:
        return {"ok": False, "erro": "assinatura em formato invalido"}
    mensagem = auth_data + hashlib.sha256(client_data).digest()
    if not verificar_es256(int(guardada["x"]), int(guardada["y"]),
                           rs[0], rs[1], mensagem):
        return {"ok": False, "erro": "assinatura nao confere"}

    # CONTADOR QUE ANDA PRA TRAS denuncia passkey clonada. Nem todo
    # autenticador mantem contador (fica em 0 dos dois lados); so
    # reclamamos quando ele existe e regride.
    anterior = int(guardada.get("contador") or 0)
    if dados["contador"] and anterior and dados["contador"] <= anterior:
        return {"ok": False, "erro": "contador nao avancou (possivel copia)"}
    return {"ok": True, "cred_id": cred_id, "contador": dados["contador"]}


def rp_id_de(host: str) -> str:
    """O dominio que a passkey fica amarrada. Sem porta."""
    return str(host or "").split(":")[0].lower() or "localhost"


def obrigatoria() -> bool:
    """Saida de emergencia: perder o aparelho nao pode virar perder o
    negocio. Mudar isto exige acesso ao EasyPanel — que, se alguem tiver,
    ja tem tudo de qualquer jeito."""
    return str(os.environ.get("PASSKEY_OBRIGATORIA", "1")).strip() not in (
        "0", "nao", "não", "false", "")
