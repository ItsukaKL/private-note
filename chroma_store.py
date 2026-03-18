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


def query_chunks(embedding: list[float], top_k: int, similarity_threshold: float) -> list[str]:
    collection = get_collection()
    result = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
        include=["documents", "distances", "metadatas"],
    )
    documents = result.get("documents", [[]])[0]
    distances = result.get("distances", [[]])[0]
    if not documents:
        return []
    filtered = []
    for doc, dist in zip(documents, distances):
        if dist is None:
            filtered.append(doc)
            continue
        similarity = 1 - dist
        if similarity >= similarity_threshold:
            filtered.append(doc)
    if filtered:
        return filtered
    return documents


def delete_note_chunks(note_id: int) -> None:
    collection = get_collection()
    collection.delete(where={"note_id": note_id})
