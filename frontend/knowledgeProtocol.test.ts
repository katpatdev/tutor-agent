import { describe, expect, it } from 'vitest';
import {
  KNOWLEDGE_FORBIDDEN_KEYS,
  isSupportedKnowledgeFile,
  parseKnowledgeRetrievalMessage,
  parseKnowledgeStatusResponse,
  parseKnowledgeUploadResponse,
  validateKnowledgeFileClient,
} from './knowledgeProtocol';

describe('knowledgeProtocol', () => {
  it('validates supported extensions', () => {
    expect(isSupportedKnowledgeFile('a.txt')).toBe(true);
    expect(isSupportedKnowledgeFile('a.md')).toBe(true);
    expect(isSupportedKnowledgeFile('a.pdf')).toBe(true);
  });

  it('rejects invalid extensions', () => {
    expect(isSupportedKnowledgeFile('a.docx')).toBe(false);
    expect(
      validateKnowledgeFileClient({ name: 'a.exe', size: 10 }, 1000).ok
    ).toBe(false);
  });

  it('validates client-side size', () => {
    expect(
      validateKnowledgeFileClient({ name: 'a.txt', size: 5000 }, 100).ok
    ).toBe(false);
    expect(
      validateKnowledgeFileClient({ name: 'a.txt', size: 50 }, 100).ok
    ).toBe(true);
  });

  it('parses upload success', () => {
    const parsed = parseKnowledgeUploadResponse({
      document_id: 'd1',
      name: 'eq.txt',
      chunk_count: 2,
      duplicate: false,
      status: 'ready',
    });
    expect(parsed?.document_id).toBe('d1');
  });

  it('rejects upload payloads with chunk text or embeddings', () => {
    expect(
      parseKnowledgeUploadResponse({
        document_id: 'd1',
        name: 'eq.txt',
        chunk_count: 1,
        duplicate: false,
        status: 'ready',
        chunk_text: 'secret',
      })
    ).toBeNull();
  });

  it('parses knowledge status', () => {
    const parsed = parseKnowledgeStatusResponse({
      storage_type: 'in_memory',
      document_count: 1,
      chunk_count: 3,
      embedding_model: 'text-embedding-3-small',
      upload_enabled: true,
      supported_file_types: ['.txt', '.md', '.pdf'],
      limits: {
        max_upload_bytes: 1000,
        max_document_characters: 1000,
        max_pdf_pages: 10,
        chunk_max_characters: 100,
        chunk_overlap_characters: 10,
        top_k: 4,
        min_similarity: 0.3,
      },
    });
    expect(parsed?.storage_type).toBe('in_memory');
  });

  it('parses retrieval sources and rejects malformed', () => {
    const ok = parseKnowledgeRetrievalMessage({
      type: 'knowledge.retrieval',
      version: 1,
      query_id: 'q1',
      status: 'ok',
      sources: [{ label: 'S1', document_name: 'Guide', page: 2 }],
    });
    expect(ok?.sources[0]?.label).toBe('S1');
    expect(
      parseKnowledgeRetrievalMessage({
        type: 'knowledge.retrieval',
        version: 1,
        query_id: 'q1',
        status: 'ok',
        sources: [{ label: 'S1', document_name: 'Guide', page: 2, text: 'nope' }],
      })
    ).toBeNull();
  });

  it('does not declare forbidden knowledge keys', () => {
    expect(KNOWLEDGE_FORBIDDEN_KEYS).toContain('chunk_text');
    expect(KNOWLEDGE_FORBIDDEN_KEYS).toContain('embedding');
    expect(KNOWLEDGE_FORBIDDEN_KEYS).toContain('OPENAI_API_KEY');
  });
});
