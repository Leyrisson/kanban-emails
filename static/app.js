/* Kanban de e-mails — front em JS puro, sem build e sem dependência.
   O quadro inteiro vem de /api/estado, e cada escrita devolve o mesmo
   snapshot: nada de estado duplicado no cliente. */
"use strict";

const $ = (s) => document.querySelector(s);
const quadro = $("#quadro");
let estado = { colunas: [], tarefas: {}, pendentes: 0 };
let editando = null;      // id do card aberto no diálogo
let colunaEditando = null;
let arrastando = null;

const PRIO = { 0: "⚪", 1: "🔺", 2: "🔴" };

async function api(rota, opcoes = {}) {
  const r = await fetch(rota, {
    ...opcoes,
    headers: { "Content-Type": "application/json", "X-Kanban-Token": window.TOKEN },
  });
  const dados = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(dados.erro || `HTTP ${r.status}`);
  return dados;
}

function aplica(snap) {
  estado = { colunas: snap.colunas || [], tarefas: snap.tarefas || {}, pendentes: snap.pendentes || 0 };
  desenha();
}

function el(tag, props = {}, filhos = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const f of [].concat(filhos)) if (f) n.append(f);
  return n;
}

function desenha() {
  quadro.textContent = "";
  for (const col of estado.colunas) quadro.append(desenhaColuna(col));
  const total = estado.pendentes;
  $("#resumo").textContent = total
    ? `${total} tarefa(s) em aberto`
    : "nada pendente";
}

function desenhaColuna(col) {
  const pilulas = el("div", { class: "pilulas" });
  const tarefas = estado.tarefas[String(col.id)] || [];

  if (!tarefas.length) pilulas.append(el("div", { class: "vazio" }, "vazio"));

  for (const t of tarefas) pilulas.append(desenhaCard(t));

  const cab = el("header", { onclick: () => abreColuna(col) }, [
    el("span", { class: "ponto", style: `background:${col.cor}` }),
    el("h2", {}, col.nome),
    el("span", { class: "contagem" }, String(tarefas.length)),
  ]);
  if (col.estourou !== null && col.estourou !== undefined) {
    cab.append(el("span", { class: "wip", title: "acima do limite WIP" },
      `⚠ ${col.estourou}/${col.limite_wip}`));
  }

  const painel = el("section", { class: "coluna", "data-col": col.id }, [cab, pilulas]);

  painel.addEventListener("dragover", (e) => {
    e.preventDefault();
    painel.classList.add("alvo");
  });
  painel.addEventListener("dragleave", () => painel.classList.remove("alvo"));
  painel.addEventListener("drop", async (e) => {
    e.preventDefault();
    painel.classList.remove("alvo");
    if (!arrastando) return;
    const tid = Number(arrastando.dataset.id);
    arrastando = null;
    try {
      aplica(await api(`/api/tarefa/${tid}/mover`, {
        method: "POST",
        body: JSON.stringify({ coluna_id: col.id }),
      }));
    } catch (err) { alert(`Não deu para mover: ${err.message}`); }
  });
  return painel;
}

function desenhaCard(t) {
  const titulo = el("div", { class: "titulo" }, t.titulo);
  const meta = el("div", { class: "meta" });
  if (t.remetente) meta.append(el("span", {}, t.remetente));
  if (t.link) meta.append(el("a", {
    href: t.link, target: "_blank", rel: "noopener",
    onclick: (e) => e.stopPropagation(),
  }, "abrir e-mail"));

  const card = el("article", {
    class: `card p${t.prioridade}` + (t.nota ? " nota-pendente" : ""),
    draggable: "true",
    "data-id": t.id,
    title: t.trecho || "",
    onclick: () => abreCard(t),
  }, [titulo, meta]);

  card.addEventListener("dragstart", () => {
    arrastando = card;
    card.classList.add("arrastando");
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("arrastando");
    arrastando = null;
  });
  return card;
}

/* ---- diálogo do card ---- */
function abreCard(t) {
  editando = t;
  $("#c-titulo").value = t.titulo || "";
  $("#c-prio").value = String(t.prioridade || 0);
  $("#c-rem").value = t.remetente || "";
  $("#c-nota").value = t.nota || "";
  $("#c-trecho").textContent = t.trecho || "";
  $("#dlg-card").showModal();
}

$("#c-salvar").addEventListener("click", async (e) => {
  e.preventDefault();
  try {
    aplica(await api(`/api/tarefa/${editando.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        titulo: $("#c-titulo").value,
        prioridade: $("#c-prio").value,
        nota: $("#c-nota").value,
      }),
    }));
    $("#dlg-card").close();
  } catch (err) { alert(err.message); }
});

$("#c-apagar").addEventListener("click", async (e) => {
  e.preventDefault();
  if (!confirm("Apagar este card?")) return;
  try {
    aplica(await api(`/api/tarefa/${editando.id}`, { method: "DELETE" }));
    $("#dlg-card").close();
  } catch (err) { alert(err.message); }
});

/* ---- diálogo da coluna ---- */
function abreColuna(col) {
  colunaEditando = col;
  $("#dlg-coluna-titulo").textContent = "Editar coluna";
  $("#k-nome").value = col.nome;
  $("#k-cor").value = col.cor;
  $("#k-wip").value = col.limite_wip || "";
  $("#k-concluida").checked = !!col.concluida;
  $("#k-apagar").style.display = "";
  $("#dlg-coluna").showModal();
}

$("#btn-coluna").addEventListener("click", () => {
  colunaEditando = null;
  $("#dlg-coluna-titulo").textContent = "Nova coluna";
  $("#k-nome").value = "";
  $("#k-cor").value = "#3b82f6";
  $("#k-wip").value = "";
  $("#k-concluida").checked = false;
  $("#k-apagar").style.display = "none";
  $("#dlg-coluna").showModal();
});

$("#k-salvar").addEventListener("click", async (e) => {
  e.preventDefault();
  const corpo = {
    nome: $("#k-nome").value,
    cor: $("#k-cor").value,
    limite_wip: $("#k-wip").value,
    concluida: $("#k-concluida").checked,
  };
  try {
    if (colunaEditando) {
      aplica(await api(`/api/coluna/${colunaEditando.id}`, {
        method: "PATCH", body: JSON.stringify(corpo),
      }));
    } else {
      aplica(await api("/api/coluna", { method: "POST", body: JSON.stringify(corpo) }));
    }
    $("#dlg-coluna").close();
  } catch (err) { alert(err.message); }
});

$("#k-apagar").addEventListener("click", async (e) => {
  e.preventDefault();
  if (!confirm(`Excluir a coluna "${colunaEditando.nome}"?`)) return;
  try {
    aplica(await api(`/api/coluna/${colunaEditando.id}`, { method: "DELETE" }));
    $("#dlg-coluna").close();
  } catch (err) { alert(err.message); }
});

/* ---- card manual ---- */
async function criaDoCampo() {
  const campo = $("#novo-titulo");
  const titulo = campo.value.trim();
  if (!titulo) return;
  try {
    aplica(await api("/api/tarefa", {
      method: "POST", body: JSON.stringify({ titulo }),
    }));
    campo.value = "";
  } catch (err) { alert(err.message); }
}

$("#btn-nova").addEventListener("click", criaDoCampo);
$("#novo-titulo").addEventListener("keydown", (e) => {
  if (e.key === "Enter") criaDoCampo();
});

/* ---- sincronização ---- */
$("#btn-sync").addEventListener("click", async () => {
  const b = $("#btn-sync");
  b.disabled = true;
  b.textContent = "lendo…";
  try {
    const r = await api("/api/sincronizar", { method: "POST" });
    aplica(await api("/api/estado"));
    b.textContent = r.erro ? "erro" : `+${r.novos || 0}`;
  } catch (err) {
    b.textContent = "erro";
    alert(err.message);
  }
  setTimeout(() => { b.disabled = false; b.textContent = "Sincronizar"; }, 1800);
});

(async function inicia() {
  aplica(await api("/api/estado"));
  setInterval(async () => {
    // Não pinta por cima de um diálogo aberto nem durante um arrasto.
    if (document.querySelector("dialog[open]") || arrastando) return;
    try { aplica(await api("/api/estado")); } catch { /* rede caiu, tenta depois */ }
  }, 30000);
})();
