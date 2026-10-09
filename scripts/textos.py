"""
Versões do texto de uma PEC: o texto original e o texto mais recente aprovado
(substitutivo da comissão, emenda aglutinativa, redação final, parecer aprovado…).

Usado por atualizar_dados.py para avisar quando o texto em votação deixou de ser o
original, que é a base dos resumos. Os PDFs só são baixados quando aparece uma
versão nova, para não repetir downloads a cada rodada.
"""
import difflib
import io
import re
import urllib.request

CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
SENADO = "https://legis.senado.leg.br/dadosabertos"

# documentos da Câmara que trazem um texto novo, do mais forte para o mais fraco
TIPOS_CAMARA = {
    "RDF": "Redação final aprovada na Câmara",
    "EMA": "Emenda aglutinativa (texto votado no plenário da Câmara)",
    "SBT-A": "Substitutivo aprovado na comissão da Câmara",
}
ORDEM_CAMARA = ["RDF", "EMA", "SBT-A"]


def _get_bytes(url, accept):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "de-olho-nas-pecs/0.2"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def versao_camara(pid, get_json):
    """Texto mais recente aprovado na Câmara (ou None). `get_json` é a função de GET do robô."""
    rel = (get_json(f"{CAMARA}/proposicoes/{pid}/relacionadas") or {}).get("dados", [])
    docs = [d for d in rel if d.get("siglaTipo") in TIPOS_CAMARA]
    if not docs:
        return None
    # a versão mais recente; em empate de data, o tipo mais forte
    docs.sort(key=lambda d: ((d.get("dataApresentacao") or "")[:10], -ORDEM_CAMARA.index(d["siglaTipo"])), reverse=True)
    d = docs[0]
    det = (get_json(f"{CAMARA}/proposicoes/{d['id']}") or {}).get("dados") or {}
    return {
        "id": f"c{d['id']}",
        "casa": "camara",
        "tipo": TIPOS_CAMARA[d["siglaTipo"]],
        "data": (d.get("dataApresentacao") or "")[:10],
        "url": det.get("urlInteiroTeor") or "",
    }


def url_senado(url):
    """Link do Senado sem o número de versão no fim (".../documento/<id>/3.0").
    O link com versão para de funcionar (erro 403) quando sai uma versão nova; sem versão, abre sempre a mais recente."""
    return re.sub(r"(/sdleg-getter/dadosabertos/documento/[0-9a-f-]{36})/\d+(\.\d+)?$", r"\1", url or "")


def versoes_senado(pid, get_json):
    """(texto original, texto mais recente aprovado ou None) no Senado."""
    docs = get_json(f"{SENADO}/processo/documento?idProcesso={pid}") or []
    data = lambda d: (d.get("dataRecebimento") or d.get("dataDocumento") or "")[:10]
    pecs = sorted((d for d in docs if d.get("descricaoTipo") == "Proposta de Emenda à Constituição"), key=data)
    original = {"url": url_senado(pecs[0].get("urlDocumento")), "data": data(pecs[0])} if pecs else None
    candidatos = []
    for d in docs:
        t = d.get("descricaoTipo") or ""
        if t == "Proposta de Emenda à Constituição" and pecs and d is not pecs[0]:
            candidatos.append((data(d), 3, "Texto aprovado no Senado", d))
        elif t == "Parecer de redação":
            candidatos.append((data(d), 2, "Redação para o segundo turno no Senado", d))
        elif t == "Parecer":
            candidatos.append((data(d), 1, "Parecer aprovado em comissão ou no plenário do Senado", d))
    if not candidatos:
        return original, None
    dt_, _, rotulo, d = max(candidatos, key=lambda c: (c[0], c[1]))
    return original, {"id": f"s{d.get('id')}", "casa": "senado", "tipo": rotulo, "data": dt_, "url": url_senado(d.get("urlDocumento"))}


def _texto_de(conteudo):
    """Extrai texto de PDF, Word .docx ou Word .doc (o Senado publica nos três formatos)."""
    if conteudo[:4] == b"%PDF":
        from pypdf import PdfReader
        return " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(conteudo)).pages)
    if conteudo[:2] == b"PK":                                   # .docx é um zip com XML
        import zipfile
        with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        return re.sub(r"<[^>]+>", " ", xml.replace("</w:p>", "\n"))
    if conteudo[:4] == b"\xd0\xcf\x11\xe0":                  # .doc antigo: precisa de ferramenta externa
        import shutil, subprocess, tempfile
        for cmd in (["textutil", "-convert", "txt", "-stdout"], ["antiword"], ["catdoc"]):
            if shutil.which(cmd[0]):
                with tempfile.NamedTemporaryFile(suffix=".doc") as f:
                    f.write(conteudo); f.flush()
                    return subprocess.run(cmd + [f.name], capture_output=True, text=True).stdout
    return ""


def baixar_texto(url, tentativas=3):
    """Texto de um documento oficial (string vazia se não der). Tenta de novo se o download vier cortado."""
    if not url:
        return ""
    import time
    for i in range(tentativas):
        try:
            texto = _texto_de(_get_bytes(url, "*/*"))
            return re.sub(r"\s+", " ", texto)
        except Exception:  # noqa: BLE001 - download cortado, servidor ocupado, arquivo quebrado
            time.sleep(3 * (i + 1))
    return ""


def dispositivos(texto):
    """Só a parte que muda a Constituição: de 'Art. 1º' até a justificação/assinaturas."""
    t = texto
    m = re.search(r"Art\.?\s*1\s*[º°o]", t)
    if m:
        t = t[m.start():]
    fim = re.search(r"JUSTIFICA[ÇC][ÃA]O|Sala das (Sessões|Comissões|Reuniões)|Assinado eletronicamente", t)
    if fim:
        t = t[:fim.start()]
    t = re.sub(r"\*?CD\d{10,}\*?|Apresentação:.*?(PLEN|CCJC|CE)\b", " ", t)   # códigos de autenticação
    return re.sub(r"[^0-9a-zà-ú ]", " ", t.lower()).split()


def parecer_sem_mudanca(texto):
    """True se o voto do parecer aprova a PEC sem mudar o texto: "nos termos em que aprovada pela Câmara",
    "pela sua aprovação." ou rejeitando todas as emendas. Olha só o voto (do título VOTO até "Sala da...")."""
    i = max(texto.rfind("VOTO"), texto.rfind("Voto"))
    voto = texto[i:i + 2500] if i >= 0 else ""
    voto = re.split(r"Sala d[ao]s? (Comiss|Sess)", voto)[0]
    v = re.sub(r"\s+", " ", voto)
    if not v or re.search(r"VOTO EM SEPARADO|rejeição da Proposta|contrári[oa] à Proposta", v):
        return False
    # qualquer emenda aprovada, acolhida ou substitutivo = texto mudou
    if re.search(r"na forma d|substitutiv|com (a|as|uma) (seguintes? )?(emenda|ajuste)|acolhimento|acatamento|"
                 r"aprova\w* (parcial )?(da|das|de) [Ee]mendas?|e (da|das) [Ee]mendas?|ajuste redacional|emendas? de redação", v):
        return False
    return bool(re.search(r"nos termos em que (foi )?aprovada pela Câmara|aprova\w*[^.;]{0,140}(Proposta de Emenda à Constituição|PEC)[^.;]{0,40}\.|pela sua aprova\w*\.", v))


def similaridade(a, b):
    """0 a 1: quão parecidos são os dispositivos de dois textos."""
    da, db = dispositivos(a), dispositivos(b)
    if not da or not db:
        return None
    return round(difflib.SequenceMatcher(None, da, db, autojunk=False).ratio(), 2)
