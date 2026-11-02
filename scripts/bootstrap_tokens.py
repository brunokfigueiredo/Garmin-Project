"""Rode UMA vez no seu Mac (não na Vercel). Faz o login no Garmin com e-mail, senha e código MFA
digitados por você no terminal e grava os tokens DI em ./garmin_tokens.json (permissão 600).
Depois:  vercel env add GARMIN_TOKENS_SEED production < garmin_tokens.json  &&  rm garmin_tokens.json
A partir daí a Vercel renova e guarda os tokens sozinha (no Redis). Não use esses tokens em outro lugar:
o refresh token rotaciona e quem renovar primeiro invalida a cópia do outro.
Requer: python3 >= 3.12 e  pip install garminconnect==0.3.20"""
import getpass, os
from garminconnect import Garmin

email = input("E-mail do Garmin: ").strip()
pwd = getpass.getpass("Senha do Garmin (não aparece na tela): ")
g = Garmin(email, pwd, prompt_mfa=lambda: input("Código MFA: ").strip())
g.login()
fd = os.open("garmin_tokens.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f: f.write(g.client.dumps())
print("OK: tokens em ./garmin_tokens.json. Teste:", g.get_full_name())
