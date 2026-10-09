#!/usr/bin/env python3
"""
Divide os dados completos (data/pecs.json) nos arquivos que o site baixa:

  site/data/lista.json      o que a página inicial e as buscas precisam (sem votos individuais,
                            movimentações, autores, relatores e links)
  site/data/pec/NN.json     os detalhes completos, em 64 pacotes; a página de uma PEC baixa só o dela
  site/data/pessoas.json    autores, relatores e votos, para a busca por parlamentar da "Descubra"
  site/data/traducoes-XX.json  resumos em inglês e espanhol, baixados só por quem escolhe esses idiomas

Assim a maioria dos visitantes baixa só a lista, e uma atualização muda só os pacotes das PECs
que mudaram. Cada arquivo só é regravado se o conteúdo mudou.

Uso:  python3 scripts/publicar.py     (o robô e o gerar_resumos.py já chamam sozinhos)
"""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "data" / "pecs.json"
SITE = RAIZ / "site" / "data"
PACOTES = 64

# fora da lista: só aparecem na página de cada PEC (ou na Descubra, via pessoas.json)
SO_DETALHE = {"movimentacoes", "autores", "relatores", "textoAtual", "textoOriginal", "linkOficial", "linkTexto",
              "linkCamara", "linkSenado", "codigoMateria", "idCamara", "origemCamara"}
SIMPLES_SO_DETALHE = {"base", "mudanca", "fonte", "en", "es"}
IDIOMAS = ("en", "es")
VOTACAO_LISTA = ("casa", "data", "turno", "aprovada", "placar", "necessarios")


def pacote(pid):
    """Número do pacote de uma PEC. A mesma conta está em site/index.html (função pacote)."""
    h = 0
    for c in pid:
        h = (h * 31 + ord(c)) % PACOTES
    return h


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _gravar(caminho, texto):
    """Grava só se mudou. Devolve True se gravou."""
    if caminho.exists() and caminho.read_text() == texto:
        return False
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto)
    return True


def salvar_dados(dados):
    """data/pecs.json com uma PEC por linha: o histórico do git mostra só as linhas que mudaram."""
    cab = {k: v for k, v in dados.items() if k != "pecs"}
    linhas = ",\n".join(_json(p) for p in dados["pecs"])
    return _gravar(DADOS, _json(cab)[:-1] + ',"pecs":[\n' + linhas + "\n]}\n")


def item_lista(p):
    x = {k: v for k, v in p.items() if k not in SO_DETALHE}
    x["votacoes"] = [{k: v[k] for k in VOTACAO_LISTA if k in v} for v in p.get("votacoes", [])]
    if p.get("simples"):
        x["simples"] = {k: v for k, v in p["simples"].items() if k not in SIMPLES_SO_DETALHE}
    # a lista completa de coautores (às vezes dezenas de nomes) fica só no detalhe
    if len(p.get("autor") or "") > 120:
        x["autor"] = p["autor"].split(", ")[0] + " e outros"
    return x


def gerar_site(dados):
    cab = {k: v for k, v in dados.items() if k != "pecs"}
    mudou = [_gravar(SITE / "lista.json", _json({**cab, "pacotes": PACOTES, "pecs": [item_lista(p) for p in dados["pecs"]]}))]

    grupos = {}
    for p in dados["pecs"]:
        grupos.setdefault(pacote(p["id"]), {})[p["id"]] = p
    for n in range(PACOTES):
        mudou.append(_gravar(SITE / "pec" / f"{n:02d}.json", _json(grupos.get(n, {}))))

    pessoas = [{"id": p["id"], "autores": p.get("autores", []), "relatores": p.get("relatores", []),
                "votacoes": [{"casa": v["casa"], "turno": v["turno"], "data": v["data"], "votos": v["votos"]}
                             for v in p.get("votacoes", []) if v.get("votos")]}
               for p in dados["pecs"]]
    mudou.append(_gravar(SITE / "pessoas.json", _json({"pecs": pessoas})))

    for lang in IDIOMAS:
        trad = {p["id"]: p["simples"][lang] for p in dados["pecs"] if (p.get("simples") or {}).get(lang)}
        mudou.append(_gravar(SITE / f"traducoes-{lang}.json", _json(trad)))

    antigo = SITE / "pecs.json"           # formato antigo, de antes da divisão
    if antigo.exists():
        antigo.unlink()
    return sum(mudou)


if __name__ == "__main__":
    n = gerar_site(json.loads(DADOS.read_text()))
    print(f"{n} arquivo(s) do site atualizados.")
