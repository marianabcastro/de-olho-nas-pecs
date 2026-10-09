# Onde paramos (9/out/2026)

## Passo 1 (dados divididos + PWA): FEITO
- Dados completos em `data/pecs.json` e `data/resumos.json` (não vão para o site).
- `scripts/publicar.py` gera `site/data/lista.json`, `pec/NN.json` (64 pacotes), `pessoas.json` e `traducoes-en/es.json`.
- PWA: `site/manifest.webmanifest`, `site/sw.js`, `site/icones/`. Testado offline.
- Próximo: passo 4 (GitHub + Cloudflare), junto com a dona do projeto.

## Checagem de fatos: FEITA (itens 1 a 3)
- Resultado e pendências em `revisao/para-revisar.md`, seção 4.
- 129 resumos conferidos com o texto oficial; 34 corrigidos. Backup do resumos.json anterior no scratchpad da sessão.
- Publicado no link do protótipo (versão 6).

## Já feito
- Site com PT/EN/ES, resumos simples, selos de revisão, aviso de "texto mudou", conversa "Descubra" (só PT).
- Robô incremental (`scripts/atualizar_dados.py`): baixa só o que mudou; não regrava se nada mudou.
- Detecção de versões novas do texto (`scripts/textos.py`): substitutivo, emenda aglutinativa, redação final, parecer.
- Junção de PECs com números diferentes na Câmara e no Senado (ex.: PEC 31/2007 = PEC 54/2024 no Senado). Código pronto, **ainda não rodado**.
- `scripts/gerar_resumos.py`: resume a partir do texto completo, em PT/EN/ES, e diz se o objetivo mudou.
- GitHub: workflow `.github/workflows/atualizar.yml`, `requirements.txt`, `.gitignore`, `scripts/issue_revisao.py`.
- Lista para revisão humana: `revisao/para-revisar.md`.

## Interrompido
- Rodada completa do robô parou no meio. O cache (`data/cache/`) foi gravado; o `site/data/pecs.json` ainda é a versão antiga.

## Próximos passos
1. Rodar `python3 scripts/atualizar_dados.py` (incremental) para gerar o `pecs.json` novo com versões de texto e a junção Câmara/Senado.
2. Conferir a PEC 54/2024 / 31/2007 (votos da Câmara aparecendo) e procurar outros casos de números diferentes.
3. Checagem das 87 PECs mais visíveis + amostra de 50 com o texto completo (script em scratchpad: `baixar_para_checar.py`).
   - Já sabido: **PEC 221/2019 está desatualizada** — o texto aprovado na Câmara (27/05/2026) é 40h semanais + 2 dias de descanso, não 36h.
   - PEC 31/2007: resumo deve descrever o pacote de corte de gastos (confirmado pela API do Senado).
4. Marcar no `resumos.json` o campo `base` de cada resumo (`ementa` / `texto` / `pessoa`).
5. Publicar a nova versão no link do protótipo.
6. GitHub: criar o repositório, ativar Pages, (opcional) segredo `ANTHROPIC_API_KEY`, preencher `REPO_ERROS`.
