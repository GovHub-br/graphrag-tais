from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from rag.components.converters.graph_card_builder import GraphCardBuilder
from rag.components.converters.metadata_record_builder import _texto
from rag.components.converters.metadata_record_builder import MetadataRecordBuilder
from rag.components.sources.openmetadata_source import OpenMetadataSource
from rag.components.sources.relationship_adapters import (
    MCDMarkdownRelationshipAdapter,
)
from rag.settings.configs import settings
from rag.components.writers.neo4j_graph_writer import Neo4jGraphWriter


SERVICE = "Cidades"
DATABASE = "cidades"
SCHEMA = "Cidades.cidades.empreendimento_far"
SOURCE_TABLE = f"{SCHEMA}.empreendimento"
TARGET_TABLE = f"{SCHEMA}.cadastro_pj"
SOURCE_COLUMN = f"{SOURCE_TABLE}.apf"
TARGET_COLUMN = f"{TARGET_TABLE}.apf"


def _markdown() -> str:
    return """## Relações semânticas MCID

Catálogo `mcid-semantic-relationships`, evidência agregada de `2026-07-23T03:51:45+00:00`.

### Relações explícitas

- **Contrato validado por teste dbt** (origem): `empreendimento_far.empreendimento.apf` → `empreendimento_far.cadastro_pj.apf`; chave `apf`; JOIN `exact`; cardinalidade `many_to_one`; confiança `high`.
- **Relação candidata** (origem): `empreendimento_far.empreendimento.apf` → `empreendimento_far.cadastro_pj.apf`; chave `apf`; JOIN `normalized_exact`; cardinalidade `unknown`; confiança `low`.

### Lógica de JOIN do modelo
"""


def _validated_markdown(join_rule: str) -> str:
    return f"""## Relações semânticas MCID

Catálogo `mcid-semantic-relationships`, evidência agregada de `2026-07-23T03:51:45+00:00`.

### Relações explícitas

- **Relação validada por agregados** (origem): `empreendimento_far.empreendimento.apf` → `empreendimento_far.cadastro_pj.apf`; chave `apf`; JOIN `{join_rule}`; cardinalidade `many_to_one`; confiança `high`.
"""


def _table(name: str, table_id: str, related_table: str) -> dict:
    table_fqn = f"{SCHEMA}.{name}"
    return {
        "id": table_id,
        "name": name,
        "fullyQualifiedName": table_fqn,
        "description": f"Descrição documental de {name}.",
        "tableType": "Regular",
        "service": {"name": SERVICE, "fullyQualifiedName": SERVICE},
        "database": {
            "name": DATABASE,
            "fullyQualifiedName": f"{SERVICE}.{DATABASE}",
        },
        "databaseSchema": {
            "name": "empreendimento_far",
            "fullyQualifiedName": SCHEMA,
        },
        "columns": [
            {
                "name": "apf",
                "fullyQualifiedName": f"{table_fqn}.apf",
                "description": "Identificador APF.",
                "dataType": "TEXT",
                "dataTypeDisplay": "text",
                "ordinalPosition": None,
                "constraint": "NULL",
                "tags": [{"tagFQN": "MCID.Identificadores.APF"}],
            }
        ],
        "tableConstraints": [],
        "extension": {
            "mcidSemanticRelationships": _markdown(),
            "mcidRelatedTables": [
                {"fullyQualifiedName": related_table, "type": "table"}
            ],
        },
    }


def _snapshot() -> dict:
    return {
        "scope_id": "mcid",
        "service": SERVICE,
        "database": DATABASE,
        "schemas": [SCHEMA],
        "source_version": "1.12.1",
        "ingestion_run_id": "run-1",
        "tables": [
            _table("empreendimento", "table-1", TARGET_TABLE),
            _table("cadastro_pj", "table-2", SOURCE_TABLE),
        ],
        "stats": {
            "tables_read": 2,
            "tables_accepted": 2,
            "tables_rejected": 0,
            "tables_rejected_by_reason": {},
        },
    }


class OpenMetadataSourceScopeTest(unittest.TestCase):
    def _source(self) -> OpenMetadataSource:
        return OpenMetadataSource(
            host="https://metadata.example",
            api_path="/api/v1",
            jwt_token="token",
            scope_id="mcid",
            service=SERVICE,
            database=DATABASE,
            schemas=[SCHEMA],
            expected_version="1.12.1",
            require_documented_tables=True,
            documentation_property="mcidSemanticRelationships",
        )

    def test_accepts_only_scoped_documented_table(self) -> None:
        source = self._source()
        table = _table("empreendimento", "table-1", TARGET_TABLE)
        self.assertEqual(source._accept_table(table, SCHEMA), (True, None))

        undocumented = copy.deepcopy(table)
        undocumented["extension"]["mcidSemanticRelationships"] = ""
        self.assertEqual(
            source._accept_table(undocumented, SCHEMA),
            (False, "missing_documentation_marker"),
        )

        wrong_service = copy.deepcopy(table)
        wrong_service["service"]["name"] = "Outro"
        wrong_service["service"]["fullyQualifiedName"] = "Outro"
        self.assertEqual(
            source._accept_table(wrong_service, SCHEMA),
            (False, "service_out_of_scope"),
        )

    def test_rejects_schema_without_full_fqn(self) -> None:
        with self.assertRaisesRegex(ValueError, "OM_SCHEMAS"):
            OpenMetadataSource(
                host="https://metadata.example",
                api_path="/api/v1",
                scope_id="mcid",
                service=SERVICE,
                database=DATABASE,
                schemas=["empreendimento_far"],
                expected_version="1.12.1",
            )

    def test_reads_cursor_pagination_per_schema(self) -> None:
        source = self._source()
        calls: list[dict] = []
        payloads = iter(
            [
                {
                    "data": [{"fullyQualifiedName": f"{SCHEMA}.a"}],
                    "paging": {"after": "cursor-1"},
                },
                {
                    "data": [{"fullyQualifiedName": f"{SCHEMA}.b"}],
                    "paging": {},
                },
            ]
        )

        class Response:
            def __init__(self, payload: dict):
                self.payload = payload

            def json(self) -> dict:
                return self.payload

        def request(_client, endpoint: str, *, params: dict | None = None):
            self.assertEqual(endpoint, "/tables")
            calls.append(dict(params or {}))
            return Response(next(payloads))

        source._request = request  # type: ignore[method-assign]
        tables, pages = source._read_schema(object(), SCHEMA)
        self.assertEqual(pages, 2)
        self.assertEqual(len(tables), 2)
        self.assertNotIn("after", calls[0])
        self.assertEqual(calls[1]["after"], "cursor-1")
        self.assertEqual(calls[0]["databaseSchema"], SCHEMA)
        self.assertEqual(
            calls[0]["fields"],
            "columns,tableConstraints,tags,extension,dataProducts",
        )

    def test_extension_overrides_apply_before_documentation_filter(self) -> None:
        import json
        import os
        import tempfile

        table = _table("empreendimento", "table-1", TARGET_TABLE)
        table["extension"] = {"outraPropriedade": "fica"}
        override = {
            SOURCE_TABLE: {"mcidSemanticRelationships": _markdown()},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(override, fh)
        try:
            source = OpenMetadataSource(
                host="https://metadata.example",
                api_path="/api/v1",
                jwt_token="token",
                scope_id="mcid",
                service=SERVICE,
                database=DATABASE,
                schemas=[SCHEMA],
                expected_version="1.12.1",
                require_documented_tables=True,
                documentation_property="mcidSemanticRelationships",
                extension_overrides_file=fh.name,
            )
        finally:
            os.unlink(fh.name)
        with patch.object(source, "_read_version", return_value="1.12.1"), patch.object(
            source, "_read_schema", return_value=([table], 1)
        ):
            snapshot = source.run()["snapshot"]

        # Sem a sobreposição a tabela seria rejeitada por falta do marcador.
        self.assertEqual(snapshot["stats"]["extension_overrides_applied"], 1)
        extension = snapshot["tables"][0]["extension"]
        self.assertEqual(extension["outraPropriedade"], "fica")
        self.assertIn("mcidSemanticRelationships", extension)

    def test_retries_server_error(self) -> None:
        source = self._source()

        class Response:
            headers: dict[str, str] = {}

            def __init__(self, status_code: int):
                self.status_code = status_code

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(str(self.status_code))

        class Client:
            def __init__(self):
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response(500 if self.calls == 1 else 200)

        client = Client()
        with patch.object(source, "_sleep_before_retry") as sleep:
            response = source._request(client, "/system/version")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.calls, 2)
        sleep.assert_called_once()


class CanonicalRecordTest(unittest.TestCase):
    def _builder(self) -> MetadataRecordBuilder:
        return MetadataRecordBuilder(
            relationship_adapter=MCDMarkdownRelationshipAdapter(
                markdown_property="mcidSemanticRelationships",
                related_tables_property="mcidRelatedTables",
                catalog_id="mcid-semantic-relationships",
            )
        )

    def test_builds_deduplicated_semantic_relationship(self) -> None:
        records = self._builder().run(_snapshot())["records"]
        self.assertEqual(len(records["tables"]), 2)
        self.assertEqual(len(records["columns"]), 2)
        self.assertEqual(
            records["stats"]["relationships_by_type"],
            {
                "HAS_COLUMN": 2,
                "FOREIGN_KEY": 0,
                "SEMANTIC_JOIN": 1,
            },
        )
        semantic = next(
            relationship
            for relationship in records["relationships"]
            if relationship["relationship_type"] == "SEMANTIC_JOIN"
        )
        self.assertEqual(semantic["source_node_id"], SOURCE_COLUMN)
        self.assertEqual(semantic["target_node_id"], TARGET_COLUMN)
        self.assertTrue(semantic["sql_join_eligible"])
        self.assertEqual(semantic["validation"], "dbt_relationships_test")
        self.assertEqual(len(semantic["relationship_id"]), 64)
        self.assertEqual(records["stats"]["semantic_candidates_skipped"], 1)

    _LINEAGE_PROSE = (
        "**Lê de:** `prata_far_empreendimento`\n\n"
        "**É lido por:** `ouro_far_evolucao_financeira_chart`\n\n"
        "_Derivado do grafo do dbt. Não constitui chave estrangeira._"
    )

    def test_lineage_only_markdown_is_counted_not_joined(self) -> None:
        snapshot = _snapshot()
        snapshot["tables"][0]["extension"]["mcidSemanticRelationships"] = (
            self._LINEAGE_PROSE
        )
        builder = MetadataRecordBuilder(
            relationship_adapter=MCDMarkdownRelationshipAdapter(
                markdown_property="mcidSemanticRelationships",
                related_tables_property="mcidRelatedTables",
                catalog_id="mcid-semantic-relationships",
                lineage_only_markers="Lê de, É lido por, Granularidade temporal",
            )
        )
        records = builder.run(snapshot)["records"]
        self.assertEqual(records["stats"]["semantic_lineage_only_tables"], 1)
        # A outra tabela segue publicando a relação com contrato completo.
        self.assertEqual(
            records["stats"]["relationships_by_type"]["SEMANTIC_JOIN"], 1
        )

    def test_unconfirmed_related_table_skips_only_that_edge(self) -> None:
        snapshot = _snapshot()
        for table in snapshot["tables"]:
            table["extension"]["mcidRelatedTables"] = []
        records = self._builder().run(snapshot)["records"]
        self.assertEqual(
            records["stats"]["relationships_by_type"]["SEMANTIC_JOIN"], 0
        )
        self.assertEqual(records["stats"]["semantic_relationships_unconfirmed"], 2)

    def test_lineage_prose_aborts_without_configured_markers(self) -> None:
        # O formato de linhagem é de um escopo: sem os marcadores no .env, o
        # contrato continua estrito, como antes.
        snapshot = _snapshot()
        snapshot["tables"][0]["extension"]["mcidSemanticRelationships"] = (
            self._LINEAGE_PROSE
        )
        with self.assertRaisesRegex(ValueError, "Contrato Markdown desconhecido"):
            self._builder().run(snapshot)

    def test_unknown_markdown_still_aborts(self) -> None:
        snapshot = _snapshot()
        snapshot["tables"][0]["extension"]["mcidSemanticRelationships"] = (
            "## Outra gramática\n\n- `a.b` liga com `c.d`"
        )
        with self.assertRaisesRegex(ValueError, "Contrato Markdown desconhecido"):
            self._builder().run(snapshot)

    def test_rejects_mismatched_composite_foreign_key(self) -> None:
        snapshot = _snapshot()
        snapshot["tables"][0]["tableConstraints"] = [
            {
                "constraintType": "FOREIGN_KEY",
                "columns": ["apf", "outra"],
                "referredColumns": [TARGET_COLUMN],
            }
        ]
        with self.assertRaisesRegex(ValueError, "FK composta inconsistente"):
            self._builder().run(snapshot)

    def test_reads_table_grain_from_configured_custom_property(self) -> None:
        snapshot = _snapshot()
        snapshot["tables"][0]["extension"]["testTableGrain"] = (
            "Uma linha por APF, medida por contagem exata."
        )

        with patch.object(settings, "OM_TABLE_GRAIN_PROPERTY", "testTableGrain"):
            records = self._builder().run(snapshot)["records"]

        table = next(
            item for item in records["tables"] if item["node_id"] == SOURCE_TABLE
        )
        self.assertEqual(
            table["grain"], "Uma linha por APF, medida por contagem exata."
        )

    def test_reads_first_data_product(self) -> None:
        snapshot = _snapshot()
        snapshot["tables"][0]["dataProducts"] = [
            {"name": "empreendimento_far", "displayName": "Empreendimento FAR",
             "description": "Empreendimentos do Fundo de Arrendamento Residencial."}
        ]
        records = self._builder().run(snapshot)["records"]
        by_fqn = {t["node_id"]: t for t in records["tables"]}
        self.assertEqual(by_fqn[SOURCE_TABLE]["data_product"], "empreendimento_far")
        self.assertEqual(by_fqn[SOURCE_TABLE]["data_product_label"], "Empreendimento FAR")
        # Sem Data Product no OM: campos vazios, não erro.
        self.assertEqual(by_fqn[TARGET_TABLE]["data_product"], "")

    def test_allows_configured_normalized_exact_for_compatible_types(self) -> None:
        snapshot = _snapshot()
        for table in snapshot["tables"]:
            table["extension"]["mcidSemanticRelationships"] = (
                _validated_markdown("normalized_exact")
            )
        builder = MetadataRecordBuilder(
            relationship_adapter=MCDMarkdownRelationshipAdapter(
                markdown_property="mcidSemanticRelationships",
                related_tables_property="mcidRelatedTables",
                catalog_id="mcid-semantic-relationships",
                eligible_join_rules="exact,normalized_exact",
            )
        )

        records = builder.run(snapshot)["records"]
        semantic = next(
            relationship
            for relationship in records["relationships"]
            if relationship["relationship_type"] == "SEMANTIC_JOIN"
        )
        self.assertTrue(semantic["sql_join_eligible"])
        self.assertIsNone(semantic["sql_eligibility_reason"])
        self.assertEqual(records["stats"]["semantic_relationships_sql_eligible"], 1)

    def test_rejects_configured_rule_when_endpoint_types_are_incompatible(self) -> None:
        snapshot = _snapshot()
        for table in snapshot["tables"]:
            table["extension"]["mcidSemanticRelationships"] = (
                _validated_markdown("normalized_exact")
            )
        snapshot["tables"][1]["columns"][0]["dataType"] = "INTEGER"
        builder = MetadataRecordBuilder(
            relationship_adapter=MCDMarkdownRelationshipAdapter(
                markdown_property="mcidSemanticRelationships",
                related_tables_property="mcidRelatedTables",
                catalog_id="mcid-semantic-relationships",
                eligible_join_rules="exact,normalized_exact",
            )
        )

        records = builder.run(snapshot)["records"]
        semantic = next(
            relationship
            for relationship in records["relationships"]
            if relationship["relationship_type"] == "SEMANTIC_JOIN"
        )
        self.assertFalse(semantic["sql_join_eligible"])
        self.assertIn("TEXT -> INTEGER", semantic["sql_eligibility_reason"])
        self.assertEqual(
            records["stats"]["semantic_relationships_type_incompatible"], 1
        )


class CardProjectionTest(unittest.TestCase):
    def test_card_ids_and_point_ids_are_stable(self) -> None:
        records = CanonicalRecordTest()._builder().run(_snapshot())["records"]
        builder = GraphCardBuilder(
            card_schema_version="v1",
            embedding_profile="test-profile",
        )
        first = builder.run(records)
        second_records = copy.deepcopy(records)
        second_records["tables"][0]["description"] = "Descrição alterada."
        second = builder.run(second_records)

        first_by_id = {doc.meta["card_id"]: doc for doc in first["documents"]}
        second_by_id = {doc.meta["card_id"]: doc for doc in second["documents"]}
        self.assertEqual(first_by_id.keys(), second_by_id.keys())
        for card_id in first_by_id:
            self.assertEqual(first_by_id[card_id].id, second_by_id[card_id].id)
        changed_card = f"mcid|table:{second_records['tables'][0]['node_id']}"
        self.assertNotEqual(
            first_by_id[changed_card].meta["content_hash"],
            second_by_id[changed_card].meta["content_hash"],
        )
        self.assertEqual(len(first["nodes"]), 4)
        self.assertEqual(len(first["documents"]), 4)

    def test_v2_card_can_leave_relationships_out_of_embedded_text(self) -> None:
        records = CanonicalRecordTest()._builder().run(_snapshot())["records"]

        def contents(embed: bool) -> list[str]:
            builder = GraphCardBuilder(
                card_schema_version="v2",
                embedding_profile="test-profile",
                embed_relationships=embed,
            )
            return [doc.content for doc in builder.run(records)["documents"]]

        self.assertTrue(any("Relacionamentos:" in c for c in contents(True)))
        self.assertFalse(any("Relacionamentos:" in c for c in contents(False)))
        # A aresta continua no payload: só o texto embedado muda.
        sem = GraphCardBuilder(
            card_schema_version="v2",
            embedding_profile="test-profile",
            embed_relationships=False,
        ).run(records)
        self.assertTrue(any(doc.meta["relationship_ids"] for doc in sem["documents"]))


    def test_product_goes_to_payload_and_only_optionally_to_text(self) -> None:
        snapshot = _snapshot()
        snapshot["tables"][0]["dataProducts"] = [
            {"name": "empreendimento_far", "displayName": "Empreendimento FAR",
             "description": "Empreendimentos do Fundo de Arrendamento Residencial."}
        ]
        records = CanonicalRecordTest()._builder().run(snapshot)["records"]

        def build(product_line: str):
            with patch.object(settings, "OM_CARD_PRODUCT_LINE", product_line):
                return GraphCardBuilder(
                    card_schema_version="v2", embedding_profile="test-profile",
                ).run(records)["documents"]

        off = {d.meta["node_id"]: d for d in build("off")}
        on = {d.meta["node_id"]: d for d in build("full")}
        label = {d.meta["node_id"]: d for d in build("label")}
        self.assertEqual(label[SOURCE_TABLE].content.count("Produto: Empreendimento FAR\n")
                         + label[SOURCE_TABLE].content.endswith("Produto: Empreendimento FAR"), 1)
        self.assertNotIn("Fundo de Arrendamento", label[SOURCE_TABLE].content)
        # Compatibilidade com o booleano da primeira versão da flag.
        legacy = {d.meta["node_id"]: d for d in build("true")}
        self.assertEqual(legacy[SOURCE_TABLE].content, on[SOURCE_TABLE].content)
        column = f"{SOURCE_TABLE}.apf"
        self.assertEqual(off[SOURCE_TABLE].meta["data_product"], "empreendimento_far")
        self.assertEqual(off[column].meta["data_product_label"], "Empreendimento FAR")
        self.assertNotIn("Produto:", off[SOURCE_TABLE].content)
        self.assertIn("Produto: Empreendimento FAR — Empreendimentos do Fundo",
                      on[SOURCE_TABLE].content)
        # Card de coluna leva só o rótulo; tabela sem produto não ganha a linha.
        self.assertIn("Produto: Empreendimento FAR", on[column].content)
        self.assertNotIn("Fundo de Arrendamento", on[column].content)
        self.assertNotIn("Produto:", on[TARGET_TABLE].content)


class _FakeGraphStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    def execute_write(self, cypher: str, parameters: dict | None = None) -> list[dict]:
        self.calls.append((cypher, parameters))
        if parameters:
            return [{"written": len(parameters["rows"])}]
        return []


class Neo4jWriterTest(unittest.TestCase):
    def test_uses_labeled_endpoints_and_relationship_id(self) -> None:
        records = CanonicalRecordTest()._builder().run(_snapshot())["records"]
        projection = GraphCardBuilder(
            card_schema_version="v1",
            embedding_profile="test-profile",
        ).run(records)
        store = _FakeGraphStore()
        result = Neo4jGraphWriter(store).run(
            projection["nodes"], projection["relationships"]
        )
        self.assertEqual(result["nodes_written"], 4)
        self.assertEqual(result["relationships_written"], 3)

        relationship_queries = [
            cypher
            for cypher, parameters in store.calls
            if parameters and "relationship_id" in cypher
        ]
        self.assertTrue(relationship_queries)
        self.assertTrue(
            any("MATCH (a:`Column`" in cypher for cypher in relationship_queries)
        )
        self.assertTrue(
            all(
                "{relationship_id: row.relationship_id}" in cypher
                for cypher in relationship_queries
            )
        )


if __name__ == "__main__":
    unittest.main()


class TextoDoOpenMetadataTest(unittest.TestCase):
    """O OM escapa a descrição; o card e o vetor precisam do texto real."""

    def test_desfaz_entidades_html(self):
        cru = "Grão por &#96;idagente&#96; &#43; ano, &#34;porta de entrada&#34;"
        self.assertEqual(
            _texto(cru),
            'Grão por `idagente` + ano, "porta de entrada"',
        )

    def test_idempotente(self):
        """Reingestão sobre dado já limpo não pode corromper."""
        limpo = 'Grão por `idagente`, "porta de entrada"'
        self.assertEqual(_texto(limpo), limpo)
        self.assertEqual(_texto(_texto(limpo)), limpo)

    def test_ausente_vira_string_vazia(self):
        self.assertEqual(_texto(None), "")


class AdapterDesacopladoDeEscopoTest(unittest.TestCase):
    """O código não deve conhecer escopo: escopo novo entra por `.env`."""

    def _build(self, nome, catalog_id=""):
        from rag.components.sources.relationship_adapters import (
            build_relationship_adapter,
        )
        return build_relationship_adapter(
            nome, markdown_property="x", related_tables_property="y",
            catalog_id=catalog_id,
        )

    def test_escopos_existentes_seguem_funcionando(self):
        self.assertEqual(self._build("mcid_markdown_v1").catalog_id,
                         "mcid-semantic-relationships")
        self.assertEqual(self._build("minc_markdown_v1").catalog_id,
                         "minc-semantic-relationships")

    def test_escopo_novo_nao_pede_mudanca_de_codigo(self):
        """Era isto que o registro hardcoded impedia."""
        self.assertEqual(self._build("ipea_markdown_v1").catalog_id,
                         "ipea-semantic-relationships")

    def test_env_sobrepoe_a_convencao(self):
        a = self._build("qualquer_coisa", catalog_id="catalogo-fora-do-padrao")
        self.assertEqual(a.catalog_id, "catalogo-fora-do-padrao")

    def test_source_kind_acompanha_o_adapter(self):
        self.assertEqual(self._build("ipea_markdown_v1").source_kind,
                         "ipea_markdown_v1")

    def test_desligado_devolve_none(self):
        for v in ("", "none", "disabled"):
            self.assertIsNone(self._build(v))

    def test_nome_fora_da_convencao_sem_env_falha(self):
        with self.assertRaises(ValueError):
            self._build("formato-estranho")
