# Kanban de E-mails

Lê a caixa de entrada do Gmail e transforma **e-mail de trabalho** em cartões
de um quadro kanban local. O quadro fica em `http://127.0.0.1:5055`, com
arrastar e soltar entre colunas, e às 10h, 14h e 17h o bot do Telegram manda o
que ainda está aberto.

O objetivo é que o quadro seja **usável**. Isso parece óbvio e foi a parte
difícil: a primeira versão criou 957 cartões a partir de um mês de caixa de
entrada, e 90% eram o mesmo portal de vagas avisando que achou 10 vagas.

## O que vira cartão

Três destinos, decididos por `classifica.py`:

| Destino | O que é | Vira |
|---|---|---|
| **cartão** | recrutador, RH, convite de entrevista, retorno de processo seletivo, candidatura | um cartão por e-mail |
| **radar** | digest automático de portal (Glassdoor, LinkedIn, InfoJobs, Catho, DIO, Indeed) | **um** cartão por dia, com a contagem |
| **lixo** | newsletter, marketing, pesquisa, notificação de senha, convite de conexão | nada |

O filtro de relevância é o mesmo do [LeitorMail](../leitormail-postits):
responde "isso fala de emprego?". Ele não distingue quem mandou nem em que tom
— por isso existe a camada de cima.

A ordem das regras importa mais que as regras. Um assunto com sinal forte
("processo seletivo", "candidatou-se para a vaga") é conferido **antes** do
remetente cair no radar. Sem isso, o e-mail de `em.linkedin.com` que diz
"saiba quem se candidatou para a vaga de Analista N2" morre junto com a
newsletter do mesmo remetente.

Resultado no mês de teste: **1892 e-mails → 89 cartões + 29 dias de radar**,
contra 957 cartões da versão ingênua.

## Colunas

Padrão de mercado, mas o quadro é dado e não código — renomeie, reordene,
recolora, defina limite de WIP e marque quais colunas contam como concluídas:

`Entrada` · `A fazer` (WIP 8) · `Fazendo` (WIP 3) · `Aguardando` ·
`Concluído` · `Arquivado` · `Radar`

Só o que está em coluna **não** concluída entra no lembrete. `Radar` é a única
exceção de propósito: ela é marcada como concluída porque é material de
consulta, não trabalho em aberto — cobrar os 881 alertas que já viraram um card
só seria cobrar o resumo do resumo.

## Instalação

```bash
git clone <este-repo> ~/GitHub/kanban-emails

# O venv mora fora do repo de propósito: o diretório do projeto é publicável.
python3 -m venv ~/.local/share/kanban-emails/venv
~/.local/share/kanban-emails/venv/bin/pip install -r requirements.txt

mkdir -p ~/.config/kanban-emails
cp credenciais.env.example ~/.config/kanban-emails/credenciais.env
chmod 600 ~/.config/kanban-emails/credenciais.env
# edite e preencha GMAIL_ADDR/GMAIL_PASS e TELEGRAM_*
```

Para o Gmail é preciso uma **senha de app** (com verificação em duas etapas
ligada), não a senha da conta — o IMAP recusa a senha normal.

```bash
mkdir -p ~/.local/state/kanban-emails
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kanban-emails.service
systemctl --user enable --now kanban-sync.timer kanban-lembrete.timer
```

A primeira sincronização lê o backlog inteiro desde `DATA_MIN` (~2 min para
1890 e-mails). As seguintes levam ~2 s: o marcador de UID fica gravado e só o
que chegou de novo é lido.

## Testes

```bash
~/.local/share/kanban-emails/venv/bin/python -m unittest discover -s tests -v
```

34 testes, sem dependência de rede. Cobrem o que já quebrou de verdade: a
trava de duplicidade, o marcador de UID e os moldes de assunto que
ressuscitaram os digests.

## Decisões que custaram para acertar

**`Mensagem-ID` com UNIQUE.** O timer roda a cada 10 minutos. Sem a trava, o
mesmo digest viraria cartão novo toda vez. É a segunda linha de defesa: o
marcador de UID é a primeira, mas ele é gravado **na mesma transação** que os
cartões — se o processo morrer no meio, o trecho é relido e a trava absorve.

**`UID SEARCH`, não `SEARCH`.** Número de sequência muda quando um e-mail é
apagado, e o marcador atravessa reinícios. E na resposta do `UID FETCH` o
primeiro número da cabeça é a *sequência* — o UID vem depois da palavra `UID`.
Ler o número errado fez o marcador gravar lixo e a segunda sincronização
reprocessar os 1890 e-mails (2 min 43 em vez de 2 s).

**Piso de data no servidor.** `(SINCE "01-Sep-2026")` vai na busca do IMAP.
Sem isso o servidor manda anos de caixa para o filtro local descartar depois.

**Lote, não um-a-um.** `UID FETCH` de 100 em 100. Um `FETCH` por e-mail eram
1890 idas e voltas ao Gmail.

**Token em toda escrita.** O quadro roda só em localhost, mas qualquer site
aberto no navegador poderia mandar POST para o seu `127.0.0.1`. Toda rota de
escrita exige um token que o processo gera no boot e injeta na página.

**Radar em coluna própria, e marcada como concluída.** Quando o radar ainda
morava no `Entrada`, o lembrete passou a listar os 29 cards de radar um a um.
Tirá-lo para uma coluna só resolveu o layout mas não a cobrança; marcar a
coluna como concluída tira o radar do lembrete sem esconder o card. A migração
disso roda sozinha em quadro criado antes dela.

## Arquivos

```
app.py            Flask: rotas e API do quadro
db.py             SQLite: colunas, tarefas, radar, marcador de UID
importer.py       IMAP: e-mails de trabalho -> cartões
classifica.py     remetente+assunto -> cartão | radar | lixo
filtro_local.py   filtro de relevância (fallback se o LeitorMail não existir)
lembrete.py       resumo das 10h/14h/17h no Telegram
templates/        quadro (arrastar e soltar, JS puro, sem build)
systemd/          units do app, do sync e do lembrete
```

`importer.py` prefere o filtro do LeitorMail quando ele está instalado; manter a
regra de relevância em dois lugares é a forma garantida de elas divergirem. O
`filtro_local.py` é o mesmo filtro, para o repositório funcionar sozinho.

## Onde ficam os dados

- Banco: `~/.local/state/kanban-emails/kanban.db` (SQLite, WAL)
- Logs: `~/.local/state/kanban-emails/{sync,lembrete}.log`
- Segredos: `~/.config/kanban-emails/credenciais.env` (modo 600)

Nada disso entra no Git — o `.gitignore` cobre e o venv mora fora do repo.

## Limitações conhecidas

- O `fetch` do IMAP é read-only: o kanban **não** marca como lido, não move e
  não arquiva nada na sua caixa. O quadro é uma vista, não um cliente.
- Um `UID FETCH` que falha pela metade para o lote ali e a próxima rodada
  refaz — de propósito, porque perder e-mail é pior que reler.
- O link "abrir" usa a busca `rfc822msgid:` do Gmail, porque o IMAP não devolve
  o thread id. Funciona no Gmail; em outro provedor, ajuste `link_gmail()`.
