/**
 * Knowledge upload / retrieval protocol helpers (no DOM, no secrets).
 */

export const KNOWLEDGE_PROTOCOL_VERSION = 1 as const;

export const SUPPORTED_KNOWLEDGE_EXTENSIONS = ['.txt', '.md', '.pdf'] as const;

export type KnowledgeUploadResponse = {
  document_id: string;
  name: string;
  chunk_count: number;
  duplicate: boolean;
  status: string;
};

export type KnowledgeStatusResponse = {
  storage_type: 'in_memory';
  document_count: number;
  chunk_count: number;
  embedding_model: string;
  upload_enabled: boolean;
  supported_file_types: string[];
  limits: {
    max_upload_bytes: number;
    max_document_characters: number;
    max_pdf_pages: number;
    chunk_max_characters: number;
    chunk_overlap_characters: number;
    top_k: number;
    min_similarity: number;
  };
};

export type KnowledgeRetrievalSource = {
  label: string;
  document_name: string;
  page: number | null;
};

export type KnowledgeRetrievalMessage = {
  type: 'knowledge.retrieval';
  version: 1;
  query_id: string;
  status: string;
  sources: KnowledgeRetrievalSource[];
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function extensionOf(filename: string): string {
  const base = filename.split(/[/\\]/).pop() || filename;
  const dot = base.lastIndexOf('.');
  if (dot < 0) return '';
  return base.slice(dot).toLowerCase();
}

export function isSupportedKnowledgeFile(filename: string): boolean {
  return (SUPPORTED_KNOWLEDGE_EXTENSIONS as readonly string[]).includes(
    extensionOf(filename)
  );
}

export function validateKnowledgeFileClient(
  file: { name: string; size: number },
  maxBytes: number
): { ok: true } | { ok: false; message: string } {
  if (!isSupportedKnowledgeFile(file.name)) {
    return { ok: false, message: 'Supported types are .txt, .md, and .pdf' };
  }
  if (file.size <= 0) {
    return { ok: false, message: 'File is empty' };
  }
  if (file.size > maxBytes) {
    return { ok: false, message: 'File exceeds the maximum upload size' };
  }
  return { ok: true };
}

export function parseKnowledgeUploadResponse(
  raw: unknown
): KnowledgeUploadResponse | null {
  if (!isObject(raw)) return null;
  if (typeof raw.document_id !== 'string') return null;
  if (typeof raw.name !== 'string') return null;
  if (typeof raw.chunk_count !== 'number') return null;
  if (typeof raw.duplicate !== 'boolean') return null;
  if (typeof raw.status !== 'string') return null;
  if ('chunk_text' in raw || 'embedding' in raw || 'embeddings' in raw) {
    return null;
  }
  return raw as KnowledgeUploadResponse;
}

export function parseKnowledgeStatusResponse(
  raw: unknown
): KnowledgeStatusResponse | null {
  if (!isObject(raw)) return null;
  if (raw.storage_type !== 'in_memory') return null;
  if (typeof raw.document_count !== 'number') return null;
  if (typeof raw.chunk_count !== 'number') return null;
  if (typeof raw.embedding_model !== 'string') return null;
  if (typeof raw.upload_enabled !== 'boolean') return null;
  if (!Array.isArray(raw.supported_file_types)) return null;
  if (!isObject(raw.limits)) return null;
  if ('OPENAI_API_KEY' in raw) return null;
  return raw as KnowledgeStatusResponse;
}

export function parseKnowledgeRetrievalMessage(
  raw: unknown
): KnowledgeRetrievalMessage | null {
  if (!isObject(raw)) return null;
  if (raw.type !== 'knowledge.retrieval') return null;
  if (raw.version !== 1) return null;
  if (typeof raw.query_id !== 'string') return null;
  if (typeof raw.status !== 'string') return null;
  if (!Array.isArray(raw.sources)) return null;
  for (const src of raw.sources) {
    if (!isObject(src)) return null;
    if (typeof src.label !== 'string') return null;
    if (typeof src.document_name !== 'string') return null;
    if (!(src.page === null || typeof src.page === 'number')) return null;
    if ('text' in src || 'chunk_text' in src || 'embedding' in src) return null;
  }
  return raw as KnowledgeRetrievalMessage;
}

/** Frontend types must never include raw chunk/embedding fields. */
export const KNOWLEDGE_FORBIDDEN_KEYS = [
  'chunk_text',
  'embedding',
  'embeddings',
  'OPENAI_API_KEY',
] as const;
