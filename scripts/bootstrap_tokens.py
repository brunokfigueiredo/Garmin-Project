"""Rode no seu Mac (não na Vercel). Grava os tokens DI do Garmin direto no Redis do projeto (chave garmin:tokens).

1) Pede a URL e o token REST do Redis. Copie cada um da Vercel (Storage → garmin-redis): KV_REST_API_URL e
   KV_REST_API_TOKEN. Antes do login, ele testa a conexão.
2) Faz o login no Garmin com e-mail, senha e código MFA digitados por você. Nada disso é salvo.
3) Grava os tokens no Redis e confere a gravação.

A partir daí a Vercel renova os tokens sozinha. Não use os mesmos tokens em outro lugar.
Requer: python3 >= 3.12 e  pip install garminconnect==0.3.20 upstash-redis==1.9.0"""
import getpass, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

if os.path.exists(".env.local"):
    for line in open(".env.local"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))
if not (os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")):
    print("Na Vercel: Storage → garmin-redis → copie o valor de KV_REST_API_URL (começa com https://).")
    os.environ["KV_REST_API_URL"] = input("Cole aqui a KV_REST_API_URL: ").strip().strip('"')
    print("Agora copie o valor de KV_REST_API_TOKEN (não o READ_ONLY).")
    os.environ["KV_REST_API_TOKEN"] = getpass.getpass("Cole aqui o KV_REST_API_TOKEN (não aparece na tela): ").strip().strip('"')

import store
try:
    store.r().set("conexao:teste", "ok", ex=60)
    assert store.r().get("conexao:teste") == "ok"
    print("Redis: conexão OK.")
except Exception as e:
    sys.exit(f"Redis: não consegui conectar ({type(e).__name__}). Confira a URL e o token e rode de novo.")

from garminconnect import Garmin
email = input("E-mail do Garmin: ").strip()
pwd = getpass.getpass("Senha do Garmin (não aparece na tela): ")
g = Garmin(email, pwd, prompt_mfa=lambda: input("Código MFA: ").strip())
g.login()
tokens = g.client.dumps()
print("Login OK:", g.get_full_name())
store.r().set(store.K_TOKENS, tokens)
print("OK: tokens gravados no Redis do projeto." if store.r().get(store.K_TOKENS) == tokens else "ERRO: não consegui confirmar a gravação no Redis.")
