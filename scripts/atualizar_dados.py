#!/usr/bin/env python3
"""
Baixa das APIs oficiais da Câmara e do Senado todas as PECs que tiveram
alguma movimentação na legislatura atual (desde 01/02/2023), qualquer que
seja o ano de apresentação, e gera data/pecs.json (dados completos) e os arquivos do site
(site/data/lista.json, pec/NN.json, pessoas.json; ver publicar.py).

Por padrão a atualização é incremental: baixa de novo só as PECs que se
mexeram desde a última rodada (guardada em data/cache/). Uma rodada completa
acontece na primeira vez, com --completo, ou quando a última rodada é antiga.

Uso:  python3 scripts/atualizar_dados.py              # incremental
      python3 scripts/atualizar_dados.py --completo   # baixa tudo de novo
"""
import datetime as dt
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import publicar
import textos

INICIO_LEGISLATURA = dt.date(2023, 2, 1)
HOJE = dt.date.today()
CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
SENADO = "https://legis.senado.leg.br/dadosabertos"
RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "data" / "pecs.json"          # dados completos; o site usa a versão dividida (publicar.py)
CACHE = RAIZ / "data" / "cache"          # registros por Casa, fora da pasta publicada
MAX_DIAS_INCREMENTAL = 80                # a API da Câmara aceita janelas de até 3 meses

# Votos necessários para aprovar uma PEC (3/5 de cada Casa)
QUORUM = {"camara": 308, "senado": 49}

ETAPAS = {
    "camara": ["Apresentada", "CCJ da Câmara", "Comissão Especial", "Plenário da Câmara", "Senado", "Promulgada"],
    "senado": ["Apresentada", "CCJ do Senado", "Plenário do Senado", "Câmara", "Promulgada"],
}


def get(url, tentativas=4):
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "de-olho-nas-pecs/0.1"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if i == tentativas - 1:
                print(f"  ! falhou {url}: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))


def pmap(fn, itens, n=8):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, itens))


def janelas(inicio, fim, dias):
    d = inicio
    while d <= fim:
        e = min(d + dt.timedelta(days=dias - 1), fim)
        yield d, e
        d = e + dt.timedelta(days=1)


def norm_voto(v):
    v = (v or "").strip().lower()
    if v.startswith("sim"):
        return "sim"
    if v.startswith("n"):
        return "nao"
    if v.startswith("abst"):
        return "abstencao"
    if v.startswith("obstr"):
        return "obstrucao"
    return "outro"


def placar_do_texto(txt):
    """Extrai 'Sim: 414; Não: 3; Abstenção: 2' de uma descrição."""
    out = {}
    for chave, rx in (("sim", r"sim\s*[:–-]\s*(\d+)"), ("nao", r"n[ãa]o\s*[:–-]\s*(\d+)"), ("abstencao", r"absten[çc][ãa]o\s*[:–-]\s*(\d+)")):
        m = re.search(rx, txt or "", re.I)
        if m:
            out[chave] = int(m.group(1))
    return out


def turno_do_texto(txt):
    t = (txt or "").lower()
    if "segundo turno" in t or "2º turno" in t:
        return 2
    if "primeiro turno" in t or "1º turno" in t:
        return 1
    return None


# ----------------------------------------------------------------- temas
TEMAS = [
    ("Saúde", r"saúde|sus\b|médic|hospital|enfermag|medicament"),
    ("Educação", r"educa|ensino|escola|universi|professor|fundeb|magistério"),
    ("Impostos e economia", r"tribut|imposto|icms|\bibs\b|contribui[çc][ãa]o social|fiscal|orçament|precatório|dívida|receita|financ|econ"),
    ("Segurança pública", r"segurança pública|polícia|policia|crime|penal|presídio|prisional|armas|guarda municipal|bombeiro"),
    ("Eleições e política", r"eleiç|eleitor|partido|mandato|reeleição|parlamentar|vereador|deputad|senador|candidat|voto|plebiscito"),
    ("Servidores e aposentadoria", r"servidor|aposentad|previd|pensão|remunera|teto|carreira"),
    ("Meio ambiente e terra", r"ambient|clima|florest|amazôn|indígen|terra|agr[áa]ri|rural|água|mineração"),
    ("Direitos e cidadania", r"direito|mulher|criança|idos|deficiên|igualdade|racial|liberdade|família|moradia|alimenta|renda básica|social"),
    ("Estados e municípios", r"município|estados|distrito federal|federativ|fundo de participação|\bfpm\b|região metropolitana|território"),
    ("Justiça e tribunais", r"judici|tribunal|\bstf\b|supremo|ministério público|magistrat|juiz|defensoria|advocacia"),
    ("Trabalho", r"trabalh|empreg|salário|sindica|jornada"),
]


def detectar_tema(*textos):
    t = " ".join(x or "" for x in textos).lower()
    for nome, rx in TEMAS:
        if re.search(rx, t):
            return nome
    return "Outros"


# ----------------------------------------------------------------- CÂMARA
def camara_ids(desde=INICIO_LEGISLATURA):
    """PECs da Câmara com alguma tramitação entre `desde` e hoje."""
    ids = {}
    for ini, fim in janelas(desde, HOJE, 90):  # API limita a 3 meses
        pag = 1
        while True:
            r = get(f"{CAMARA}/proposicoes?siglaTipo=PEC&dataInicio={ini}&dataFim={fim}&itens=100&pagina={pag}")
            if not r:
                break
            for x in r["dados"]:
                ids[x["id"]] = x
            if not any(link["rel"] == "next" for link in r.get("links", [])):
                break
            pag += 1
    return list(ids)


_VIDEOS = {}   # evento da Câmara -> link do vídeo da sessão (uma consulta por sessão)


def video_da_votacao(id_votacao):
    """Link do vídeo da sessão (YouTube) e horário em que a votação foi registrada, pela API da Câmara."""
    det = (get(f"{CAMARA}/votacoes/{id_votacao}") or {}).get("dados") or {}
    ev = det.get("idEvento")
    if ev and ev not in _VIDEOS:
        _VIDEOS[ev] = ((get(f"{CAMARA}/eventos/{ev}") or {}).get("dados") or {}).get("urlRegistro") or ""
    hora = (det.get("dataHoraRegistro") or "")[11:16]
    return {"video": _VIDEOS.get(ev) or "", "hora": hora}


def completar_videos(registros):
    """Acrescenta vídeo e horário às votações da Câmara que ainda não têm (inclui o cache antigo).
    Votações antigas, sem o id da votação, são procuradas uma vez pela data e pela descrição."""
    for p in registros.values():
        if p.get("casa") != "camara" or not p.get("votacoes"):
            continue
        faltam = [v for v in p["votacoes"] if v.get("casa") == "camara" and "video" not in v]
        if not faltam:
            continue
        if any("idVotacao" not in v for v in faltam):
            lista = (get(f"{CAMARA}/proposicoes/{p['id'][1:]}/votacoes") or {}).get("dados", [])
            for v in faltam:
                if "idVotacao" not in v:
                    achou = next((x for x in lista if x.get("data") == v["data"] and x.get("descricao") == v["descricao"]), None)
                    v["idVotacao"] = achou["id"] if achou else ""
        for v in faltam:
            v.update(video_da_votacao(v["idVotacao"]) if v["idVotacao"] else {"video": "", "hora": ""})


def camara_votacoes(pid):
    """Votações de plenário em 1º/2º turno da própria PEC, com o voto de cada deputado."""
    r = get(f"{CAMARA}/proposicoes/{pid}/votacoes") or {"dados": []}
    out = []
    for v in r["dados"]:
        if v["siglaOrgao"] != "PLEN" or v.get("proposicaoObjeto"):
            continue
        turno = turno_do_texto(v["descricao"])
        if not turno:
            continue
        votos_r = get(f"{CAMARA}/votacoes/{v['id']}/votos") or {"dados": []}
        votos = [
            [x["deputado_"]["nome"], x["deputado_"].get("siglaPartido") or "", x["deputado_"].get("siglaUf") or "", norm_voto(x["tipoVoto"])]
            for x in votos_r["dados"]
        ]
        placar = placar_do_texto(v["descricao"])
        if not votos and not placar:
            continue
        if votos:
            placar = {k: sum(1 for x in votos if x[3] == k) for k in ("sim", "nao", "abstencao")}
        out.append({
            "casa": "camara",
            "data": v["data"],
            "turno": turno,
            "descricao": v["descricao"],
            "aprovada": bool(v.get("aprovacao")),
            "placar": placar,
            "necessarios": QUORUM["camara"],
            "votos": votos,
            "idVotacao": v["id"],
        })
    # mantém só a votação principal de cada turno (a com mais votos registrados)
    melhor = {}
    for v in out:
        k = v["turno"]
        principal = bool(re.search(r"proposta de emenda|substitutivo|emenda aglutinativa", v["descricao"], re.I))
        peso = (principal, sum(v["placar"].values()))
        if k not in melhor or peso > melhor[k][0]:
            melhor[k] = (peso, v)
    return sorted((v for _, v in melhor.values()), key=lambda v: v["turno"])


def camara_pec(pid):
    d = get(f"{CAMARA}/proposicoes/{pid}")
    if not d:
        return None
    d = d["dados"]
    st = d.get("statusProposicao") or {}
    tram = (get(f"{CAMARA}/proposicoes/{pid}/tramitacoes") or {"dados": []})["dados"]
    autores = (get(f"{CAMARA}/proposicoes/{pid}/autores") or {"dados": []})["dados"]
    votacoes = camara_votacoes(pid)

    situacao = (st.get("descricaoSituacao") or "").strip()
    orgao = st.get("siglaOrgao") or ""
    sit = situacao.lower()
    ultimos = [t for t in tram if t.get("despacho")][-6:]
    textos_recentes = " ".join(t["despacho"] for t in ultimos).lower()

    # ---- em que etapa do caminho a PEC está
    aprovada_2t = any(v["turno"] == 2 and v["aprovada"] for v in votacoes)
    ccj_ok = any(t.get("siglaOrgao") == "CCJC" and "aprovad" in (t.get("despacho") or "").lower() and "parecer" in (t.get("despacho") or "").lower() for t in tram)
    if "norma jurídica" in sit or "promulga" in sit:
        etapa = 5
    elif "senado" in sit or aprovada_2t:
        etapa = 4
    elif orgao == "PLEN" or ("pronta para pauta" in sit and orgao not in ("CCJC",) and not orgao.startswith("PEC")):
        etapa = 3
    elif orgao.startswith("PEC") or "comissão temporária" in sit or "comissão especial" in sit or (ccj_ok and orgao != "CCJC"):
        etapa = 2
    elif orgao == "CCJC":
        etapa = 1
    else:
        etapa = 0
    if aprovada_2t and "promulga" in textos_recentes:
        etapa = max(etapa, 4)

    estado = "andando"
    if "norma jurídica" in sit:
        estado = "promulgada"
    elif "arquivad" in sit or "arquivo" in sit:
        estado = "aprovada" if aprovada_2t else "arquivada"
    elif "conjunto" in sit:
        estado = "apensada"
    elif "retirado" in sit or "perdeu" in sit or "nova proposição" in sit:
        estado = "encerrada"

    principal = None
    if d.get("uriPropPrincipal"):
        principal = "c" + d["uriPropPrincipal"].rstrip("/").split("/")[-1]

    autores = sorted(autores, key=lambda a: (not a.get("proponente"), a.get("ordemAssinatura") or 999))
    autor = autores[0]["nome"] if autores else ""
    if len(autores) > 1:
        autor += " e outros"
    # [nome, casa, é autor principal?]
    lista_autores = [[a["nome"], "senado" if "senador" in (a.get("tipo") or "").lower() else "camara", 1 if (a.get("proponente") or i == 0) else 0]
                     for i, a in enumerate(autores) if a.get("nome")]
    # relatores: só quando há designação de fato. O campo uriUltimoRelator repete o relator anterior
    # nas etapas seguintes (ex.: o relator da CCJ aparecia como se fosse da comissão especial).
    relatores_ids = []
    for t in tram:
        u = t.get("uriUltimoRelator")
        desc = f"{t.get('descricaoTramitacao') or ''} {t.get('despacho') or ''}"
        if u and re.search(r"Designa[çc][ãa]o de Relator|Designad[oa] Relator", desc) and (u, t.get("siglaOrgao")) not in relatores_ids:
            relatores_ids.append((u, t.get("siglaOrgao") or ""))

    return {
        "id": f"c{pid}",
        "casa": "camara",
        "casaOrigem": "camara",
        "numero": d["numero"],
        "ano": d["ano"],
        "sigla": f"PEC {d['numero']}/{d['ano']}",
        "ementa": d.get("ementa") or "",
        "explicacao": d.get("ementaDetalhada") or "",
        "autor": autor,
        "autores": lista_autores,
        "_relatoresCamara": relatores_ids,
        "idCamara": pid,
        "textoOriginal": {"url": d.get("urlInteiroTeor") or "", "data": (d.get("dataApresentacao") or "")[:10]},
        "textoAtual": textos.versao_camara(pid, get),
        "apresentacao": (d.get("dataApresentacao") or "")[:10],
        "situacao": situacao or "Sem informação",
        "orgaoAtual": orgao,
        "ultimaMovimentacao": (st.get("dataHora") or "")[:10],
        "etapa": etapa,
        "estado": estado,
        "principal": principal,
        "tema": detectar_tema(d.get("ementa"), d.get("keywords")),
        "palavrasChave": d.get("keywords") or "",
        "linkOficial": f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={pid}",
        "linkTexto": d.get("urlInteiroTeor") or "",
        "movimentacoes": [
            {"data": t["dataHora"][:10], "orgao": t.get("siglaOrgao") or "", "texto": t["despacho"].strip()}
            for t in reversed(ultimos)
        ],
        "votacoes": votacoes,
    }


def nome_colegiado(sigla):
    """Só conta relatoria em comissão ou plenário (a Câmara repete o relator em etapas burocráticas)."""
    if sigla in ("CCJC", "CCJ"):
        return "CCJ"
    if sigla.startswith("PEC"):
        return "Comissão Especial"
    if sigla == "PLEN":
        return "Plenário"
    return None


# ----------------------------------------------------------------- SENADO
SEN_PLENARIO = ("PRONDEPLEN", "INCLOD", "PRONTOPAUTA")


def senado_votacoes_por_processo(desde_ano=INICIO_LEGISLATURA.year):
    por = {}
    for ano in range(desde_ano, HOJE.year + 1):
        r = get(f"{SENADO}/votacao?dataInicio={ano}-01-01&dataFim={ano}-12-31") or []
        for v in r:
            if v.get("sigla") != "PEC":
                continue
            turno = turno_do_texto(v.get("descricaoVotacao"))
            if not turno:
                continue
            votos = [
                [x.get("nomeParlamentar", ""), x.get("siglaPartidoParlamentar") or "", x.get("siglaUFParlamentar") or "", norm_voto(x.get("siglaVotoParlamentar"))]
                for x in v.get("votos") or []
            ]
            placar = {k: sum(1 for x in votos if x[3] == k) for k in ("sim", "nao", "abstencao")}
            if not votos and v.get("totalVotosSim") is not None:
                placar = {"sim": v["totalVotosSim"] or 0, "nao": v["totalVotosNao"] or 0, "abstencao": v["totalVotosAbstencao"] or 0}
            por.setdefault(v["idProcesso"], []).append({
                "casa": "senado",
                "data": v["dataSessao"],
                "turno": turno,
                "descricao": v.get("descricaoVotacao") or "",
                "aprovada": v.get("resultadoVotacao") == "A",
                "placar": placar,
                "necessarios": QUORUM["senado"],
                "votos": votos,
            })
    for k, lst in por.items():
        melhor = {}
        for v in lst:
            peso = sum(v["placar"].values())
            if v["turno"] not in melhor or peso > melhor[v["turno"]][0]:
                melhor[v["turno"]] = (peso, v)
        por[k] = sorted((v for _, v in melhor.values()), key=lambda v: v["turno"])
    return por


def senado_pec(pid, votacoes):
    d = get(f"{SENADO}/processo/{pid}")
    if not d:
        return None
    aut = (d.get("autuacoes") or [{}])[0]
    informes = aut.get("informesLegislativos") or []
    sigla_sit = ""
    if aut.get("situacoes"):
        sigla_sit = aut["situacoes"][-1].get("sigla", "")
    colegiado = aut.get("siglaColegiadoControleAtual") or ""
    situacao = d.get("situacaoAtual") or ""
    revisora = d.get("objetivo") == "Revisora"
    origem = "camara" if revisora else "senado"
    norma = d.get("normaGerada") or {}
    aprovada_2t = any(v["turno"] == 2 and v["aprovada"] for v in votacoes)
    tramitando = d.get("tramitando") == "Sim"

    if origem == "senado":
        if norma:
            etapa = 4
        elif aprovada_2t:
            etapa = 3
        elif colegiado == "PLEN" or sigla_sit in SEN_PLENARIO or votacoes:
            etapa = 2
        elif colegiado == "CCJ" or "relator" in situacao.lower() or "comiss" in situacao.lower():
            etapa = 1
        else:
            etapa = 0
    else:  # veio da Câmara: já está na etapa "Senado"
        etapa = 5 if (norma or aprovada_2t) else 4

    if norma:
        estado = "promulgada"
    elif "REJEITAD" in situacao.upper():
        estado = "rejeitada"
    elif not tramitando:
        estado = "aprovada" if aprovada_2t else "arquivada"
    else:
        estado = "andando"

    ementa = (d.get("conteudo") or {}).get("ementa") or ""
    doc = d.get("documento") or {}
    autoria = sorted(d.get("autoriaIniciativa") or doc.get("autoria") or [], key=lambda a: a.get("ordem") or 999)
    lista_autores = [[a["autor"], "senado" if a.get("siglaTipo") == "SENADOR" else "camara", 1 if i == 0 else 0]
                     for i, a in enumerate(autoria) if a.get("autor") and (a.get("siglaTipo") == "SENADOR" or "DEPUTADO" in (a.get("siglaTipo") or ""))]
    texto_original, texto_atual = textos.versoes_senado(pid, get)
    rel = get(f"{SENADO}/processo/relatoria?codigoMateria={d.get('codigoMateria')}") or []
    relatores = []
    for r in rel:
        item = [r.get("nomeParlamentar"), "senado", r.get("siglaColegiado") or ""]
        if item[0] and item not in relatores:
            relatores.append(item)
    return {
        "id": f"s{pid}",
        "casa": "senado",
        "casaOrigem": origem,
        "numero": int(d.get("numero") or 0),
        "ano": int(d.get("ano") or 0),
        "sigla": d.get("identificacao") or "",
        "ementa": ementa,
        "explicacao": (d.get("conteudo") or {}).get("explicacaoEmenta") or "",
        "autor": doc.get("resumoAutoria") or "",
        "autores": lista_autores,
        "relatores": relatores,
        "codigoMateria": d.get("codigoMateria"),
        "origemCamara": d.get("identificacaoProcessoInicial") if d.get("siglaCasaIniciadora") == "CD" else None,
        "numeroCamara": numero_na_camara(d),
        "textoOriginal": texto_original,
        "textoAtual": texto_atual,
        "apresentacao": (doc.get("dataApresentacao") or "")[:10],
        "situacao": situacao.capitalize() if situacao.isupper() else situacao,
        "orgaoAtual": colegiado,
        "ultimaMovimentacao": (d.get("dataSituacaoAtual") or (informes[-1]["data"] if informes else ""))[:10],
        "etapa": etapa,
        "estado": estado,
        "principal": None,
        "tema": detectar_tema(ementa, (d.get("conteudo") or {}).get("assuntoEspecifico"), doc.get("indexacao")),
        "palavrasChave": doc.get("indexacao") or "",
        "linkOficial": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{d.get('codigoMateria')}",
        "linkTexto": "",
        "movimentacoes": [
            {"data": i["data"][:10], "orgao": (i.get("colegiado") or {}).get("sigla", ""), "texto": (i.get("descricao") or "").strip()}
            for i in reversed(informes[-6:])
        ],
        "votacoes": votacoes,
    }


# ----------------------------------------------------------------- junção
PRIORIDADE_ESTADO = ["promulgada", "rejeitada", "andando", "aprovada", "apensada", "arquivada", "encerrada"]


def numero_na_camara(det):
    """Número que a PEC do Senado ganhou ao chegar à Câmara (ex.: PEC 2/2016 → "PEC 20/2025"), ou None."""
    for o in det.get("outrosNumeros") or []:
        if o.get("casaIdentificadora") == "CD" and o.get("sigla") == "PEC" and o.get("numero") and o.get("ano"):
            return f"PEC {int(o['numero'])}/{o['ano']}"
    return None


def juntar(camara, senado):
    """Desde 2019 a numeração é única no Congresso: a PEC X/ano é a mesma nas duas Casas
    (e pode ter mais de um registro, p.ex. quando volta para a Casa de origem: "fase 2")."""
    grupos = {}
    for p in camara + senado:
        chave = None
        if p["casa"] == "camara":
            chave = (p["numero"], p["ano"])
        elif p.get("origemCamara"):            # começou na Câmara com outro número (ex.: PEC 31/2007 → PEC 54/2024)
            m = re.match(r"PEC (\d+)/(\d{4})", p["origemCamara"])
            chave = (int(m.group(1)), int(m.group(2))) if m else None
        elif p.get("numeroCamara"):            # começou no Senado e ganhou outro número na Câmara (ex.: PEC 2/2016 → PEC 20/2025)
            m = re.match(r"PEC (\d+)/(\d{4})", p["numeroCamara"])
            chave = (int(m.group(1)), int(m.group(2))) if m else None
        elif p["ano"] >= 2019:                 # numeração única no Congresso desde 2019
            chave = (p["numero"], p["ano"])
        grupos.setdefault(chave or ("s", p["id"]), []).append(p)
    final = []
    for chave, regs in grupos.items():
        if len(regs) == 1:
            final.append(regs[0])
            continue
        sen = [r for r in regs if r["casa"] == "senado"]
        cam = [r for r in regs if r["casa"] == "camara"]
        origem = "senado" if any(r["casaOrigem"] == "senado" for r in sen) else "camara"
        da_origem = sorted((r for r in regs if r["casa"] == origem), key=lambda r: "fase" in r["sigla"]) or regs
        base = dict(da_origem[0])
        if origem == "camara":
            base["sigla"] = next((r["sigla"] for r in cam), base["sigla"])
            outras = sorted({r["sigla"] for r in sen if r["sigla"] != base["sigla"]})
            if outras:
                base["siglaSenado"] = ", ".join(outras)   # mesmo processo, outro número no Senado
        else:
            base["sigla"] = re.sub(r"\s*\(.*?\)", "", base["sigla"])
            outras = sorted({re.sub(r"\s*\(.*?\)", "", r["sigla"]) for r in cam} - {base["sigla"]})
            if outras:
                base["siglaCamara"] = ", ".join(outras)   # mesmo processo, outro número na Câmara
        base["casaOrigem"] = origem
        base["casa"] = "ambas" if sen and cam else base["casa"]
        base["idsRelacionados"] = [r["id"] for r in regs]
        vistos, votacoes = set(), []
        for v in sorted((v for r in regs for v in r["votacoes"]), key=lambda v: v["data"]):
            k = (v["casa"], v["turno"], v["data"])
            if k not in vistos:
                vistos.add(k)
                votacoes.append(v)
        base["votacoes"] = votacoes
        vistos_a, autores = set(), []
        for r in regs:
            for a in r.get("autores", []):
                # a Câmara registra autores de PECs do Senado como "Senado Federal - Fulano"
                m = re.match(r"Senado Federal\s*-\s*(.+)", a[0])
                if m:
                    a = [m.group(1).strip(), "senado", a[2]]
                if (a[0], a[1]) not in vistos_a:
                    vistos_a.add((a[0], a[1]))
                    autores.append(a)
        base["autores"] = autores
        base["relatores"] = []
        for r in regs:
            for x in r.get("relatores", []):
                if x not in base["relatores"]:
                    base["relatores"].append(x)
        atuais = [r["textoAtual"] for r in regs if r.get("textoAtual")]
        base["textoAtual"] = max(atuais, key=lambda t: t["data"]) if atuais else None
        base["codigoMateria"] = next((r.get("codigoMateria") for r in sen if r.get("codigoMateria")), None)
        base["idCamara"] = next((r.get("idCamara") for r in cam if r.get("idCamara")), None)
        base["movimentacoes"] = sorted((m for r in regs for m in r["movimentacoes"]), key=lambda m: m["data"], reverse=True)[:8]
        base["explicacao"] = next((r["explicacao"] for r in sen if r["explicacao"]), base["explicacao"])
        if cam:
            base["linkCamara"] = cam[0]["linkOficial"]
        if sen:
            base["linkSenado"] = sen[0]["linkOficial"]
        # etapa no caminho da Casa de origem
        etapas = [r["etapa"] for r in regs if r["casa"] == origem]
        if origem == "senado":
            etapas += [4 if r["etapa"] == 5 else 3 for r in cam]  # na Câmara = etapa "Câmara" ou promulgada
        else:
            etapas += [r["etapa"] for r in sen]
        base["etapa"] = max(etapas)
        base["estado"] = min((r["estado"] for r in regs), key=PRIORIDADE_ESTADO.index)
        base["ultimaMovimentacao"] = max(r["ultimaMovimentacao"] for r in regs)
        final.append(base)
    return final


def ler_json(path, padrao):
    return json.loads(path.read_text()) if path.exists() else padrao


def resolver_relatores(recs, nomes):
    """Troca as URIs de relatores da Câmara por nomes, usando e completando o cache `nomes`."""
    uris = sorted({u for p in recs for u, _ in p["_relatoresCamara"] if u not in nomes})
    if uris:
        print(f"  {len(uris)} relatores novos na Câmara; buscando nomes…")
        nomes.update(zip(uris, pmap(lambda u: ((get(u) or {}).get("dados") or {}).get("ultimoStatus", {}).get("nomeEleitoral"), uris)))
    for p in recs:
        rel, vistos = [], set()
        for u, org in p.pop("_relatoresCamara"):
            org = nome_colegiado(org)
            if nomes.get(u) and org and (nomes[u], org) not in vistos:
                vistos.add((nomes[u], org))
                rel.append([nomes[u], "camara", org])
        p["relatores"] = rel


def main():
    completo = "--completo" in sys.argv
    CACHE.mkdir(parents=True, exist_ok=True)
    estado = ler_json(CACHE / "estado.json", {})
    cam = {} if completo else ler_json(CACHE / "camara.json", {})
    sen = {} if completo else ler_json(CACHE / "senado.json", {})
    nomes = ler_json(CACHE / "relatores.json", {})
    ultima = dt.date.fromisoformat(estado["ultimaRodada"]) if estado.get("ultimaRodada") else None
    if not cam or not sen or not ultima or (HOJE - ultima).days > MAX_DIAS_INCREMENTAL:
        completo = True
    # começa um dia antes da última rodada, para não perder nada registrado com atraso
    desde = INICIO_LEGISLATURA if completo else ultima - dt.timedelta(days=1)
    print("Rodada", "COMPLETA" if completo else f"INCREMENTAL (mudanças desde {desde})")

    # ---- Câmara
    ids = camara_ids(desde)
    print(f"Câmara: {len(ids)} PECs com movimentação; baixando…")
    novos = [p for p in pmap(camara_pec, ids) if p]
    resolver_relatores(novos, nomes)
    for p in novos:
        cam[p["id"]] = p

    # ---- Senado
    votos_sen = senado_votacoes_por_processo(desde.year)
    lista = get(f"{SENADO}/processo?sigla=PEC&tramitando=S") or []
    # PECs que chegaram da Câmara e já saíram de tramitação no Senado (rejeitadas, arquivadas, promulgadas)
    encerradas = [x for x in (get(f"{SENADO}/processo?sigla=PEC&tramitando=N&dataInicioApresentacao=2021-01-01") or [])
                  if x.get("objetivo") == "Revisora" and (x.get("dataSituacaoAtual") or "") >= INICIO_LEGISLATURA.isoformat()]
    lista = lista + encerradas
    if completo:
        pids = {x["id"] for x in lista} | set(votos_sen)
    else:
        # nova na lista, situação mudou desde a última rodada, ou votação nova
        pids = {x["id"] for x in lista if f"s{x['id']}" not in sen or (x.get("dataSituacaoAtual") or "") >= desde.isoformat()}
        pids |= {pid for pid, vs in votos_sen.items() if any(v["data"] >= desde.isoformat() for v in vs)}
    print(f"Senado: {len(pids)} PECs para baixar…")
    for p in pmap(lambda pid: senado_pec(pid, votos_sen.get(pid, [])), sorted(pids)):
        if p:
            if not p["votacoes"] and sen.get(p["id"], {}).get("votacoes"):
                p["votacoes"] = sen[p["id"]]["votacoes"]   # votação de anos anteriores já guardada
            sen[p["id"]] = p

    # PECs do Senado que foram à Câmara com outro número (uma consulta por PEC, só para o cache antigo)
    faltam = [r["id"] for r in sen.values() if r.get("casaOrigem") == "senado" and "numeroCamara" not in r]
    for sid, det in zip(faltam, pmap(lambda sid: get(f"{SENADO}/processo/{sid[1:]}"), faltam)):
        sen[sid]["numeroCamara"] = numero_na_camara(det or {})

    faltam = [r["id"] for r in sen.values() if r.get("casaOrigem") == "camara" and "origemCamara" not in r]
    for sid, det in zip(faltam, pmap(lambda sid: get(f"{SENADO}/processo/{sid[1:]}"), faltam)):
        det = det or {}
        sen[sid]["origemCamara"] = det.get("identificacaoProcessoInicial") if det.get("siglaCasaIniciadora") == "CD" else None

    # PECs que começaram na Câmara e não se mexeram lá nesta legislatura (ex.: PEC 39/2011 → PEC 3/2022):
    # sem o registro da Câmara, a página perde a apresentação, as votações e os relatores de lá
    na_camara = {(p["numero"], p["ano"]) for p in cam.values()}
    pedir = sorted({(int(m[1]), int(m[2])) for r in sen.values()
                    if (m := re.match(r"PEC (\d+)/(\d{4})", r.get("origemCamara") or "")) and (int(m[1]), int(m[2])) not in na_camara})
    ids_antigos = [x["id"] for nk in pmap(lambda nk: get(f"{CAMARA}/proposicoes?siglaTipo=PEC&numero={nk[0]}&ano={nk[1]}"), pedir)
                   for x in ((nk or {}).get("dados") or [])]
    if ids_antigos:
        print(f"Câmara: {len(ids_antigos)} PECs antigas que seguiram para o Senado; baixando…")
        antigos = [p for p in pmap(camara_pec, ids_antigos) if p]
        resolver_relatores(antigos, nomes)
        for p in antigos:
            cam[p["id"]] = p

    # vídeo da sessão de cada votação da Câmara (só as que ainda não têm; uma vez por votação)
    completar_videos(cam)

    # ---- grava o cache (antes de juntar, que reaproveita os mesmos objetos)
    for nome, dados in (("camara", cam), ("senado", sen), ("relatores", nomes)):
        (CACHE / f"{nome}.json").write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")))
    (CACHE / "estado.json").write_text(json.dumps({"ultimaRodada": HOJE.isoformat(), "completa": completo}))

    # ---- junta, aplica os resumos e grava
    pecs = juntar(list(cam.values()), list(sen.values()))
    for p in pecs:   # a data da ficha pode ficar atrás da movimentação mais recente (ex.: Senado registra 31/08, movimenta em 02/09)
        p["ultimaMovimentacao"] = max([p["ultimaMovimentacao"] or ""] + [m["data"] for m in p.get("movimentacoes") or []])
    pecs.sort(key=lambda p: p["ultimaMovimentacao"], reverse=True)
    resumos = ler_json(RAIZ / "data" / "resumos.json", {})
    comparados = ler_json(CACHE / "textos.json", {})     # {versão do texto: semelhança com o original}
    pareceres = ler_json(CACHE / "pareceres.json", {})   # {parecer: aprovou o texto sem mudanças?}
    sem_resumo, a_revisar = 0, []
    for p in pecs:
        # quando duas PECs se juntam, o id pode mudar (s… → c…): o resumo continua valendo pelo id antigo
        rid = next((i for i in [p["id"], *p.get("idsRelacionados", [])] if i in resumos), None)
        if rid:
            p["simples"] = resumos[rid]
        else:
            sem_resumo += 1
        atual = p.get("textoAtual")
        if not atual:
            continue
        if atual["id"] not in comparados:     # só baixa PDFs quando aparece uma versão nova
            orig = (p.get("textoOriginal") or {}).get("url")
            comparados[atual["id"]] = textos.similaridade(textos.baixar_texto(orig), textos.baixar_texto(atual["url"])) if orig else None
            print(f"  versão nova do texto: {p['sigla']} ({atual['tipo']}, {atual['data']}) semelhança={comparados[atual['id']]}")
        atual["semelhanca"] = comparados[atual["id"]]
        if atual["tipo"].startswith("Parecer"):
            if atual["id"] not in pareceres:      # só baixa uma vez por parecer
                pareceres[atual["id"]] = textos.parecer_sem_mudanca(textos.baixar_texto(atual["url"]))
            if pareceres[atual["id"]]:
                atual["mesmoTexto"] = True
        base = (p.get("simples") or {}).get("base") or {}
        # parecer que manteve o texto: o resumo feito de uma versão posterior à original continua valendo
        sem_mudanca = atual.get("mesmoTexto") and (base.get("versao") not in (None, "original") or p.get("casaOrigem") == "senado")
        if p.get("simples") and base.get("versao") != atual["id"] and not sem_mudanca:
            a_revisar.append({"id": p["id"], "sigla": p["sigla"], "titulo": p["simples"].get("titulo"), "versao": atual["tipo"], "data": atual["data"], "url": atual["url"], "semelhanca": atual["semelhanca"]})
    (CACHE / "textos.json").write_text(json.dumps(comparados))
    (CACHE / "pareceres.json").write_text(json.dumps(pareceres))
    a_revisar.sort(key=lambda x: x["data"], reverse=True)
    (RAIZ / "data" / "resumos_a_revisar.json").write_text(json.dumps(a_revisar, ensure_ascii=False, indent=1))

    # links do Senado sem número de versão (o link com versão quebra quando sai uma nova; vale também para o cache antigo)
    for p in pecs:
        for campo in ("textoAtual", "textoOriginal"):
            if p.get(campo) and p[campo].get("url"):
                p[campo]["url"] = textos.url_senado(p[campo]["url"])
        if p.get("linkTexto"):
            p["linkTexto"] = textos.url_senado(p["linkTexto"])

    # só regrava (e portanto só republica o site) se algum dado mudou de fato
    anterior = ler_json(SAIDA, {})
    if anterior.get("pecs") == json.loads(json.dumps(pecs)):
        publicar.gerar_site(anterior)     # normalmente não grava nada; só refaz arquivos que faltem
        print("Nenhuma mudança nos dados: site não precisa ser republicado.")
        return
    dados = {
        "atualizadoEm": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "etapas": ETAPAS,
        "quorum": QUORUM,
        "pecs": pecs,
    }
    publicar.salvar_dados(dados)
    n = publicar.gerar_site(dados)
    print(f"OK: {len(pecs)} PECs ({len(novos)} da Câmara e {len(pids)} do Senado atualizadas; {sem_resumo} sem resumo; "
          f"{len(a_revisar)} com texto novo a revisar); {n} arquivo(s) do site atualizados")


if __name__ == "__main__":
    main()
