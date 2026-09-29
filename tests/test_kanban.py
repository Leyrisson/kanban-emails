#!/usr/bin/env python3
"""Testes do Kanban de e-mails. Roda com a stdlib, sem dependência extra:

    python3 -m unittest discover -s tests -v

O foco é o que já nos mordeu antes: card duplicado na releitura do IMAP e o
martelo do `Restart=always` quando falta credencial.
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp(prefix="kanban-test-")
os.environ["KANBAN_DB"] = str(Path(TMP) / "teste.db")

db = importlib.import_module("db")


def novo_banco():
    c = db.conecta()
    c.executescript("DROP TABLE IF EXISTS tarefas; DROP TABLE IF EXISTS colunas;"
                    " DROP TABLE IF EXISTS meta; DROP TABLE IF EXISTS radar;")
    db.cria_schema(c)
    return c


class TestQuadro(unittest.TestCase):
    def setUp(self):
        self.c = novo_banco()

    def tearDown(self):
        self.c.close()

    def test_colunas_padrao(self):
        nomes = [x["nome"] for x in db.colunas(self.c)]
        self.assertEqual(nomes[0], "Entrada")
        self.assertIn("Concluído", nomes)
        self.assertEqual(len(nomes), 7)

    def test_radar_tem_coluna_propria_e_nao_e_cobrada(self):
        """O radar é consulta, não trabalho: ficar fora do lembrete é o ponto."""
        cols = {x["nome"]: x for x in db.colunas(self.c)}
        self.assertIn("Radar", cols)
        self.assertEqual(cols["Radar"]["concluida"], 1)
        col = cols["Radar"]["id"]
        self.c.execute(
            "INSERT INTO tarefas (mensagem_id,titulo,coluna_id,criado_em)"
            " VALUES ('radar-2026-09-29','📡 Radar',?,?)", (col, db.agora()))
        self.c.commit()
        self.assertEqual(db.pendentes(self.c), [])

    def test_migracao_joga_radar_antigo_na_coluna(self):
        """Quadro criado antes de 29/09: os cards de radar estavam na Entrada."""
        entrada = self.c.execute(
            "SELECT id FROM colunas WHERE nome='Entrada'").fetchone()["id"]
        self.c.execute(
            "INSERT INTO tarefas (mensagem_id,titulo,coluna_id,criado_em)"
            " VALUES ('radar-2026-09-01','📡 Radar antigo',?,?)", (entrada, db.agora()))
        self.c.execute("DELETE FROM colunas WHERE nome='Radar'")   # quadro antigo
        self.c.commit()

        db._migra_radar(self.c)
        self.c.commit()

        linha = self.c.execute(
            "SELECT c.nome AS nome FROM tarefas t JOIN colunas c ON c.id=t.coluna_id"
            " WHERE t.mensagem_id='radar-2026-09-01'").fetchone()
        self.assertEqual(linha["nome"], "Radar")

    def test_nao_duplica_mesmo_email(self):
        col = db.colunas(self.c)[0]["id"]
        for _ in range(3):
            self.c.execute(
                "INSERT OR IGNORE INTO tarefas (mensagem_id,titulo,coluna_id,"
                "criado_em) VALUES (?,?,?,?)",
                ("<abc@mail>", "Vaga de dev", col, db.agora()))
        self.c.commit()
        n = self.c.execute("SELECT COUNT(*) n FROM tarefas").fetchone()["n"]
        self.assertEqual(n, 1, "o mesmo Message-ID virou mais de um card")

    def test_sem_mensagem_id_permite_varios_manuais(self):
        col = db.colunas(self.c)[0]["id"]
        for t in ("Manual A", "Manual B"):
            self.c.execute(
                "INSERT INTO tarefas (mensagem_id,titulo,coluna_id,criado_em)"
                " VALUES (NULL,?,?,?)", (t, col, db.agora()))
        self.c.commit()
        self.assertEqual(
            self.c.execute("SELECT COUNT(*) n FROM tarefas").fetchone()["n"], 2)

    def test_mover_marca_e_reabre_conclusao(self):
        cols = db.colunas(self.c)
        entrada, feito = cols[0]["id"], [x for x in cols if x["nome"] == "Concluído"][0]["id"]
        self.c.execute(
            "INSERT INTO tarefas (titulo,coluna_id,criado_em) VALUES (?,?,?)",
            ("x", entrada, db.agora()))
        tid = self.c.execute("SELECT id FROM tarefas").fetchone()["id"]

        self.assertTrue(db.move(self.c, tid, feito)[0])
        self.assertIsNotNone(self.c.execute(
            "SELECT concluido_em FROM tarefas WHERE id=?", (tid,)).fetchone()["concluido_em"])

        self.assertTrue(db.move(self.c, tid, entrada)[0])
        self.assertIsNone(self.c.execute(
            "SELECT concluido_em FROM tarefas WHERE id=?", (tid,)).fetchone()["concluido_em"])

    def test_move_recusa_coluna_inexistente(self):
        self.assertFalse(db.move(self.c, 1, 9999)[0])

    def test_wip(self):
        col = [x for x in db.colunas(self.c) if x["nome"] == "Fazendo"][0]
        for i in range(col["limite_wip"] + 1):
            self.c.execute(
                "INSERT INTO tarefas (titulo,coluna_id,criado_em) VALUES (?,?,?)",
                (f"t{i}", col["id"], db.agora()))
        self.c.commit()
        self.assertEqual(db.estourou_wip(self.c, col["id"]), col["limite_wip"] + 1)

    def test_pendentes_ignora_colunas_concluidas(self):
        cols = db.colunas(self.c)
        entrada = cols[0]["id"]
        feito = [x for x in cols if x["nome"] == "Concluído"][0]["id"]
        arq = [x for x in cols if x["nome"] == "Arquivado"][0]["id"]
        for nome, col in (("aberta", entrada), ("fechada", feito), ("jogada fora", arq)):
            self.c.execute(
                "INSERT INTO tarefas (titulo,coluna_id,criado_em) VALUES (?,?,?)",
                (nome, col, db.agora()))
        self.c.commit()
        self.assertEqual([t["titulo"] for t in db.pendentes(self.c)], ["aberta"])

    def test_meta_guarda_avanco_do_uid(self):
        self.assertEqual(db.meta_get(self.c, "imap_uid", "0"), "0")
        db.meta_set(self.c, "imap_uid", 4321)
        self.c.commit()
        self.assertEqual(db.meta_get(self.c, "imap_uid"), "4321")
        db.meta_set(self.c, "imap_uid", 5000)
        self.c.commit()
        self.assertEqual(db.meta_get(self.c, "imap_uid"), "5000")


class TestFiltro(unittest.TestCase):
    def setUp(self):
        self.f = importlib.import_module("filtro_local")

    def test_aceita_vaga(self):
        self.assertTrue(self.f.e_relevante(
            "Oportunidade: Desenvolvedor Python", "rh@empresa.com.br",
            "Vimos seu currículo e gostaríamos de agendar uma entrevista."))

    def test_recusa_promocao(self):
        self.assertFalse(self.f.e_relevante(
            "Promoção imperdível de tênis", "ofertas@loja.com",
            "Cupom de 50% off só hoje!"))

    def test_recusa_newsletter(self):
        self.assertFalse(self.f.e_relevante(
            "Newsletter de tecnologia", "newsletter@portal.dev", "As notícias da semana"))

    def test_recusa_no_reply(self):
        self.assertFalse(self.f.e_relevante(
            "Vaga de estagiário", "noreply@vagas.com", "Confira a vaga"))

    def test_recusa_email_proprio(self):
        self.assertFalse(self.f.e_relevante(
            "Vaga de dev", "eu@gmail.com", "para mim mesmo", addr="eu@gmail.com"))

    def test_data_sem_cabecalho_passa(self):
        from email.message import Message
        m = Message()
        self.assertTrue(self.f.e_data_ok(m))

    def test_data_antiga_recusa(self):
        from email.message import Message
        m = Message()
        m["Date"] = "Thu, 01 Jan 2020 10:00:00 -0300"
        self.assertFalse(self.f.e_data_ok(m))

    def test_dec_hdr_com_acento(self):
        self.assertIn("Vaga", self.f.dec_hdr("=?utf-8?q?Vaga_de_Dev?="))


class TestImporter(unittest.TestCase):
    def test_link_gmail_usa_rfc822msgid(self):
        imp = importlib.import_module("importer")
        link = imp.link_gmail("<abc@mail.gmail.com>", "a@b.com")
        self.assertTrue(link.startswith("https://mail.google.com/mail/u/0/#search/"))
        self.assertIn("rfc822msgid", link)
        self.assertNotIn("<", link.split("#")[1])


class TestClassifica(unittest.TestCase):
    """O que separa um quadro útil de 900 linhas de ruído."""

    def setUp(self):
        self.cl = importlib.import_module("classifica")

    def test_portal_vira_radar(self):
        for r in ("noreply@glassdoor.com", "info@infojobs.com.br",
                  "alerts@infojobs.com.br", "jobalerts-noreply@linkedin.com",
                  "sugestaovagas@catho.com.br", "ead@dio.me",
                  "donotreply@match.indeed.com",
                  "sonepar-jobnotification@noreply12.jobs2web.com",
                  "marketing@catho.com.br", "linkedin@em.linkedin.com",
                  "newcomers@email.intch.org"):
            self.assertEqual(self.cl.classifica(r), "radar", r)

    def test_pessoa_vira_cartao(self):
        for r in ("bruna@jobbol.com.br", "rh@empresa.com.br",
                  "messages-noreply@linkedin.com", "indeedapply@indeed.com",
                  "joao.silva@gmail.com", "jonas.tavares@cognizant.com",
                  "no-reply@pandape.com.br"):
            self.assertEqual(self.cl.classifica(r), "cartao", r)

    def test_lixo_some(self):
        for r in ("security-noreply@linkedin.com", "invitations@linkedin.com",
                  "no-reply@cs.linkedin.com", "mailer-daemon@provedor.com",
                  "fujiokadistribuidor@fujiokadistribuidor.com.br",
                  "contato@azureacademynews.com.br", "olx@newsolx.com.br",
                  "no-reply@messages.lifepointspanel.com"):
            self.assertEqual(self.cl.classifica(r, "oportunidades de venda"),
                             "lixo", r)

    def test_assunto_forte_vence_remetente(self):
        """em.linkedin.com é newsletter E notícia de candidatura. O assunto
        decide, senão a candidatura real morre junto com a newsletter."""
        self.assertEqual(
            self.cl.classifica("linkedin@em.linkedin.com",
                               "saiba quem se candidatou para a vaga de Analista N2"),
            "cartao")
        self.assertEqual(
            self.cl.classifica("linkedin@em.linkedin.com",
                               "Most sellers waste time on the wrong buyers"),
            "radar")

    def test_assunto_forte_resgata_de_lixo(self):
        self.assertEqual(
            self.cl.classifica("security-noreply@linkedin.com",
                               "Processo seletivo: convite para participar"),
            "cartao")

    def test_marketing_nao_passa_so_com_oportunidade(self):
        # Remetente desconhecido com "oportunidades" no assunto cai em card;
        # o filtro de relevância do LeitorMail ainda tem a palavra final.
        # O que não pode é o remetente conhecido de marketing escapar.
        self.assertEqual(
            self.cl.classifica("fujiokadistribuidor@fujiokadistribuidor.com.br",
                               "🔥 9.9 com oportunidades para movimentar suas vendas!"),
            "lixo")
        self.assertEqual(
            self.cl.classifica("alguem@fornecedor.com.br",
                               "🔥 9.9 com oportunidades para movimentar suas vendas!"),
            "cartao")

    def test_assunto_forte_nao_pode_ser_fraco(self):
        """Regressão real: aceitar "candidate-se"/"vaga de" no assunto
        ressuscitou os digests do Glassdoor e o quadro foi de 149 para 649
        cards. Estes molds precisam continuar caindo no radar."""
        for s in ("Novas vagas: Trabalho remoto, Brasil. Candidate-se agora!",
                  "A Accurate Software está contratando em São Paulo. Candidate-se a uma das vagas",
                  "Analista De Tecnologia Da Informação na Wolpac e 9 outras vagas em SP",
                  "Suas Vagas Remotas Personalizadas para Renda Extra 🚀 (Sep. 04)",
                  "A DXC Technology está contratando em São Paulo, SP. Candidate-se agora!"):
            self.assertEqual(
                self.cl.classifica("noreply@glassdoor.com", s), "radar", s)
            self.assertEqual(
                self.cl.classifica("jobalerts-noreply@linkedin.com", s), "radar", s)

    def test_notificacao_de_conta_e_lixo(self):
        for r, s in (("notifications@smartrecruiters.com", "Senha de uso único do SmartRecruiters"),
                     ("naoresponda@vagas.com.br", "Recupere seu acesso - VAGAS.com.br"),
                     ("contato@vagas.com.br", "Sua senha foi alterada"),
                     ("noreply-accounts@google.com",
                      "Você compartilhou algum dos seus dados da Conta do Google com LinkedIn"),
                     ("messages-noreply@linkedin.com", "Giovana Gomes, Tech Recruiter, está em alta na sua rede")):
            self.assertEqual(self.cl.classifica(r, s), "lixo", f"{r} / {s}")

    def test_resgata_e_ainda_manda(self):
        """Um assunto de_notificação não pode engolir um assunto de vaga."""
        self.assertEqual(
            self.cl.classifica("no-reply@portal.com.br",
                               "Sicredi - Retorno do Processo Seletivo"),
            "cartao")

    def test_vazio_e_lixo(self):
        self.assertEqual(self.cl.classifica(""), "lixo")
        self.assertEqual(self.cl.classifica(None), "lixo")

    def test_fonte(self):
        self.assertEqual(self.cl.fonte("rh@www.acme.com.br"), "acme.com.br")
        self.assertEqual(self.cl.fonte("sem-arroba"), "sem-arroba")


class TestRadar(unittest.TestCase):
    def setUp(self):
        self.c = novo_banco()

    def tearDown(self):
        self.c.close()

    def test_soma_por_dia(self):
        db.radar_registra(self.c, "2026-09-29", "glassdoor.com")
        db.radar_registra(self.c, "2026-09-29", "glassdoor.com")
        qtd, fontes = db.radar_registra(self.c, "2026-09-29", "linkedin.com")
        self.assertEqual(qtd, 3)
        self.assertEqual(fontes["glassdoor.com"], 2)
        self.assertEqual(fontes["linkedin.com"], 1)

    def test_dias_nao_se_misturam(self):
        db.radar_registra(self.c, "2026-09-28", "catho.com.br")
        qtd, _ = db.radar_registra(self.c, "2026-09-29", "catho.com.br")
        self.assertEqual(qtd, 1)

    def test_resumo_ordena_e_corta(self):
        fontes = {f"p{i}.com": 10 - i for i in range(9)}
        r = db.radar_resumo(fontes, limite=3)
        self.assertIn("p0.com (10)", r)
        self.assertIn("+6 portal(is)", r)
        self.assertNotIn("p5.com", r)

    def test_resumo_vazio(self):
        self.assertEqual(db.radar_resumo({}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
