#!/usr/bin/env python3
"""Kanban de e-mails — aplicação Flask.

Roda só em 127.0.0.1. Ainda assim, toda rota de escrita exige um token que o
próprio processo gera no boot e injeta na página: sem ele, qualquer site que
você abrisse no navegador poderia mandar POST para o seu localhost e mover
tarefas. É o mesmo motivo de `SameSite=Lax` existir num cookie — a defesa é
barata e não custa nada.
"""
import os
import secrets
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, abort

import db
import importer

BASE = Path(__file__).resolve().parent
HOST = os.environ.get("KANBAN_HOST", "127.0.0.1")
PORTA = int(os.environ.get("KANBAN_PORT", "5000"))
TOKEN = os.environ.get("KANBAN_TOKEN") or secrets.token_urlsafe(24)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

app.jinja_env.globals["TOKEN"] = TOKEN
app.jinja_env.globals["agora"] = datetime.now


def db_rota(fn):
    """Abre a conexão, garante o schema e fecha — SQLite não gosta de
    conexão compartilhada entre threads do Flask."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        c = db.conecta()
        db.cria_schema(c)
        try:
            return fn(c, *a, **kw)
        finally:
            c.close()
    return wrapper


@app.before_request
def exige_token():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if not secrets.compare_digest(request.headers.get("X-Kanban-Token", ""), TOKEN):
        abort(403, "token inválido")
    return None


def snapshot(c):
    cols = db.colunas(c)
    agrupado = db.tarefas_por_coluna(c)
    return {
        "colunas": [{
            "id": x["id"], "nome": x["nome"], "cor": x["cor"],
            "limite_wip": x["limite_wip"], "concluida": x["concluida"],
            "ordem": x["ordem"],
            "total": len(agrupado.get(x["id"], [])),
            "estourou": db.estourou_wip(c, x["id"]),
        } for x in cols],
        "tarefas": {str(k): [dict(t) for t in v] for k, v in agrupado.items()},
        "pendentes": len(db.pendentes(c)),
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/estado")
@db_rota
def estado(c):
    return jsonify(snapshot(c))


@app.post("/api/tarefa")
@db_rota
def nova_tarefa(c):
    d = request.get_json(force=True, silent=True) or {}
    titulo = (d.get("titulo") or "").strip()
    if not titulo:
        return jsonify({"erro": "título vazio"}), 400
    col_id = d.get("coluna_id") or c.execute(
        "SELECT id FROM colunas ORDER BY ordem, id LIMIT 1").fetchone()["id"]
    if not c.execute("SELECT 1 FROM colunas WHERE id=?", (col_id,)).fetchone():
        return jsonify({"erro": "coluna não existe"}), 400
    cur = c.execute(
        "INSERT INTO tarefas (mensagem_id,titulo,remetente,link,trecho,coluna_id,"
        "criado_em,movido_em) VALUES (NULL,?,?,?,?,?,?,?)",
        (titulo[:300], (d.get("remetente") or "").strip()[:120],
         (d.get("link") or "").strip()[:500], (d.get("trecho") or "").strip()[:280],
         col_id, db.agora(), db.agora()))
    c.commit()
    return jsonify({"id": cur.lastrowid, **snapshot(c)})


@app.patch("/api/tarefa/<int:tid>")
@db_rota
def edita_tarefa(c, tid):
    d = request.get_json(force=True, silent=True) or {}
    campos, vals = [], []
    if "titulo" in d:
        t = (d["titulo"] or "").strip()
        if not t:
            return jsonify({"erro": "título vazio"}), 400
        campos.append("titulo=?"); vals.append(t[:300])
    if "nota" in d:
        campos.append("nota=?"); vals.append((d["nota"] or "")[:4000])
    if "prioridade" in d:
        campos.append("prioridade=?"); vals.append(int(d["prioridade"] or 0))
    if not campos:
        return jsonify({"erro": "nada para alterar"}), 400
    vals.append(tid)
    c.execute(f"UPDATE tarefas SET {','.join(campos)} WHERE id=?", vals)
    c.commit()
    return jsonify(snapshot(c))


@app.post("/api/tarefa/<int:tid>/mover")
@db_rota
def move_tarefa(c, tid):
    d = request.get_json(force=True, silent=True) or {}
    ok, msg = db.move(c, tid, d.get("coluna_id"))
    if not ok:
        return jsonify({"erro": msg}), 400
    return jsonify(snapshot(c))


@app.delete("/api/tarefa/<int:tid>")
@db_rota
def apaga_tarefa(c, tid):
    c.execute("DELETE FROM tarefas WHERE id=?", (tid,))
    c.commit()
    return jsonify(snapshot(c))


@app.post("/api/coluna")
@db_rota
def nova_coluna(c):
    d = request.get_json(force=True, silent=True) or {}
    nome = (d.get("nome") or "").strip()
    if not nome:
        return jsonify({"erro": "nome vazio"}), 400
    prox = c.execute("SELECT COALESCE(MAX(ordem),-1)+1 p FROM colunas").fetchone()["p"]
    wip = d.get("limite_wip")
    cur = c.execute(
        "INSERT INTO colunas (nome,ordem,cor,limite_wip,concluida) VALUES (?,?,?,?,?)",
        (nome[:60], prox, (d.get("cor") or "#3b82f6")[:9],
         int(wip) if wip else None, 1 if d.get("concluida") else 0))
    c.commit()
    return jsonify({"id": cur.lastrowid, **snapshot(c)})


@app.patch("/api/coluna/<int:cid>")
@db_rota
def edita_coluna(c, cid):
    if not c.execute("SELECT 1 FROM colunas WHERE id=?", (cid,)).fetchone():
        return jsonify({"erro": "coluna não existe"}), 400
    d = request.get_json(force=True, silent=True) or {}
    mapa = {"nome": ("nome", lambda v: str(v).strip()[:60]),
            "cor": ("cor", lambda v: str(v).strip()[:9]),
            "ordem": ("ordem", int),
            "concluida": ("concluida", lambda v: 1 if v else 0)}
    campos, vals = [], []
    for k, (col, conv) in mapa.items():
        if k in d:
            v = conv(d[k])
            if col == "nome" and not v:
                return jsonify({"erro": "nome vazio"}), 400
            campos.append(f"{col}=?"); vals.append(v)
    if "limite_wip" in d:
        campos.append("limite_wip=?")
        vals.append(int(d["limite_wip"]) if d["limite_wip"] else None)
    if not campos:
        return jsonify({"erro": "nada para alterar"}), 400
    vals.append(cid)
    c.execute(f"UPDATE colunas SET {','.join(campos)} WHERE id=?", vals)
    c.commit()
    return jsonify(snapshot(c))


@app.delete("/api/coluna/<int:cid>")
@db_rota
def apaga_coluna(c, cid):
    restantes = c.execute("SELECT COUNT(*) n FROM colunas").fetchone()["n"]
    if restantes <= 1:
        return jsonify({"erro": "o quadro precisa de ao menos uma coluna"}), 400
    n = c.execute("SELECT COUNT(*) n FROM tarefas WHERE coluna_id=?", (cid,)).fetchone()["n"]
    if n:
        return jsonify({"erro": f"{n} tarefa(s) nesta coluna — mova ou apague antes"}), 400
    c.execute("DELETE FROM colunas WHERE id=?", (cid,))
    c.commit()
    return jsonify(snapshot(c))


@app.post("/api/sincronizar")
def sincroniza():
    """Dispara a leitura do IMAP na hora, fora do relógio do timer."""
    return jsonify(importer.sincroniza())


if __name__ == "__main__":
    with app.app_context():
        c = db.conecta()
        db.cria_schema(c)
        c.close()
    print(f"Kanban de e-mails em http://{HOST}:{PORTA}")
    app.run(host=HOST, port=PORTA, debug=False, threaded=True)
