# De Olho nas PECs (protótipo)

Site que acompanha, em linguagem simples, todas as Propostas de Emenda à Constituição
que tiveram alguma movimentação na Câmara ou no Senado desde 01/02/2023 (início da
legislatura atual), qualquer que seja o ano em que foram apresentadas.

## Como funciona

```
scripts/atualizar_dados.py   → baixa das APIs oficiais e gera data/pecs.json e os arquivos do site (~5 min)
scripts/gerar_resumos.py     → (opcional) cria título e resumo simples com a API do Claude
site/index.html              → o site (HTML/CSS/JS puros, sem build)
```

Fontes: API de Dados Abertos da Câmara (`dadosabertos.camara.leg.br/api/v2`) e do Senado
(`legis.senado.leg.br/dadosabertos`). Desde 2019 a numeração de PECs é única no Congresso,
então a PEC X/ano é juntada num registro só quando passa pelas duas Casas.

## Rodar localmente

```bash
pip install -r requirements.txt
python3 scripts/atualizar_dados.py             # incremental (a primeira vez baixa tudo)
python3 scripts/atualizar_dados.py --completo  # baixa tudo de novo
python3 -m http.server 8765 --directory site   # abra http://localhost:8765
```

## Como o site se mantém atualizado (GitHub)

O arquivo `.github/workflows/atualizar.yml` roda o robô no GitHub Actions:

- **Quando:** duas vezes por dia em dias úteis, às 9h05 e às 21h05 (Brasília); uma vez por dia nos fins de semana;
  e uma rodada completa aos domingos de madrugada.
- **Incremental:** cada rodada consulta as APIs e baixa de novo só as PECs que se mexeram desde a última vez.
  O que já foi baixado fica no cache do GitHub Actions (não no repositório).
- **Só publica se mudou:** se nenhum dado mudou, nada é salvo nem publicado.
- **Texto novo:** quando uma PEC ganha uma versão nova do texto (substitutivo, redação final, parecer aprovado),
  o robô compara com o original e coloca a PEC em `data/resumos_a_revisar.json`. Com a chave da API, o resumo é
  refeito a partir do texto novo e o site mostra o que mudou e se o objetivo da PEC mudou. Uma issue
  "Resumos para revisar" no GitHub lista o que precisa de olhar humano.
- **Publicação:** o site vai para o Cloudflare (Workers com arquivos estáticos, plano gratuito). Os dados e o
  histórico ficam no GitHub; o Cloudflare só serve os arquivos.

### Configuração (uma vez)

1. Crie um repositório vazio no GitHub e envie esta pasta.
2. No Cloudflare (conta gratuita), crie um token em **My Profile → API Tokens → Create Token → modelo
   "Edit Cloudflare Workers"** e copie o **Account ID** (aparece na página inicial da conta, em "Workers & Pages").
3. No GitHub, em **Settings → Secrets and variables → Actions**, crie os segredos `CLOUDFLARE_API_TOKEN` e
   `CLOUDFLARE_ACCOUNT_ID`. (Opcional, para resumos automáticos: `ANTHROPIC_API_KEY`.)
4. Em **Actions → Atualizar dados e publicar → Run workflow** para rodar a primeira vez.
   O site fica em `https://deolhonaspecs.com.br`. O endereço antigo (`de-olho-nas-pecs.deolhonaspecs.workers.dev`), numa conta do Cloudflare sem acesso, só redireciona para o novo (pasta `redirect/`).

## Revisão humana

- `revisao/para-revisar.md`: ementas vagas, contexto acrescentado e apelidos para conferir.
- Cada resumo tem uma marcação `base.conferido`: `ementa` (só a ementa), `texto` (texto completo) ou
  `pessoa` (revisado por alguém). O site mostra isso em cada PEC. Para marcar um resumo como revisado, mude para
  `"conferido": "pessoa"` e coloque a data em `"data"` no `data/resumos.json`.
