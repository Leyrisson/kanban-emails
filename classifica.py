#!/usr/bin/env python3
"""Kanban de e-mails — classificação do remetente e do assunto.

O filtro de relevância do LeitorMail responde "isso fala de emprego?". Ele não
distingue **quem** mandou nem **em que tom**, e essa é a diferença entre um
quadro útil e um lixão: no teste real, 90% dos e-mails de emprego eram o mesmo
portal te avisando que achou 10 vagas. Isso não é tarefa — é ruído com formato
de tarefa.

Quatro destinos:

  lixo    — marketing, newsletter, pesquisa de satisfação, notificação de
            segurança. Não é vaga, some.
  radar   — digest automático de portal de vaga. Vira **um** card por dia, não
            431. Continua acessível, mas ocupa uma linha só.
  cartao  — alguém te escreveu: recrutador, RH, convite de entrevista,
            confirmação de candidatura. Vira card de verdade.

A ordem das regras é o ponto todo. O assunto tem poder de **resgatar**: um
e-mail de `em.linkedin.com` pode ser newsletter ("Most sellers waste time on
the wrong buyers") ou pode ser "saiba quem se candidatou para a vaga de
Analista N2". Classificar só pelo remetente jogaria a segunda fora. Por isso o
sinal forte de assunto é conferido **antes** do remetente cair no radar.

Este módulo é nosso e não é reaproveitado do LeitorMail por isso: é a camada
que o filtro de lá não tem.
"""
import re

# Assunto com sinal forte de que existe uma ação humana em jogo. Vence
# qualquer regra de remetente e de radar.
#
# A lista é curta de propósito. A primeira versão aceitava "candidate-se" e
# "vaga de" — que são exatamente os molds dos digests do Glassdoor
# ("Candidate-se agora!", "Novas vagas: Trabalho remoto") — e o quadro foi de
# 149 para 649 cards. Sin(subject) frouxo é pior que nenhum: ele ressuscita
# justamente o ruído que o radar existe para engolir.
ASSUNTO_JOB = re.compile(
    r"(processo seletivo|"
    r"candidatou[- ]se para|se candidatou para|saiba quem se candidatou|"
    r"retorno (d[oa]|do) (processo|curr[íi]culo)|"
    r"convite para (participar|uma|o processo)|"
    r"interesse em fazer parte|"
    r"aguardo da sua resposta|aguardando (a|sua) resposta|"
    r"entrevista (agendada|confirmada|marcada|realizada)|"
    r"preenchimento de cadastro|"
    r"seu (curr[íi]culo|perfil)|curr[íi]culo (foi|recebido|em an[áa]lise)|"
    r"retorno do seu|encaminhamento do seu)", re.I)

# Assunto de notificação de conta ou de rede social. Não chega a "lixo" por
# remetente — chega pelo assunto, porque o remetente é sempre "no-reply@".
ASSUNTO_RUIDO = re.compile(
    r"(senha|uso [úu]nico|recupere seu acesso|recupera[çc][ãa]o de senha|"
    r"altera[çc][ãa][ão]o? de senha|sua senha foi|c[oó]digo (de|para) (acesso|autentica)|"
    r"\b2fa\b|autentica[çc][ãa]o em duas etapas|"
    r"est[áa] em alta na sua rede|adicione .{0,30} na sua rede|"
    r"voc[êe] compartilhou (algum|alguns)\b|"
    r"dados da sua conta (do )?google|compartilhamento de dados|"
    r"newsletter|boletim|atualize suas prefer)", re.I)

# Remetente que nunca é tarefa.
LIXO = re.compile(
    r"(security-noreply|invitations@|no-?reply-?@(cs|em)\.linkedin\.com|"
    r"@bounce|bounce-?@|mailer-daemon|postmaster|"
    r"@fujiokadistribuidor\.com\.br|"          # "oportunidades" no marketing dele
    r"@azureacademynews\.com\.br|"
    r"@newsolx\.com\.br|"
    r"@arenabreakoutinfinite\.com|"
    r"\.lifepointspanel\.com$|"                   #vem como messages.lifepointspanel.com
    r"@burh\.com\.br|"
    r"@news\.n8n\.io|"
    r"@loopcv\.com|"
    r"@mailquickin\.com)", re.I)

# Lista de exceção: cai no "cartao" mesmo tendo o domínio no RADAR abaixo.
CARTAO = re.compile(
    r"^messages-noreply@linkedin\.com$"   # InMail: é recrutador te escrevendo
    r"|indeedapply@indeed\.com", re.I)

# Digest automático de portal de vaga: vira um card por dia, não 431.
RADAR = re.compile(
    r"@glassdoor\.com$|"
    r"@infojobs\.com\.br$|"
    r"@linkedin\.com$|"
    r"@em\.linkedin\.com$|"                # newsletter — o assunto forte resgata
    r"@catho\.com\.br$|"
    r"@match\.indeed\.com$|"
    r"-jobnotification@.*jobs2web\.com$|"
    r"\.intch\.org$|"                           # "Suas Vagas Remotas Personalizadas"
    r"@dio\.me$", re.I)


def classifica(remetente, assunto=""):
    """-> "cartao" | "radar" | "lixo".

    Ordem: sinal de assunto forte > ruído de notificação > lixo conhecido >
    exceção > radar > card.
    """
    r = (remetente or "").strip().lower()
    s = (assunto or "").strip()
    if not r and not s:
        return "lixo"
    if s and ASSUNTO_JOB.search(s):
        return "cartao"
    if s and ASSUNTO_RUIDO.search(s):
        return "lixo"
    if LIXO.search(r):
        return "lixo"
    if CARTAO.search(r):
        return "cartao"
    if RADAR.search(r):
        return "radar"
    return "cartao"


def fonte(remetente):
    """Domínio legível para o card do radar."""
    r = (remetente or "").strip().lower()
    if "@" in r:
        d = r.rsplit("@", 1)[1]
        return d[4:] if d.startswith("www.") else d
    return r or "desconhecido"
