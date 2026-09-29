#!/usr/bin/env python3
"""Kanban de e-mails — sincronização IMAP.

Lê a caixa de entrada e transforma **e-mail de trabalho** em card na coluna
"Entrada". Só isso: quem decide se a vaga é relevante é o filtro que já roda no
LeitorMail, e ele é reaproveitado em vez de reescrito.

Duas decisões que evitam o clássico problema de encher o quadro de lixo:

1. `Mensagem-ID` tem UNIQUE em `tarefas`. O timer roda a cada 10 min; sem a
   trava o mesmo digest viraria card novo toda vez.
2. O marcador de UID avança **junto** com a inserção dos cards, na mesma
   transação, e só para e-mail que foi lido de fato. Se a conexão cair no meio,
   o mesmo trecho é relido — e a trava do `Mensagem-ID` absorve. Marcar antes
   de processar perderia e-mail de vez.

O piso de data (`DATA_MIN`) vai na busca do IMAP, não no filtro local: assim o
servidor manda só o que interessa, em vez de baixar anos de caixa para
descartar aqui.
"""
import email
import imaplib
import os
import re
import sys
import urllib.parse
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path

import db
import classifica

HOME = Path.home()
BASE = Path(__file__).resolve().parent
ENV_PROPRIO = Path(os.environ.get("KANBAN_ENV_FILE", BASE / "credenciais.env"))
ENV_LEITOR = HOME / ".config/omarchy-voice/plugins/leitormail/credenciais.env"
ENV_TELEBOT = HOME / ".config/omarchy-voice/plugins/telebot/credenciais.env"
LOG = Path(os.environ.get("KANBAN_LOG",
                          HOME / ".local/state/kanban-emails/sync.log"))
JANELA = int(os.environ.get("KANBAN_JANELA", "100"))

# Onde buscar o filtro pronto. Se o LeitorMail estiver instalado ao lado, é
# ele que manda — manter a regra de relevância em dois lugares é a forma
# garantida de elas divergirem.
DIR_LEITOR = ENV_LEITOR.parent


def _carrega_filtro():
    """Importa a lógica do LeitorMail; cai no filtro embutido se não achar."""
    if str(DIR_LEITOR) not in sys.path:
        sys.path.insert(0, str(DIR_LEITOR))
    try:
        import leitormail as lm  # noqa: E402
        return {
            "origem": "LeitorMail",
            "e_data_ok": lm.e_data_ok,
            "e_relevante": lm.e_relevante,
            "dec_hdr": lm.dec_hdr,
            "body_text": lm.body_text,
            "data_min": lm.DATA_MIN,
        }
    except Exception:
        pass
    try:
        from filtro_local import (  # noqa: E402
            DATA_MIN, body_text, dec_hdr, e_data_ok, e_relevante)
        return {"origem": "embutido", "e_data_ok": e_data_ok,
                "e_relevante": e_relevante, "dec_hdr": dec_hdr,
                "body_text": body_text, "data_min": DATA_MIN}
    except Exception as e:
        raise RuntimeError(f"nenhum filtro disponível: {e}")


def load_env():
    """Credenciais do kanban primeiro; o resto vem do LeitorMail (IMAP) e do
    telebot (Telegram) para não duplicar segredo em três arquivos.
    O primeiro arquivo que define uma chave ganha."""
    data = {}
    for caminho in (ENV_PROPRIO, ENV_LEITOR, ENV_TELEBOT):
        if not caminho.exists():
            continue
        for line in caminho.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                data.setdefault(k.strip(), v.strip())
    for k in ("GMAIL_ADDR", "GMAIL_PASS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if not data.get(k):
            data[k] = os.environ.get(k, "")
    return data


def link_gmail(msg_id, addr):
    """Gmail aceita `rfc822msgid:` na busca — abre o e-mail exato, sem
    precisar do thread id que o IMAP não devolve."""
    q = urllib.parse.quote(f"rfc822msgid:{msg_id}")
    return f"https://mail.google.com/mail/u/0/#search/{q}"


def dia_do_email(msg):
    """Agrupa o radar pelo dia do e-mail, não pelo dia da sincronização —
    senão o card de um alerta de ontem nasce carimbado com a data de hoje."""
    from email.utils import parsedate_to_datetime
    from zoneinfo import ZoneInfo

    try:
        d = parsedate_to_datetime(msg.get("Date"))
        if d is not None:
            if d.tzinfo is not None:
                d = d.astimezone(ZoneInfo("America/Sao_Paulo"))
            return d.strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now().strftime("%Y-%m-%d")


def sincroniza():
    env = load_env()
    addr, pwd = env.get("GMAIL_ADDR", ""), env.get("GMAIL_PASS", "")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not addr or not pwd:
        LOG.write_text(f"{datetime.now().isoformat()} ERRO sem GMAIL_ADDR/GMAIL_PASS\n")
        return {"erro": "sem credencial IMAP"}

    f = _carrega_filtro()
    c = db.conecta()
    db.cria_schema(c)
    entrada = c.execute("SELECT id FROM colunas ORDER BY ordem, id LIMIT 1").fetchone()
    if not entrada:
        c.close()
        return {"erro": "sem colunas"}
    col_id = entrada["id"]
    # O radar tem coluna própria desde 29/09. Se um quadro antigo não tem,
    # o card volta para a primeira coluna em vez de sumir.
    r = c.execute("SELECT id FROM colunas WHERE nome='Radar'").fetchone()
    radar_col_id = r["id"] if r else col_id

    ultimo = int(db.meta_get(c, "imap_uid", "0") or 0)
    criados, vistos, fora_data, ignorados, lixo = 0, 0, 0, 0, 0
    radar_dias = set()
    alta = ultimo
    mail = None
    # O piso de data vai para o servidor: sem isso o IMAP devolveria a caixa
    # inteira e só aqui a gente jogaria fora — pagando o download de anos de
    # e-mail para descartar depois.
    desde = f["data_min"].strftime("%d-%b-%Y")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        mail.login(addr, pwd)
        mail.select("INBOX")
        # `uid` e não `search`: número de sequência muda quando um e-mail é
        # apagado, e o marcador atravessa reinícios — UID não muda.
        typ, data_ = mail.uid("SEARCH", None, f'(SINCE "{desde}")')
        if typ != "OK":
            return {"erro": "SEARCH falhou"}
        uids = [int(x) for x in data_[0].split()]
        if not uids:
            return {"novos": 0, "msg": "nada na janela desde DATA_MIN"}
        # Em lotes: no primeiro uso o marcador está em 0 e a fila pode ser
        # grande; depois sobra só o que chegou desde a última rodada.
        fila = [u for u in uids if u > ultimo]
        for ini in range(0, len(fila), JANELA):
            lote = fila[ini:ini + JANELA]
            res, dados = mail.uid("FETCH", f"{lote[0]}:{lote[-1]}", "(RFC822)")
            if res != "OK":
                # Não avança o marcador: a próxima rodada tenta de novo.
                # Reprocessar é seguro — a trava de Mensagem-ID deduplica.
                break
            for bloco in dados:
                if not isinstance(bloco, tuple) or not bloco[1]:
                    continue
                # A cabeça vem como b'<seq> (UID <uid> RFC822 {<bytes>}'. O
                # primeiro número é o de SEQUÊNCIA, que muda com expurgo; o
                # UID está depois da palavra UID e é o único estável.
                m = re.search(rb"UID (\d+)", bloco[0] or b"")
                if not m:
                    continue
                raw = bloco[1]
                # Só aqui o e-mail foi lido de fato; a partir daqui avançar
                # o marcador é seguro, inclusive para os que serão ignorados.
                alta = max(alta, int(m.group(1)))
                vistos += 1
                msg = email.message_from_bytes(raw)
                if not f["e_data_ok"](msg):
                    fora_data += 1
                    continue
                subj = f["dec_hdr"](msg.get("Subject")) or "(sem assunto)"
                rem_bruto = f["dec_hdr"](msg.get("From")) or "desconhecido"
                _, rem = parseaddr(rem_bruto)
                rem = rem or rem_bruto
                corpo = re.sub(r"\s+", " ", f["body_text"](msg)).strip()

                # Quem manda decide o destino, antes da relevância: um portal
                # mandando 10 vagas por e-mail é ruído mesmo falando de emprego.
                destino = classifica.classifica(rem, subj)
                if destino == "lixo":
                    lixo += 1
                    continue
                if destino == "radar":
                    dia = dia_do_email(msg)
                    qtd, fontes = db.radar_registra(c, dia, classifica.fonte(rem))
                    radar_dias.add(dia)
                    radar_mid = f"radar-{dia}"
                    ja_existe = c.execute(
                        "SELECT 1 FROM tarefas WHERE mensagem_id=?",
                        (radar_mid,)).fetchone()
                    c.execute(
                        "INSERT INTO tarefas"
                        " (mensagem_id,titulo,remetente,link,trecho,coluna_id,"
                        "  criado_em,movido_em)"
                        " VALUES (?,?,?,?,?,?,?,?)"
                        " ON CONFLICT(mensagem_id) DO UPDATE SET"
                        "  titulo=excluded.titulo, remetente=excluded.remetente,"
                        "  trecho=excluded.trecho, movido_em=excluded.movido_em",
                        (radar_mid, f"📡 Radar de vagas — {dia[8:10]}/{dia[5:7]}",
                         f"{qtd} alerta(s) automático(s)", None,
                         db.radar_resumo(fontes), radar_col_id,
                         db.agora(), db.agora()))
                    if not ja_existe:
                        criados += 1
                    continue
                if not f["e_relevante"](subj, rem, corpo, addr):
                    ignorados += 1
                    continue
                msg_id = (msg.get("Message-ID") or f"uid-{alta}").strip()
                cur = c.execute(
                    "INSERT OR IGNORE INTO tarefas"
                    " (mensagem_id,titulo,remetente,link,trecho,coluna_id,"
                    "  criado_em,movido_em)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (msg_id, subj.strip()[:300], rem.strip()[:120],
                     link_gmail(msg_id, addr), corpo[:280], col_id,
                     db.agora(), db.agora()))
                criados += cur.rowcount
        # Marcador e cards na MESMA transação: gravar o avanço depois do
        # commit deixaria o marcador para trás se o processo morresse no meio.
        db.meta_set(c, "imap_uid", alta)
        c.commit()
    except Exception as e:
        LOG.write_text(f"{datetime.now().isoformat()} ERRO {type(e).__name__}: {e}\n")
        return {"erro": f"{type(e).__name__}: {e}"}
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass
        c.close()

    msg = (f"OK {criados} cards novos de {vistos} e-mails "
           f"(filtro: {f['origem']}"
           + (f", {len(radar_dias)} dia(s) de radar" if radar_dias else "")
           + (f", {ignorados} irrelevantes" if ignorados else "")
           + (f", {lixo} descartados" if lixo else "")
           + (f", {fora_data} fora de data" if fora_data else "") + ")")
    LOG.write_text(f"{datetime.now().isoformat()} {msg}\n")
    return {"novos": criados, "conferidos": vistos, "msg": msg}


if __name__ == "__main__":
    print(sincroniza())
