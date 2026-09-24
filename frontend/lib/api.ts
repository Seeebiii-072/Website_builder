const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface ProjectResponse {
  id: string;
  name: string;
  prompt: string;
  status: 'CREATING' | 'GENERATING' | 'BUILDING' | 'READY' | 'FAILED';
  error_message: string | null;
  preview_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface FileEntry {
  path: string;
  type: 'file' | 'dir';
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  createProject: (name: string, prompt: string) =>
    fetch(`${API_URL}/api/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, prompt }),
    }).then((r) => handle<ProjectResponse>(r)),

  getProject: (id: string) =>
    fetch(`${API_URL}/api/projects/${id}`).then((r) => handle<ProjectResponse>(r)),

  generate: (id: string) =>
    fetch(`${API_URL}/api/projects/${id}/generate`, { method: 'POST' }).then((r) =>
      handle<ProjectResponse>(r)
    ),

  edit: (id: string, message: string) =>
    fetch(`${API_URL}/api/projects/${id}/edit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    }).then((r) => handle<ProjectResponse>(r)),

  getFileTree: (id: string) =>
    fetch(`${API_URL}/api/projects/${id}/files`).then((r) => handle<FileEntry[]>(r)),

  getFile: (id: string, path: string) =>
    fetch(`${API_URL}/api/projects/${id}/files/${encodeURIComponent(path)}`).then((r) =>
      handle<{ path: string; content: string }>(r)
    ),

  restartPreview: (id: string) =>
    fetch(`${API_URL}/api/projects/${id}/preview/restart`, { method: 'POST' }).then((r) =>
      handle<any>(r)
    ),

  exportUrl: (id: string) => `${API_URL}/api/projects/${id}/export`,

  eventsUrl: (id: string) => `${API_URL}/api/projects/${id}/events`,
};
