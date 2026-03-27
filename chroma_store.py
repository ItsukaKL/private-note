import os
import chromadb


CHROMA_PATH = os.getenv("CHROMA_PATH", "data/chroma")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "notes")

_collection = None


def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
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
