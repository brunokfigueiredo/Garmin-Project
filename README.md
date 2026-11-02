# Painel de Treino na Vercel

Pipeline Garmin → Painel de Treino rodando sozinho na Vercel. O Mac não precisa estar ligado, e não depende da aba do Chrome.

## Como funciona

```
Vercel Cron (00:07 UTC = 21:07 em São Paulo)
  └─ GET /api/cron  (app.py, Python, até 800 s)
       1. lê os tokens DI do Garmin no Redis (na 1ª vez, da env GARMIN_TOKENS_SEED)
       2. coleta só com GET em connectapi.garmin.com (lib/collector.py, porte do collector.js:
          mesmas chamadas, ≥1,5 s entre elas, para em 403/429)
       3. roda o mesmo daily.py / engine.py / build_data.py de antes (lib/pipeline/)
       4. grava no Upstash Redis: lote_completo, data.json, relatório, log do dia, bruto (30 d)
       5. grava os tokens renovados (o refresh token muda a cada renovação)
Portal:  /            → public/index.html (o mesmo painel), lê /api/data
Claude:  /api/report  → relatório do dia, lido pela rotina do Claude pelo conector da Vercel
```

O projeto inteiro fica atrás da **Vercel Authentication (All Deployments)**: só quem entra com a sua conta Vercel abre o portal e as APIs. No Pro, isso não tem custo extra.

## Instalação (uma vez) — tudo pelo navegador + 3 comandos no Terminal

Pastas no Mac: `~/Desktop/Garmin/Garmin-Project` (este repositório) e `~/Desktop/Garmin/dados_iniciais` (histórico; fica fora do GitHub).
Não precisa de Homebrew, Node nem Vercel CLI. Precisa de Python ≥ 3.12 (python.org).

1. **GitHub** — GitHub Desktop: *Commit to main* → *Push origin*.
2. **Vercel: importar** — vercel.com → *Add New… → Project* → importe `Garmin-Project` → Framework Preset **Other** → *Deploy* (o 1º deploy fica sem dados; é esperado).
3. **Vercel: Redis** — no projeto: *Storage* → **Upstash for Redis** (plano Free) → criar e **Connect** ao projeto (marque Production, Preview e Development).
4. **Vercel: proteção** — *Settings → Deployment Protection* → **Vercel Authentication** ligado, escopo **All Deployments** → *Save*.
5. **Vercel: segredo do cron** — no Terminal: `openssl rand -hex 32 | pbcopy`. Em *Settings → Environment Variables*: nome `CRON_SECRET`, valor = colar, ambiente Production, marcar **Sensitive** → *Save*.
6. **Tokens do Garmin** (você digita a senha no seu Terminal; ela não vai para lugar nenhum):
   ```bash
   cd ~/Desktop/Garmin/Garmin-Project
   python3.12 -m pip install --user garminconnect==0.3.20 upstash-redis==1.9.0
   python3.12 scripts/bootstrap_tokens.py
   pbcopy < garmin_tokens.json && rm garmin_tokens.json
   ```
   Em *Settings → Environment Variables*: nome `GARMIN_TOKENS_SEED`, valor = colar, Production, **Sensitive** → *Save*.
7. **Histórico** — em *Storage → (seu Redis) → aba .env.local* → *Copy Snippet*. No Terminal:
   ```bash
   cd ~/Desktop/Garmin/Garmin-Project
   pbpaste > .env.local
   python3.12 scripts/seed_history.py ../dados_iniciais/lote_completo.json ../dados_iniciais/data.json
   rm .env.local
   ```
8. **Redeploy** — *Deployments* → último deploy → ⋯ → **Redeploy** (para valerem as variáveis).
9. **Teste G0** — *Settings → Cron Jobs* → **Run** em `/api/cron`; depois *Logs* e procure `PIPELINE_RESULT`:
   - `"status": "done"` → funcionou; abra o domínio do projeto (*Domains*): é o portal.
   - `SESSION_EXPIRED` → refaça o passo 6.
   - `HALTED_403`/`HALTED_429` → o Garmin recusou o IP da Vercel; mantenha a rotina pelo Mac.

## Período de comparação (5–7 dias)

Deixe a rotina atual do Mac rodando junto. As duas só fazem leitura, e o total fica em ~70 chamadas por dia. Compare o portal da Vercel com o painel do Claude. Se baterem, a rotina do Claude passa a ler `/api/report` pelo conector da Vercel e o Mac deixa de ser necessário.

## Manutenção

- **Tokens:** renovam a cada execução diária; o refresh token vale ~30 dias. Se o cron ficar mais de ~30 dias sem rodar, ou o Garmin revogar a sessão, refaça o passo 6 e apague a chave `garmin:tokens` no Upstash (Storage → Redis → Data Browser).
- **Não use os mesmos tokens em outro lugar.** Quem renovar primeiro invalida a cópia do outro.
- **Reprocessar um dia:** chame `/api/cron?to=AAAA-MM-DD` com o header do `CRON_SECRET`, ou espere: a janela de 3 dias recupera sozinha.
- **Voltar o histórico um dia:** a chave `lote_completo:prev` guarda a versão anterior.
- **Metas/provas:** ficam em `races_and_goals` dentro do `lote_completo`. Mudanças passam pelo Claude ou por um script de upsert.
- **Novo teste cardiopulmonar:** atualize `CPET`/`Z_FLOORS` em `lib/pipeline/engine.py` e `FLOORS` em `lib/collector.py`.

## Riscos conhecidos

- A API do Garmin Connect usada aqui não é pública. É o mesmo caminho do app de celular, via a biblioteca `garminconnect`. Em março de 2026 o Garmin mudou o login e quebrou a biblioteca `garth`, e pode mudar de novo. Quando isso acontecer, o log mostra `SESSION_EXPIRED` ou erros 4xx.
- Os tokens dão acesso à sua conta Garmin. Ficam no Upstash e na env da Vercel. Mantenha 2FA na Vercel, no GitHub e no Upstash.
