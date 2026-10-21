# Retrieval Guide

This guide is intentionally longer so the corpus can create multiple chunks for one document.
Filter aware retrieval helps the model narrow its search to a path prefix and a file type before reading deeply.
Neighbor expansion is useful when a relevant chunk is found in the middle of a larger document and the surrounding
context is needed to answer a question. Document chunk navigation lets the recursive runner inspect the corpus
gradually instead of loading the whole file into the prompt at once.

Additional notes:

- Use list_documents to understand what files exist.
- Use search_with_filters to narrow retrieval scope.
- Use get_document_chunks to inspect chunk IDs and previews for a single document.
- Use read_document_section to fetch a contiguous region by chunk index.
- Use expand_neighbors to recover surrounding context around a promising chunk.
