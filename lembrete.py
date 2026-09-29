#!/usr/bin/env python3
"""Kanban de e-mails — lembrete das 10h, 14h e 17h.

Manda pelo Telegram o que continua aberto. A escolha de ir direto na Bot API,
sem passar pelo opencode: o resumo é uma consulta ao banco, não uma tarefa — e
o tempo de resposta do lembrete precisa ser de 1 segundo, não de 40.

Só entra na cobrança o que está em coluna **não** concluída. "Concluído" e
"Arquivado" estão marcados como tal no quadro, então cairam fora sozinhos.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import db
from importer import load_env

LOG = Path(os.environ.get("KANBAN_LOG",
                          Path.home() / ".local/state/kanban-emails/lembrete.log"))
FAIXA = {10: "manhã", 14: "tarde", 17: "fim de tarde"}


def manda(chat, texto):
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat:
        raise RuntimeError("sem TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")
    corpo = json.dumps({
        "chat_id": chat,
        "text": texto,
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=corpo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def resumo():
    c = db.conecta()
    db.cria_schema(c)
    linhas = db.pendentes(c)
    cols = db.colunas(c)
    c.close()
    por_coluna = {}
    for t in linhas:
        por_coluna.setdefault(t["coluna"], []).append(t)
    return cols, por_coluna, len(linhas)


def _linha(t):
    marcas = {0: "", 1: "🔺 ", 2: "🔴 "}
    titulo = t["titulo"]
    if len(titulo) > 74:
        titulo = titulo[:73] + "…"
    linha = f"  {marcas.get(t['prioridade'], '')}{titulo}"
    if t["link"]:
        linha += f"  [abrir]({t['link']})"
    return linha


def monta_texto(agora):
    """Cobra o que você se comprometeu a fazer; só conta o que ainda não triou.

    A primeira coluna é a caixa de entrada. Listar 118 itens não triados às 10h
    não é lembrete, é parede de texto — o valor está em ver o que está em
    "Fazendo" e "A fazer" e o tamanho do que ainda espera triagem.
    """
    cols, por_coluna, total = resumo()
    if not total:
        return (f"✅ Kanban {agora:%H:%M} — nada pendente. "
                "Quadro limpo, aproveite o dia.")
    entrada_id = cols[0]["id"] if cols else None
    partes = [f"📋 *Kanban {agora:%H:%M}* — {total} em aberto\n"]

    ativos = [c for c in cols
              if not c["concluida"] and c["id"] != entrada_id]
    mostrou = False
    for col in ativos:
        tarefas = por_coluna.get(col["nome"], [])
        if not tarefas:
            continue
        mostrou = True
        partes.append(f"*{col['nome']}* ({len(tarefas)})")
        for t in tarefas[:10]:
            partes.append(_linha(t))
        if len(tarefas) > 10:
            partes.append(f"  … e mais {len(tarefas) - 10}")
        partes.append("")

    esperando = por_coluna.get(cols[0]["nome"], []) if cols else []
    if esperando:
        if not mostrou:
            partes.append("_Nada em andamento ainda._\n")
        partes.append(f"🗂 *Entrada:* {len(esperando)} para triar "
                      "(abra o quadro quando puder)")
    return "\n".join(partes).strip()


def main():
    agora = datetime.now()
    env = load_env()
    chat = env.get("TELEGRAM_CHAT_ID", "")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        manda(chat, monta_texto(agora))
        LOG.write_text(
            f"{agora.isoformat()} enviado ({FAIXA.get(agora.hour, '?')})\n")
        return 0
    except Exception as e:
        LOG.write_text(f"{agora.isoformat()} ERRO {type(e).__name__}: {e}\n")
        print(f"ERRO: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
