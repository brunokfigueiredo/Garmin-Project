"""Armazenamento no Upstash Redis (Vercel Marketplace). Valores grandes vão em gzip + base64."""
import base64, gzip, json, os

from upstash_redis import Redis


def _redis():
    url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
    tok = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if not url or not tok: raise RuntimeError("Upstash Redis não configurado (UPSTASH_REDIS_REST_URL/TOKEN ou KV_REST_API_URL/TOKEN)")
    return Redis(url=url, token=tok)


R = None


def r():
    global R
    if R is None: R = _redis()
    return R


def put_bytes(key, raw: bytes, ex=None):
    r().set(key, base64.b64encode(gzip.compress(raw)).decode(), ex=ex)


def get_gz(key):
    """Bytes gzip (para servir com Content-Encoding: gzip) ou None."""
    v = r().get(key)
    return base64.b64decode(v) if v else None


def get_bytes(key):
    g = get_gz(key)
    return gzip.decompress(g) if g else None


def put_json(key, obj, ex=None):
    put_bytes(key, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), ex=ex)


def get_json(key):
    b = get_bytes(key)
    return json.loads(b) if b else None


# Chaves
K_LOTE = "lote_completo"          # histórico completo (esquema da Fase 1)
K_LOTE_PREV = "lote_completo:prev" # cópia do dia anterior, para voltar atrás
K_DATA = "painel:data"             # data.json do painel
K_REPORT = "painel:report"         # daily_report + log do último dia
K_TOKENS = "garmin:tokens"         # tokens DI (texto puro; rotacionam a cada execução)
K_LOCK = "pipeline:lock"
K_PLAN = "painel:plan"               # aba Plano (assessoria × realizado + projeção)
K_PLANNED = "plano:assessoria"        # treinos da assessoria já vistos (data → treino)
raw_key = lambda d: f"raw:{d}"     # bruto do dia (expira em 30 dias)
log_key = lambda d: f"log:{d}"     # log do dia (expira em 400 dias)
