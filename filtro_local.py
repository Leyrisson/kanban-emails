#!/usr/bin/env python3
"""Kanban de e-mails — filtro de relevância (cópia autonomous).

Este arquivo só é usado quando o plugin do LeitorMail não está instalado ao
lado. Mesmoatchet: o filtro é o mesmo, com a mesma política de data, para o
quadro não mudar de comportamento conforme o que estiver na máquina.

Critério: e-mail que fala de **vaga, proposta ou entrevista de emprego**.
Some com `self`, promoções e newsletter genérica.
"""
import re
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

DATA_MIN = datetime(2026, 9, 1)
TZ = ZoneInfo("America/Sao_Paulo")

RELEVANTES = re.compile(
    r"(vaga|vagas|oportunidade|oportunidades|candidatura|candidat[ae]|"
    r"entrevista|processo seletivo|processo seletivo|selec[aã]cao|"
    r"curriculo|curr[ií]culo|preenchimento de cadastro|"
    r"contrata[çc][ãa]o|est[aá]gio|aprendiz|trainee|"
    r"freelancer|freela|pessoa jur[ií]dica|home office|remoto|"
    r"analista de|s Desenvolvedor|engenheiro de|suporte t[eé]cnico|"
    r"gerente de ti|tecnologia da informa)", re.I)

IGNORADOS = re.compile(
    r"(^self$|noreply|no-reply|do-not-reply|"
    r"promo[çc][ãa]o|desconto|cupom|black friday|"
    r"newsletter|broadcast|informe|boletim)", re.I)


def dec_hdr(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def e_data_ok(msg, minimo=DATA_MIN):
    """E-mail sem `Date` legível passa; com data antiga, não."""
    raw = msg.get("Date")
    if not raw:
        return True
    try:
        d = parsedate_to_datetime(raw)
    except Exception:
        return True
    if d is None:
        return True
    # Mesmo contrato do LeitorMail: traz para o fuso local e compara ingênuo,
    # para não misturar datetime aware com DATA_MIN ingênuo.
    if d.tzinfo is not None:
        d = d.astimezone(TZ).replace(tzinfo=None)
    return d >= minimo


def _looks_b64(s):
    return (len(s) > 40 and s.isalnum() and re.fullmatch(r"[A-Za-z0-9+/=]+", s)
            and len(s) % 4 == 0)


def _mime_text(blob, depth=0):
    if depth > 6 or not isinstance(blob, str):
        return ""
    if _looks_b64(blob):
        import base64
        import quopri
        for dec in (lambda x: base64.b64decode(x, validate=False),
                    lambda x: quopri.decodestring(x.encode()).decode(errors="replace")):
            try:
                t = _mime_text(dec(blob).decode(errors="replace"), depth + 1)
                if t.strip():
                    return t
            except Exception:
                continue
        return ""
    if "=?" in blob and "charset=" in blob:
        return _mime_text(str(make_header(decode_header(blob))), depth + 1)
    return blob


def body_text(msg):
    partes = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=False)
        partes.append(_mime_text(payload))
    return "\n".join(p for p in partes if p)


def e_relevante(subj, rem, corpo, addr=""):
    texto = f"{subj}\n{rem}\n{corpo}"
    if IGNORADOS.search(rem or ""):
        return False
    # Não cria card para o que você mesmo enviou.
    if addr and addr.lower() in (rem or "").lower():
        return False
    return bool(RELEVANTES.search(texto))
