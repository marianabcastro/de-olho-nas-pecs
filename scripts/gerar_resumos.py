#!/usr/bin/env python3
"""
Escreve, com a API do Claude, o título simples e o resumo em 3 frases (em português,
inglês e espanhol) a partir do TEXTO COMPLETO de cada PEC.

Dois casos:
  - PEC nova, ainda sem resumo;
  - PEC cujo texto ganhou uma versão nova depois do resumo (substitutivo, redação final…):
    o resumo é refeito para a versão atual e o modelo diz se o objetivo da PEC mudou.

Os resultados ficam em data/resumos.json com a marcação "base" (de qual versão do texto
o resumo foi escrito) e "conferido": "texto". Resumos revisados por uma pessoa
("conferido": "pessoa") nunca são sobrescritos: entram só na lista de revisão.

Requisitos:  pip install anthropic pypdf
             ANTHROPIC_API_KEY no ambiente (ou `ant auth login`)
Uso:         python3 scripts/gerar_resumos.py               # novas + textos alterados
             python3 scripts/gerar_resumos.py --limite 5
"""
import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

import publicar
import textos

RAIZ = Path(__file__).resolve().parent.parent
PECS = RAIZ / "data" / "pecs.json"
RESUMOS = RAIZ / "data" / "resumos.json"
MODELO = "claude-opus-5-5"
MAX_TEXTO = 15000   # caracteres dos dispositivos enviados ao modelo

TEMAS = ["Saúde", "Educação", "Impostos e economia", "Segurança pública", "Eleições e política", "Servidores e aposentadoria",
         "Meio ambiente e terra", "Direitos e cidadania", "Estados e municípios", "Justiça e tribunais", "Trabalho", "Outros"]

SISTEMA = """Você explica Propostas de Emenda à Constituição (PECs) do Congresso brasileiro para a população em geral, num site público e apartidário.

Regras:
- Linguagem simples, para quem tem ensino fundamental. Frases curtas. Sem juridiquês; se precisar de um termo técnico, explique.
- Estritamente neutro: descreva o que a PEC faz, nunca se é boa ou ruim. Sem adjetivos de valor, sem citar apoiadores ou críticos.
- Baseie-se só no texto fornecido. Não invente números, prazos ou efeitos que não estejam no texto.
- "titulo": até 90 caracteres, começando com verbo no presente. Nunca comece com "Altera o art.".
- "oQueMuda": uma frase sobre o que muda na prática.
- "quemAfeta": uma frase sobre quem seria afetado diretamente.
- "porQueImporta": uma frase factual sobre o que está em jogo, sem opinar.
- "dePrincipio": true se a PEC só acrescenta um direito, princípio ou objetivo (ex.: "é direito de todos…", "a ordem econômica observará…") sem criar sozinha regras concretas, verbas, prazos ou punições. Nesse caso, diga em "oQueMuda" que ela sozinha não cria programas, verbas nem punições.
- "en" e "es": tradução fiel dos mesmos quatro campos para inglês e espanhol, explicando siglas brasileiras quando necessário.
- Se receber um RESUMO ANTERIOR, compare: "objetivoMudou" é true só se a finalidade principal da PEC mudou (não conta mudança de redação ou detalhe); "oQueMudouNoTexto" diz em uma frase, em português, o que a nova versão mudou em relação ao resumo anterior. Sem resumo anterior, use false e ""."""

CAMPOS = {"titulo": {"type": "string"}, "oQueMuda": {"type": "string"}, "quemAfeta": {"type": "string"}, "porQueImporta": {"type": "string"}}
TRAD = {"type": "object", "properties": CAMPOS, "required": list(CAMPOS), "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {**CAMPOS, "tema": {"type": "string", "enum": TEMAS}, "en": TRAD, "es": TRAD,
                   "objetivoMudou": {"type": "boolean"}, "oQueMudouNoTexto": {"type": "string"}, "dePrincipio": {"type": "boolean"}},
    "required": [*CAMPOS, "tema", "en", "es", "objetivoMudou", "oQueMudouNoTexto", "dePrincipio"],
    "additionalProperties": False,
}

client = anthropic.Anthropic()


def pedido(p, texto, anterior):
    linhas = [f"{p['sigla']}", f"Ementa oficial: {p['ementa']}"]
    if p.get("explicacao"):
        linhas.append(f"Explicação oficial: {p['explicacao']}")
    linhas.append(f"TEXTO DA PEC (dispositivos):\n{texto[:MAX_TEXTO]}" if texto else "TEXTO DA PEC: indisponível; use só a ementa.")
    if anterior:
        linhas.append("RESUMO ANTERIOR: " + json.dumps({k: anterior.get(k) for k in CAMPOS}, ensure_ascii=False))
    return "\n\n".join(linhas)


def resumir(p, anterior):
    versao = p.get("textoAtual") or {}
    url = versao.get("url") or (p.get("textoOriginal") or {}).get("url") or p.get("linkTexto")
    bruto = textos.baixar_texto(url)
    texto = " ".join(textos.dispositivos(bruto)) if bruto else ""
    resp = client.beta.messages.create(
        model=MODELO,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SISTEMA,
        messages=[{"role": "user", "content": pedido(p, texto, anterior)}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"parou com stop_reason={resp.stop_reason}")
    out = json.loads(next(b.text for b in resp.content if b.type == "text"))
    novo = {k: out[k] for k in CAMPOS} | {"tema": out["tema"], "en": out["en"], "es": out["es"]}
    if out["dePrincipio"]:
        novo["tipo"] = "principio"
    if anterior and anterior.get("apelido"):
        novo["apelido"] = anterior["apelido"]
        for lang in ("en", "es"):
            if anterior.get(lang, {}).get("apelido"):
                novo[lang]["apelido"] = anterior[lang]["apelido"]
    novo["base"] = {"versao": versao.get("id") or "original", "conferido": "texto" if texto else "ementa",
                    "data": dt.date.today().isoformat(), "fonte": resp.model}
    if anterior:
        novo["mudanca"] = {"objetivoMudou": out["objetivoMudou"], "descricao": out["oQueMudouNoTexto"],
                           "versao": versao.get("tipo"), "data": versao.get("data")}
    return novo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--paralelo", type=int, default=3)
    args = ap.parse_args()

    dados = json.loads(PECS.read_text())
    resumos = json.loads(RESUMOS.read_text()) if RESUMOS.exists() else {}
    fila = []
    for p in dados["pecs"]:
        r = resumos.get(p["id"])
        atual = (p.get("textoAtual") or {}).get("id")
        if not r:
            fila.append((p, None))                                    # PEC nova
        elif atual and (r.get("base") or {}).get("versao") != atual and (r.get("base") or {}).get("conferido") != "pessoa":
            fila.append((p, r))                                       # texto mudou depois do resumo
    if args.limite:
        fila = fila[: args.limite]
    if not fila:
        print("Nada a resumir.")
        return
    print(f"Resumindo {len(fila)} PECs com {MODELO}…")

    erros = 0
    with ThreadPoolExecutor(args.paralelo) as ex:
        futuros = {ex.submit(resumir, p, ant): p for p, ant in fila}
        for f in as_completed(futuros):
            p = futuros[f]
            try:
                resumos[p["id"]] = f.result()
            except anthropic.AuthenticationError:
                sys.exit("Chave da API inválida ou ausente. Defina ANTHROPIC_API_KEY.")
            except (anthropic.APIStatusError, anthropic.APIConnectionError, RuntimeError, json.JSONDecodeError) as e:
                erros += 1
                print(f"  ! {p['sigla']}: {e}", file=sys.stderr)
                continue
            m = resumos[p["id"]].get("mudanca")
            print(f"  {p['sigla']}: {resumos[p['id']]['titulo']}" + (f"  [objetivo mudou: {m['objetivoMudou']}]" if m else ""))

    RESUMOS.write_text(json.dumps(resumos, ensure_ascii=False, indent=1))
    for p in dados["pecs"]:
        if p["id"] in resumos:
            p["simples"] = resumos[p["id"]]
    publicar.salvar_dados(dados)
    publicar.gerar_site(dados)
    print(f"OK ({erros} erros).")


if __name__ == "__main__":
    main()
