# -*- coding: utf-8 -*-
"""Submete o catálogo de templates na Meta Cloud API.

    python templates/submeter.py             # SECO: só mostra o que iria
    python templates/submeter.py --enviar    # cria de verdade na Meta

Por que existe: colar sete templates à mão no Business Manager é sete chances
de o corpo submetido divergir do corpo que o bot manda. E essa divergência é a
pior possível — a Meta APROVA o template, o envio usa o corpo do repo, e a
recusa só aparece em produção, no disparo, como falha sem explicação.

Aqui a fonte é `templates.CATALOGO`, o mesmo objeto que o `canal.falar` usa.

TRÊS DECISÕES DE SEGURANÇA, todas por causa das duas restrições que este
número já levou da Meta:

  1. SECO POR PADRÃO. O passo que fala com a Meta é o que você digita.
  2. RODAR DUAS VEZES É NORMAL, e não pode ser fatal: você submete sete, a
     Meta reprova um, você corrige aquele e roda de novo. Se o primeiro
     "já existe" abortar o lote, os outros seis nunca sobem.
  3. CREDENCIAL FALTANDO DIZ QUAL. Nada de KeyError, e nada de tentar a
     chamada "pra ver o que acontece" com token vazio.

Depois que a Meta aprovar, o que liga o envio é a variável de ambiente no
EasyPanel — o repo não decide isso sozinho:

    TEMPLATES_APROVADOS=nome1,nome2,...
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import templates                                            # noqa: E402

API_VERSION = os.environ.get("META_API_VERSION", "v23.0")
GRAPH = f"https://graph.facebook.com/{API_VERSION}"
TOKEN = os.environ.get("META_TOKEN", "").strip()
WABA_ID = os.environ.get("META_WABA_ID", "").strip()

# A Meta responde "já existe" com este subcódigo. É o resultado ESPERADO da
# segunda execução, não um erro — e tratá-lo como erro é o que transformaria
# um comando reexecutável num comando de uma vez só.
SUBCODIGO_JA_EXISTE = 2388023


def payload_de(t: templates.Template) -> dict:
    """O corpo do POST /{WABA_ID}/message_templates para um template."""
    componentes = [{"type": "BODY", "text": t.corpo}]
    if t.exemplo:
        # A Meta exige exemplo quando o corpo tem variável, e recusa a
        # submissão inteira sem ele.
        componentes[0]["example"] = {"body_text": [list(t.exemplo)]}
    return {"name": t.nome, "language": t.idioma, "category": t.categoria,
            "components": componentes}


def _post(url: str, payload: dict):
    """Isolado numa função só pra que o teste possa interceptar a rede.

    Sem esse ponto de corte, testar este arquivo exigiria ou falar com a Meta
    de verdade, ou não testar — e "não testar" é como o script de submissão
    vira o único código sem cobertura do repo.
    """
    import httpx
    return httpx.post(url, json=payload,
                      headers={"Authorization": f"Bearer {TOKEN}"},
                      timeout=30)


def _credenciais_faltando() -> list:
    faltando = []
    if not WABA_ID:
        faltando.append("META_WABA_ID")
    if not TOKEN:
        faltando.append("META_TOKEN")
    return faltando


def _resultado(resp) -> tuple:
    """(estado, detalhe) a partir da resposta da Meta.

    estado: "criado" | "ja_existe" | "erro"
    """
    try:
        corpo = resp.json()
    except Exception:
        corpo = {}
    if 200 <= getattr(resp, "status_code", 0) < 300:
        return "criado", corpo.get("id") or corpo.get("status") or "ok"
    erro = corpo.get("error") or {}
    sub = erro.get("error_subcode")
    msg = erro.get("message") or getattr(resp, "text", "") or "erro sem corpo"
    if sub == SUBCODIGO_JA_EXISTE or "already exists" in str(msg).lower():
        return "ja_existe", msg
    return "erro", msg


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `--dry-run` ja e o padrao; ele existe como flag porque quem digita a
    # intencao explicita nao pode ser surpreendido por um envio.
    enviar = "--enviar" in argv and "--dry-run" not in argv

    if not enviar:
        print("MODO SECO — nada será criado na Meta. "
              "Use --enviar para submeter de verdade.\n")
        for nome, t in templates.CATALOGO.items():
            print(f"--- {nome} [{t.categoria}/{t.idioma}] ---")
            print(json.dumps(payload_de(t), ensure_ascii=False, indent=2))
            print()
        print(f"{len(templates.CATALOGO)} template(s) prontos para submeter.")
        return 0

    faltando = _credenciais_faltando()
    if faltando:
        # DIZER QUAL, e não "credenciais inválidas": quem roda isso está no
        # terminal do EasyPanel e precisa saber o nome exato da variável.
        print("Não dá pra submeter: falta configurar "
              + ", ".join(faltando)
              + ".\nElas são as mesmas que o bot já usa (meta_cloud.py). "
                "Nenhum POST foi feito.")
        return 2

    url = f"{GRAPH}/{WABA_ID}/message_templates"
    contagem = {"criado": 0, "ja_existe": 0, "erro": 0}
    for nome, t in templates.CATALOGO.items():
        try:
            resp = _post(url, payload_de(t))
        except Exception as e:
            # Rede caindo no meio do lote não pode derrubar os que faltam.
            estado, detalhe = "erro", f"falha de rede: {e}"
        else:
            estado, detalhe = _resultado(resp)
        contagem[estado] += 1
        marca = {"criado": "✅ criado", "ja_existe": "↩️  já existe",
                 "erro": "❌ ERRO"}[estado]
        print(f"{marca}: {nome} — {detalhe}")

    print(f"\n{contagem['criado']} criado(s), "
          f"{contagem['ja_existe']} já existia(m), "
          f"{contagem['erro']} com erro.")
    if contagem["erro"]:
        print("Corrija os templates com erro e rode de novo: os que já "
              "existem serão apenas reportados.")
        return 1
    print("Agora acompanhe a aprovação no WhatsApp Manager. Quando aprovar, "
          "configure no EasyPanel:\n  TEMPLATES_APROVADOS="
          + ",".join(templates.CATALOGO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
