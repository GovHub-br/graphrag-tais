"""Testes do link de download assinado pelo RAG (rota `/files`).

O download deixou de ser URL assinada do MinIO (o navegador não alcança o MinIO)
e passou a ser um link para o próprio RAG. A assinatura é nossa — então é ela
que precisa de teste: o que ela protege é o bucket inteiro.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag.components.storage import download_links  # noqa: E402
from rag.settings.configs import settings  # noqa: E402

BUCKET = "relatorios"
OBJETO = "relatorio-govhub-20260827-101500-a1b2c3.docx"


def _parse(url: str) -> tuple[str, int, str]:
    parts = urlparse(url)
    qs = parse_qs(parts.query)
    return parts.path, int(qs["exp"][0]), qs["sig"][0]


class TestAssinatura(unittest.TestCase):
    def setUp(self) -> None:
        self._base = settings.PUBLIC_BASE_URL
        self._key = settings.DOWNLOAD_SIGNING_KEY
        settings.PUBLIC_BASE_URL = "https://rag.exemplo.gov.br"
        settings.DOWNLOAD_SIGNING_KEY = "chave-de-teste"

    def tearDown(self) -> None:
        settings.PUBLIC_BASE_URL = self._base
        settings.DOWNLOAD_SIGNING_KEY = self._key

    def test_url_aponta_para_o_rag_e_nao_para_o_minio(self):
        url = download_links.build_download_url(BUCKET, OBJETO, 3600)
        self.assertTrue(url.startswith("https://rag.exemplo.gov.br/files/"))
        path, exp, sig = _parse(url)
        self.assertEqual(path, f"/files/{BUCKET}/{OBJETO}")
        self.assertGreater(exp, int(time.time()))
        self.assertEqual(len(sig), 64)  # hexdigest do sha256

    def test_barra_final_da_base_nao_duplica(self):
        settings.PUBLIC_BASE_URL = "https://rag.exemplo.gov.br/"
        url = download_links.build_download_url(BUCKET, OBJETO, 60)
        self.assertNotIn("//files/", url.replace("https://", ""))

    def test_nome_com_caractere_especial_e_escapado(self):
        url = download_links.build_download_url(BUCKET, "amostra ação.csv", 60)
        self.assertIn("amostra%20a%C3%A7%C3%A3o.csv", url)

    def test_link_recem_gerado_e_valido(self):
        _, exp, sig = _parse(download_links.build_download_url(BUCKET, OBJETO, 3600))
        download_links.verify(BUCKET, OBJETO, exp, sig)  # não levanta

    def test_objeto_trocado_invalida(self):
        _, exp, sig = _parse(download_links.build_download_url(BUCKET, OBJETO, 3600))
        with self.assertRaises(download_links.LinkInvalid):
            download_links.verify(BUCKET, "outro-relatorio.docx", exp, sig)

    def test_bucket_trocado_invalida(self):
        _, exp, sig = _parse(download_links.build_download_url(BUCKET, OBJETO, 3600))
        with self.assertRaises(download_links.LinkInvalid):
            download_links.verify("amostras", OBJETO, exp, sig)

    def test_esticar_a_expiracao_invalida_a_assinatura(self):
        """O `exp` entra no HMAC — mexer nele na URL não estende o prazo."""
        _, exp, sig = _parse(download_links.build_download_url(BUCKET, OBJETO, 60))
        with self.assertRaises(download_links.LinkInvalid):
            download_links.verify(BUCKET, OBJETO, exp + 86400, sig)

    def test_assinatura_vazia_ou_lixo_invalida(self):
        _, exp, _ = _parse(download_links.build_download_url(BUCKET, OBJETO, 60))
        for sig in ("", "deadbeef"):
            with self.assertRaises(download_links.LinkInvalid):
                download_links.verify(BUCKET, OBJETO, exp, sig)

    def test_link_vencido_expira(self):
        _, exp, sig = _parse(download_links.build_download_url(BUCKET, OBJETO, -1))
        with self.assertRaises(download_links.LinkExpired):
            download_links.verify(BUCKET, OBJETO, exp, sig)

    def test_chave_diferente_nao_valida(self):
        _, exp, sig = _parse(download_links.build_download_url(BUCKET, OBJETO, 3600))
        settings.DOWNLOAD_SIGNING_KEY = "outra-chave"
        with self.assertRaises(download_links.LinkInvalid):
            download_links.verify(BUCKET, OBJETO, exp, sig)

    def test_sem_chave_propria_usa_a_secret_do_minio(self):
        settings.DOWNLOAD_SIGNING_KEY = ""
        _, exp, sig = _parse(download_links.build_download_url(BUCKET, OBJETO, 3600))
        download_links.verify(BUCKET, OBJETO, exp, sig)  # não levanta
        antes = settings.MINIO_SECRET_KEY
        try:
            settings.MINIO_SECRET_KEY = antes + "-mudou"
            with self.assertRaises(download_links.LinkInvalid):
                download_links.verify(BUCKET, OBJETO, exp, sig)
        finally:
            settings.MINIO_SECRET_KEY = antes


class TestBaseUrlLocal(unittest.TestCase):
    """A falha mais silenciosa do download: link bonito, assinado, e inalcançável.

    `PUBLIC_BASE_URL` ausente do `.env` cai no default `localhost:8000`, e aí o
    link abre na máquina de QUEM CLICOU, não no servidor. O arquivo existe, a
    rota responde, o download falha — e o sintoma não aponta para a causa. O
    boot avisa; estes casos fixam o que conta como 'local'."""

    def test_reconhece_enderecos_da_propria_maquina(self):
        for url in ("http://localhost:8000", "http://127.0.0.1:8000",
                    "http://0.0.0.0:8000", "http://host.docker.internal:8000",
                    "https://LOCALHOST:8443/base"):
            with self.subTest(url=url):
                self.assertTrue(download_links.base_url_looks_local(url))

    def test_url_publica_nao_dispara_o_aviso(self):
        for url in ("https://rag.exemplo.gov.br", "http://10.0.0.50:8000",
                    "https://govhub.gov.br/rag"):
            with self.subTest(url=url):
                self.assertFalse(download_links.base_url_looks_local(url))


if __name__ == "__main__":
    unittest.main()
