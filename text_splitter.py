def split_text(text: str) -> list[str]:
    length = len(text)
    if length <= 200:
        return [text]
    chunk_size = 400
    overlap = 50
    step = chunk_size - overlap
    chunks = []
    index = 0
    while index < length:
        chunk = text[index : index + chunk_size]
        chunks.append(chunk)
        if index + chunk_size >= length:
            break
        index += step
    return chunks
