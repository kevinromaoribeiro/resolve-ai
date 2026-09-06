# -*- coding: utf-8 -*-
"""Varredura de segurança das dependências.

POR QUE UM ARQUIVO E NÃO UM COMANDO SOLTO: varredura que mora na cabeça de
alguém é varredura que roda uma vez. As dependências não pioram sozinhas —
o mundo é que descobre falhas nelas depois que já estão instaladas. Uma
biblioteca que estava limpa em setembro pode ter CVE em outubro sem nada
ter mudado aqui dentro.

COMO RODAR:

    ./.venv/Scripts/python.exe seguranca.py

Duas varreduras, porque respondem a perguntas diferentes:

  1. **O que está instalado aqui** — pega o que a máquina de desenvolvimento
     realmente carrega, incluindo dependência de dependência.
  2. **O que o `requirements.txt` resolve** — é o que o build do EasyPanel
     vai instalar. As versões são faixas (`>=`), então produção pode subir
     com uma versão diferente da daqui. Auditar só o local deixaria
     justamente produção sem olhar.

Sai com código != 0 se achar alguma coisa, pra poder virar passo de CI
depois sem reescrever nada.
"""
import subprocess
import sys

FERRAMENTA = "pip_audit"


def _rodar(titulo: str, args: list) -> bool:
    print(f"\n=== {titulo} ===")
    r = subprocess.run([sys.executable, "-m", FERRAMENTA,
                        "--progress-spinner", "off", *args],
                       capture_output=True, text=True)
    saida = (r.stdout or "") + (r.stderr or "")
    # O aviso de junction do venv no Windows é ruído, não achado.
    print("\n".join(l for l in saida.splitlines()
                    if "Actual environment location" not in l
                    and "Requested location" not in l
                    and "Actual location" not in l).strip())
    return r.returncode == 0


def main() -> int:
    try:
        subprocess.run([sys.executable, "-m", FERRAMENTA, "--version"],
                       capture_output=True, check=True)
    except Exception:
        print("pip-audit não está instalado. Rode:\n"
              "  ./.venv/Scripts/python.exe -m pip install pip-audit")
        return 2

    limpo = _rodar("o que está instalado nesta máquina", [])
    limpo &= _rodar("o que o requirements.txt resolve (é o que sobe)",
                    ["-r", "requirements.txt"])

    print("\n" + ("tudo limpo." if limpo else
                  "ACHOU COISA. Leia acima: cada linha traz a versão que "
                  "conserta.\nSuba a versão no requirements.txt e rode de "
                  "novo antes de fazer deploy."))
    return 0 if limpo else 1


if __name__ == "__main__":
    raise SystemExit(main())
