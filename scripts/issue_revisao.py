#!/usr/bin/env python3
"""Mantém UMA issue aberta no GitHub listando as PECs cujo texto mudou depois do resumo."""
import json
import subprocess
from pathlib import Path

LISTA = Path(__file__).resolve().parent.parent / "data" / "resumos_a_revisar.json"
TITULO = "Resumos para revisar (texto da PEC mudou)"


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


itens = json.loads(LISTA.read_text()) if LISTA.exists() else []
abertas = json.loads(gh("issue", "list", "--label", "revisao", "--state", "open", "--json", "number"))
if not itens:
    for i in abertas:
        gh("issue", "close", str(i["number"]), "--comment", "Nenhum resumo pendente.")
    raise SystemExit
corpo = ["O robô encontrou versões novas do texto destas PECs depois que o resumo foi escrito.",
         "Se houver chave da API, o resumo já foi refeito automaticamente: confira se está certo. Senão, revise à mão.", ""]
for x in itens:
    sem = f" · semelhança com o original: {int(x['semelhanca'] * 100)}%" if x.get("semelhanca") is not None else ""
    corpo.append(f"- [ ] **{x['sigla']}** — {x['titulo']} · {x['versao']} ({x['data']}){sem} · [texto]({x['url']})")
texto = "\n".join(corpo)
subprocess.run(["gh", "label", "create", "revisao", "--color", "e0a400", "--force"], capture_output=True)
if abertas:
    gh("issue", "edit", str(abertas[0]["number"]), "--body", texto)
else:
    gh("issue", "create", "--title", TITULO, "--label", "revisao", "--body", texto)
