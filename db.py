#!/usr/bin/env python3
"""Kanban de e-mails — camada de dados (SQLite).

Duas tabelas e uma de metadados:

  colunas  — o quadro é dado, não código: nasce com o padrão de mercado,
             mas o dono renomeia, reordena, apaga e cria quantas quiser.
  tarefas  — uma linha por e-mail. `mensagem_id` tem UNIQUE, e é isso que
             impede o mesmo e-mail de virar card duas vezes quando a
             sincronização roda a cada 10 minutos.
  radar    — contador por dia dos alertas automáticos de portal de vaga.
             Sem ela, cada aviso do portal viraria card e o quadro morreria
             de 900 linhas.
  meta     — o marcador de "já li até o UID X" da última sincronização IMAP.

SQLite em WAL: a página Flask lê enquanto o timer de sincronização escreve.
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

HOME = Path.home()
BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("KANBAN_DB", HOME / ".local/state/kanban-emails/kanban.db"))

# Padrão de mercado para quadro pessoal: entrada não triada, fila, execução,
# bloqueio por terceiros, concluído e o descarte. "Concluídas" marca quais
# colunas alimentam o lembrete das 10/14/17.
COLUNAS_PADRAO = [
    ("Entrada",    "#64748b", None,     0),
    ("A fazer",    "#3b82f6", 8,        0),
    ("Fazendo",    "#f59e0b", 3,        0),
    ("Aguardando", "#a855f7", None,     0),
    ("Concluído",  "#22c55e", None,     1),
    ("Arquivado",  "#6b7280", None,     1),
    ("Radar",      "#0ea5e9", None,     1),
]


def agora():
    return datetime.now().isoformat(timespec="seconds")


def conecta():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA foreign_keys=ON;")
    return c


def cria_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS colunas (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        nome        TEXT    NOT NULL,
        ordem       INTEGER NOT NULL,
        cor         TEXT    NOT NULL DEFAULT '#6b7280',
        limite_wip  INTEGER,
        concluida   INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tarefas (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        mensagem_id  TEXT UNIQUE,
        titulo       TEXT    NOT NULL,
        remetente    TEXT,
        link         TEXT,
        trecho       TEXT,
        coluna_id    INTEGER NOT NULL REFERENCES colunas(id),
        prioridade   INTEGER NOT NULL DEFAULT 0,
        nota         TEXT    NOT NULL DEFAULT '',
        criado_em    TEXT    NOT NULL,
        movido_em    TEXT,
        concluido_em TEXT
    );
    CREATE TABLE IF NOT EXISTS radar (
        dia    TEXT PRIMARY KEY,
        qtd    INTEGER NOT NULL DEFAULT 0,
        fontes TEXT    NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS meta (
        chave TEXT PRIMARY KEY,
        valor TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tarefas_coluna ON tarefas(coluna_id);
    """)
    if not c.execute("SELECT 1 FROM colunas LIMIT 1").fetchone():
        for i, (nome, cor, wip, concl) in enumerate(COLUNAS_PADRAO):
            c.execute(
                "INSERT INTO colunas (nome, ordem, cor, limite_wip, concluida)"
                " VALUES (?,?,?,?,?)", (nome, i, cor, wip, concl))
    else:
        _migra_radar(c)
    c.commit()


def _migra_radar(c):
    """2026-09-29: o radar saiu da `Entrada` para uma coluna só dele.

    Fica com `concluida=1` de propósito: o radar é material de consulta, não
    trabalho em aberto. Marcado assim ele **não** entra no lembrete das 10/14/17
    — cobrar do dono os 881 alertas que já foram digeridos num card só seria
    cobrar o resumo do resumo.
    """
    col = c.execute("SELECT id FROM colunas WHERE nome='Radar'").fetchone()
    if not col:
        prox = c.execute("SELECT COALESCE(MAX(ordem),-1)+1 p FROM colunas").fetchone()["p"]
        col_id = c.execute(
            "INSERT INTO colunas (nome, ordem, cor, limite_wip, concluida)"
            " VALUES ('Radar',?,?,NULL,1)", (prox, "#0ea5e9")).lastrowid
    else:
        col_id = col["id"]
    c.execute("UPDATE tarefas SET coluna_id=?, movido_em=?"
              " WHERE mensagem_id LIKE 'radar-%' AND coluna_id<>?",
              (col_id, agora(), col_id))


def meta_get(c, chave, padrao=None):
    r = c.execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
    return r["valor"] if r else padrao


def meta_set(c, chave, valor):
    c.execute("INSERT INTO meta (chave,valor) VALUES (?,?)"
              " ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
              (chave, str(valor)))


def colunas(c):
    return c.execute("SELECT * FROM colunas ORDER BY ordem, id").fetchall()


def tarefas_por_coluna(c):
    """Devolve {coluna_id: [tarefa, ...]} com as pendentes primeiro."""
    todas = c.execute("SELECT * FROM tarefas ORDER BY criado_em DESC, id DESC").fetchall()
    agrupado = {col["id"]: [] for col in colunas(c)}
    for t in todas:
        agrupado.setdefault(t["coluna_id"], []).append(t)
    return agrupado


def pendentes(c):
    """Tarefas que o lembrete deve cobrar: está em coluna não-concluída."""
    return c.execute("""
        SELECT t.*, col.nome AS coluna
        FROM tarefas t JOIN colunas col ON col.id = t.coluna_id
        WHERE col.concluida = 0
        ORDER BY t.prioridade DESC, t.criado_em
    """).fetchall()


def move(c, tarefa_id, coluna_id, posicao=None):
    col = c.execute("SELECT * FROM colunas WHERE id=?", (coluna_id,)).fetchone()
    if not col:
        return False, "coluna não existe"
    t = c.execute("SELECT * FROM tarefas WHERE id=?", (tarefa_id,)).fetchone()
    if not t:
        return False, "tarefa não existe"
    # Sair de uma coluna concluída reabre a tarefa; entrar nela conclui.
    era_concluida = c.execute(
        "SELECT concluida FROM colunas WHERE id=?", (t["coluna_id"],)).fetchone()["concluida"]
    concluido_em = agora() if col["concluida"] and not era_concluida else (
        None if not col["concluida"] else t["concluido_em"])
    c.execute("UPDATE tarefas SET coluna_id=?, movido_em=?, concluido_em=? WHERE id=?",
              (coluna_id, agora(), concluido_em, tarefa_id))
    c.commit()
    return True, col["nome"]


def estourou_wip(c, coluna_id):
    col = c.execute("SELECT * FROM colunas WHERE id=?", (coluna_id,)).fetchone()
    if not col or not col["limite_wip"]:
        return None
    n = c.execute("SELECT COUNT(*) n FROM tarefas WHERE coluna_id=?", (coluna_id,)).fetchone()["n"]
    return n if n > col["limite_wip"] else None


def radar_registra(c, dia, fonte):
    """Soma um alerta do dia. Idempotente por natureza: quem orquestra é o
    marcador de UID, não esta função."""
    linha = c.execute("SELECT qtd, fontes FROM radar WHERE dia=?", (dia,)).fetchone()
    fontes = json.loads(linha["fontes"]) if linha else {}
    qtd = (linha["qtd"] if linha else 0) + 1
    fontes[fonte] = fontes.get(fonte, 0) + 1
    c.execute("INSERT INTO radar (dia,qtd,fontes) VALUES (?,?,?)"
              " ON CONFLICT(dia) DO UPDATE SET qtd=excluded.qtd,"
              " fontes=excluded.fontes", (dia, qtd, json.dumps(fontes)))
    return qtd, fontes


def radar_resumo(fontes, limite=6):
    """Lê o contador e vira a frase que aparece no card do dia."""
    if not fontes:
        return ""
    top = sorted(fontes.items(), key=lambda kv: (-kv[1], kv[0]))[:limite]
    texto = " · ".join(f"{d} ({n})" for d, n in top)
    extra = len(fontes) - len(top)
    if extra > 0:
        texto += f" · +{extra} portal(is)"
    return texto
