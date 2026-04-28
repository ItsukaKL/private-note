import os
import chromadb

from project_paths import CHROMA_PATH

COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "notes")

_collection = None


def get_collection():
    global _collection
    if _collection is None:
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        _collection = client.get_or_create_collection(COLLECTION_NAME)
    return _collection


def add_chunks(note_id: int, chunks: list[str], embeddings: list[list[float]]) -> None:
    collection = get_collection()
    ids = [f"{note_id}_{index}" for index in range(len(chunks))]
    metadatas = [{"note_id": note_id, "chunk_index": index} for index in range(len(chunks))]
    collection.add(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)


def query_chunks(embedding: list[float], top_k: int, similarity_threshold: float | None = None) -> list[dict]:
    collection = get_collection()
    result = collection.query(
        query_embeddings=[embedding],
        n_results=max(top_k, 1),
        include=["documents", "distances", "metadatas"],
    )
    documents = result.get("documents", [[]])[0]
    distances = result.get("distances", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    if not documents:
        return []

    items = []
    for doc, dist, metadata in zip(documents, distances, metadatas):
        items.append(
            {
                "document": doc,
                "distance": dist,
                "metadata": metadata or {},
            }
        )
    return items


def delete_note_chunks(note_id: int) -> None:
    collection = get_collection()
    collection.delete(where={"note_id": note_id})


def get_note_chunks(note_id: int) -> tuple[list[str], list[list[float]]]:
    collection = get_collection()
    result = collection.get(
        where={"note_id": note_id},
        include=["documents", "embeddings", "metadatas"],
    )
    raw_documents = result.get("documents")
    raw_embeddings = result.get("embeddings")
    raw_metadatas = result.get("metadatas")
    documents = list(raw_documents) if raw_documents is not None else []
    embeddings = raw_embeddings.tolist() if hasattr(raw_embeddings, "tolist") else (list(raw_embeddings) if raw_embeddings is not None else [])
    metadatas = list(raw_metadatas) if raw_metadatas is not None else []
    rows: list[tuple[int, str, list[float]]] = []
    for document, embedding, metadata in zip(documents, embeddings, metadatas):
        payload = metadata or {}
        rows.append((int(payload.get("chunk_index") or 0), str(document or ""), list(embedding or [])))
    rows.sort(key=lambda item: item[0])
    return [item[1] for item in rows], [item[2] for item in rows]
