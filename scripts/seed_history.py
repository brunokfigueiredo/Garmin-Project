"""Rode UMA vez no Mac, na pasta do projeto, depois de `vercel env pull .env.local`.
Sobe o histórico atual (historico/lote_completo.json) e o data.json do painel para o Redis.
Uso: python3 scripts/seed_history.py ../dados_iniciais/lote_completo.json ../dados_iniciais/data.json
Requer: pip install upstash-redis==1.9.0"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
for line in open(".env.local"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); os.environ.setdefault(k, v.strip().strip('"'))
import store  # noqa: E402

lote, data = sys.argv[1], sys.argv[2]
store.put_bytes(store.K_LOTE, open(lote, "rb").read())
store.put_bytes(store.K_DATA, open(data, "rb").read())
print("OK: histórico e painel no Redis.", round(os.path.getsize(lote) / 1e6, 1), "MB de histórico.")
