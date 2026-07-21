"""
Neo4j como GRAFO puro (não vector store), acessado pelo driver oficial `neo4j`.

`Neo4jGraphStore` é um wrapper fino sobre o driver: cuida da conexão e expõe
helpers para rodar Cypher de escrita/leitura. Componentes Haystack custom
(Neo4jGraphWriter, Neo4jSubgraphRetriever) recebem uma instância deste store
via factory, no mesmo espírito das outras factories do projeto.
"""
from typing import Any

from loguru import logger
from neo4j import GraphDatabase

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class Neo4jGraphStore:
    def __init__(self, url: str, username: str, password: str, database: str):
        self.database = database
        self._driver = GraphDatabase.driver(url, auth=(username, password))

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def execute_write(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict]:
        with self._driver.session(database=self.database) as session:
            result = session.execute_write(
                lambda tx: [r.data() for r in tx.run(cypher, parameters or {})]
            )
        return result

    def execute_read(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict]:
        with self._driver.session(database=self.database) as session:
            result = session.execute_read(
                lambda tx: [r.data() for r in tx.run(cypher, parameters or {})]
            )
        return result

    def close(self) -> None:
        self._driver.close()


class Neo4jGraphStoreFactory(ComponentFactory):
    """Factory do Neo4jGraphStore. Defaults vêm do settings (.env)."""

    def __init__(
        self,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ):
        self.url = url or settings.NEO4J_URL
        self.username = username or settings.NEO4J_USERNAME
        self.password = password or settings.NEO4J_PASSWORD
        self.database = database or settings.NEO4J_DATABASE

    def build(self) -> Neo4jGraphStore:
        logger.info("Initializing Neo4jGraphStore: url=%s db=%s", self.url, self.database)
        store = Neo4jGraphStore(
            url=self.url,
            username=self.username,
            password=self.password,
            database=self.database,
        )
        return store
