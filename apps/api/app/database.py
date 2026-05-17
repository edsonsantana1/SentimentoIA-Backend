import logging
from typing import Optional

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from app.config import settings

logger = logging.getLogger(__name__)


class MongoDB:
    """Gerenciador de conexão MongoDB.

    Usa pymongo síncrono por simplicidade. Como as operações são pequenas no MVP,
    isso é suficiente. Para alta escala, migrar para Motor async.
    """

    client: Optional[MongoClient] = None
    db = None

    @classmethod
    async def connect_db(cls):
        """Conecta no MongoDB Atlas/local e cria índices."""
        try:
            cls.client = MongoClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                retryWrites=True,
                w="majority",
            )
            cls.client.admin.command("ping")
            cls.db = cls.client[settings.DATABASE_NAME]

            logger.info("✓ Conectado ao MongoDB com sucesso")
            await cls.create_indexes()

        except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
            logger.error("✗ Erro ao conectar ao MongoDB: %s", exc)

            # Em desenvolvimento, tenta usar mongomock como fallback.
            # Isso permite subir a aplicação sem MongoDB local/Atlas,
            # mas não deve ser usado como banco real.
            if getattr(settings, "ENV", "").lower() == "development":
                try:
                    import mongomock  # type: ignore

                    logger.warning(
                        "! Usando mongomock como fallback (ENV=development)"
                    )
                    cls.client = mongomock.MongoClient()
                    cls.db = cls.client[settings.DATABASE_NAME]
                    logger.info("✓ Conectado ao mongomock (fallback)")
                    await cls.create_indexes()
                    return
                except Exception as mexc:
                    logger.error(
                        "✗ Falha ao ativar mongomock fallback: %s", mexc
                    )

            raise

    @classmethod
    async def close_db(cls):
        """Fecha conexão MongoDB."""
        if cls.client:
            cls.client.close()
            logger.info("✓ Conexão com MongoDB fechada")

    @classmethod
    def _drop_index_if_exists(cls, collection_name: str, index_name: str) -> None:
        """Remove um índice se ele existir.

        Usado apenas para limpar índices legados que podem conflitar com
        partialFilterExpression ou com novos índices compostos por user_id.
        """
        if cls.db is None:
            return

        try:
            collection = cls.db[collection_name]
            if index_name in collection.index_information():
                collection.drop_index(index_name)
                logger.warning(
                    "Índice legado removido: %s.%s",
                    collection_name,
                    index_name,
                )
        except Exception as exc:
            logger.warning(
                "Não foi possível remover índice %s.%s: %s",
                collection_name,
                index_name,
                exc,
            )

    @classmethod
    def _cleanup_legacy_indexes(cls) -> None:
        """Remove índices antigos que costumam causar conflito.

        Esses nomes vêm dos índices automáticos antigos do projeto.
        Se eles já tiverem a estrutura correta, o MongoDB recria abaixo.
        """
        if cls.db is None:
            return

        legacy_indexes = {
            "users": [
                "email_1",
                "openId_1",
            ],
            "search_jobs": [
                "search_id_1",
            ],
            "source_checkpoints": [
                "source_1_query_key_1",
            ],
            "scrape_cache": [
                "user_id_1_hash_1",
            ],
            "monitor_sources": [
                "name_1",
            ],
            "comment_batches": [
                "batch_id_1",
            ],
            "dashboard_settings": [
                "user_id_1",
            ],
        }

        for collection_name, index_names in legacy_indexes.items():
            for index_name in index_names:
                cls._drop_index_if_exists(collection_name, index_name)

    @classmethod
    async def create_indexes(cls):
        """Cria índices para performance e consistência.

        Observação importante:
        - Campos opcionais com unique devem usar partialFilterExpression.
        - Não use {"$ne": None} em partialFilterExpression.
        - Para campos opcionais, prefira {"$type": "string"}.
        """
        if cls.db is None:
            return

        try:
            # Remove índices antigos que poderiam conflitar com os índices abaixo.
            # Se você não quiser essa limpeza automática depois que tudo estabilizar,
            # pode comentar esta linha.
            cls._cleanup_legacy_indexes()

            # USERS
            cls.db.users.create_index(
                "email",
                unique=True,
                partialFilterExpression={
                    "email": {"$type": "string"},
                },
            )

            cls.db.users.create_index(
                "openId",
                unique=True,
                partialFilterExpression={
                    "openId": {"$type": "string"},
                },
            )

            cls.db.users.create_index(
                [("created_at", -1)],
            )

            # SEARCH JOBS
            cls.db.search_jobs.create_index(
                [("user_id", 1), ("created_at", -1)],
            )

            cls.db.search_jobs.create_index(
                "search_id",
                unique=True,
                partialFilterExpression={
                    "search_id": {"$type": "string"},
                },
            )

            cls.db.search_jobs.create_index(
                [("user_id", 1), ("query", 1), ("created_at", -1)],
            )

            cls.db.search_jobs.create_index(
                [("user_id", 1), ("status", 1), ("created_at", -1)],
            )

            # MENTIONS
            cls.db.mentions.create_index(
                [("user_id", 1), ("search_id", 1)],
            )

            cls.db.mentions.create_index(
                [("search_id", 1), ("published_at", -1)],
            )

            cls.db.mentions.create_index("source")
            cls.db.mentions.create_index("sentiment")
            cls.db.mentions.create_index("criticality")

            cls.db.mentions.create_index(
                [("user_id", 1), ("batch_id", 1), ("status", 1), ("created_at", -1)],
            )

            cls.db.mentions.create_index(
                [("user_id", 1), ("external_id", 1), ("batch_id", 1)],
            )

            cls.db.mentions.create_index(
                [("user_id", 1), ("text_fingerprint", 1), ("batch_id", 1)],
            )

            cls.db.mentions.create_index(
                [("user_id", 1), ("content_hash", 1)],
            )

            cls.db.mentions.create_index(
                [("user_id", 1), ("canonical_url", 1)],
            )

            cls.db.mentions.create_index(
                [("user_id", 1), ("source", 1), ("published_at", -1)],
            )

            cls.db.mentions.create_index(
                [("user_id", 1), ("created_at", -1)],
            )

            # SCRAPED ITEMS
            cls.db.scraped_items.create_index(
                [("user_id", 1), ("source", 1), ("query_key", 1), ("created_at", -1)],
            )

            cls.db.scraped_items.create_index(
                [("user_id", 1), ("source", 1), ("query_key", 1), ("canonical_url", 1)],
            )

            cls.db.scraped_items.create_index(
                [("user_id", 1), ("source", 1), ("query_key", 1), ("content_hash", 1)],
            )

            cls.db.scraped_items.create_index(
                [("user_id", 1), ("query_key", 1), ("scraped_at", -1)],
            )

            cls.db.scraped_items.create_index(
                [("user_id", 1), ("sha256_hash", 1)],
            )

            # SCRAPE CACHE
            cls.db.scrape_cache.create_index(
                [("user_id", 1), ("hash", 1)],
                unique=True,
                partialFilterExpression={
                    "user_id": {"$type": "string"},
                    "hash": {"$type": "string"},
                },
            )

            cls.db.scrape_cache.create_index(
                [("user_id", 1), ("query_key", 1), ("created_at", -1)],
            )

            # SOURCE CHECKPOINTS
            # Importante: precisa incluir user_id.
            # O scraper atual usa filtro com source + query_key + user_id.
            cls.db.source_checkpoints.create_index(
                [("user_id", 1), ("source", 1), ("query_key", 1)],
                unique=True,
                partialFilterExpression={
                    "user_id": {"$type": "string"},
                    "source": {"$type": "string"},
                    "query_key": {"$type": "string"},
                },
            )

            cls.db.source_checkpoints.create_index(
                [("user_id", 1), ("updatedAt", -1)],
            )

            # MONITOR SOURCES
            cls.db.monitor_sources.create_index(
                "name",
                unique=True,
                partialFilterExpression={
                    "name": {"$type": "string"},
                },
            )

            cls.db.monitor_sources.create_index(
                [("active", 1), ("priority", -1)],
            )

            # COMMENT BATCHES
            cls.db.comment_batches.create_index(
                [("user_id", 1), ("created_at", -1)],
            )

            cls.db.comment_batches.create_index(
                "batch_id",
                unique=True,
                partialFilterExpression={
                    "batch_id": {"$type": "string"},
                },
            )

            # INSIGHT JOBS
            cls.db.insight_jobs.create_index(
                [("user_id", 1), ("batch_id", 1), ("status", 1), ("created_at", -1)],
            )

            cls.db.insight_jobs.create_index(
                [("user_id", 1), ("status", 1), ("created_at", -1)],
            )

            cls.db.insight_jobs.create_index(
                [("job_id", 1)],
                unique=True,
                partialFilterExpression={
                    "job_id": {"$type": "string"},
                },
            )

            # INSIGHTS
            cls.db.insights.create_index(
                [("user_id", 1), ("batch_id", 1), ("created_at", -1)],
            )

            cls.db.insights.create_index(
                [("user_id", 1), ("created_at", -1)],
            )

            cls.db.insights.create_index(
                [("user_id", 1), ("severity", 1), ("created_at", -1)],
            )

            # CHAT
            cls.db.chat_threads.create_index(
                [("user_id", 1), ("created_at", -1)],
            )

            cls.db.chat_messages.create_index(
                [("user_id", 1), ("thread_id", 1), ("created_at", 1)],
            )

            # DASHBOARD SETTINGS
            cls.db.dashboard_settings.create_index(
                "user_id",
                unique=True,
                partialFilterExpression={
                    "user_id": {"$type": "string"},
                },
            )

            # ALERTS / REPORTS / AUDIT
            cls.db.alerts.create_index(
                [("user_id", 1), ("search_id", 1), ("created_at", -1)],
            )

            cls.db.alerts.create_index(
                [("user_id", 1), ("status", 1), ("created_at", -1)],
            )

            cls.db.reports.create_index(
                [("user_id", 1), ("search_id", 1), ("created_at", -1)],
            )

            cls.db.reports.create_index(
                [("user_id", 1), ("created_at", -1)],
            )

            cls.db.audit_logs.create_index(
                [("user_id", 1), ("created_at", -1)],
            )

            cls.db.audit_logs.create_index(
                [("event", 1), ("created_at", -1)],
            )

            # NPS
            cls.db.nps_responses.create_index(
                [("user_id", 1), ("created_at", -1)],
            )

            cls.db.nps_responses.create_index(
                [("session_id", 1), ("created_at", -1)],
            )

            cls.db.nps_responses.create_index(
                [("module_key", 1), ("created_at", -1)],
            )

            logger.info("✓ Índices criados com sucesso")

        except Exception as exc:
            logger.error("✗ Erro ao criar índices: %s", exc)


def get_db():
    """Retorna instância ativa do MongoDB."""
    return MongoDB.db
