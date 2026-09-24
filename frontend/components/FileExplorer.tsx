'use client';

import { useEffect, useMemo, useState } from 'react';
import { api, FileEntry } from '@/lib/api';

interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  children: TreeNode[];
}

function buildTree(entries: FileEntry[]): TreeNode[] {
  const root: TreeNode[] = [];
  const dirMap = new Map<string, TreeNode>();

  for (const entry of entries) {
    const parts = entry.path.split('/');
    let currentLevel = root;
    let currentPath = '';
    parts.forEach((part, idx) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const isLast = idx === parts.length - 1;
      let node = currentLevel.find((n) => n.name === part);
      if (!node) {
        node = {
          name: part,
          path: currentPath,
          type: isLast ? entry.type : 'dir',
          children: [],
        };
        currentLevel.push(node);
        if (node.type === 'dir') dirMap.set(currentPath, node);
      }
      currentLevel = node.children;
    });
  }
  return root;
}

function FileNode({
  node,
  onSelect,
  selectedPath,
  depth,
}: {
  node: TreeNode;
  onSelect: (path: string) => void;
  selectedPath: string | null;
  depth: number;
}) {
  const [open, setOpen] = useState(depth < 1);

  if (node.type === 'dir') {
    return (
      <div>
        <div
          className="flex items-center gap-1.5 px-2 py-1 text-sm cursor-pointer hover:bg-white/5 rounded"
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
          onClick={() => setOpen(!open)}
        >
          <span className="text-neutral-500">{open ? '📂' : '📁'}</span>
          <span className="text-neutral-300">{node.name}</span>
        </div>
        {open &&
          node.children.map((child) => (
            <FileNode key={child.path} node={child} onSelect={onSelect} selectedPath={selectedPath} depth={depth + 1} />
          ))}
      </div>
    );
  }

  return (
    <div
      className={`px-2 py-1 text-sm cursor-pointer hover:bg-white/5 rounded truncate ${
        selectedPath === node.path ? 'bg-white/10 text-white' : 'text-neutral-400'
      }`}
      style={{ paddingLeft: `${depth * 14 + 24}px` }}
      onClick={() => onSelect(node.path)}
    >
      {node.name}
    </div>
  );
}

export default function FileExplorer({ projectId, refreshKey }: { projectId: string; refreshKey: number }) {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState<string>('');
  const [loadingFile, setLoadingFile] = useState(false);

  useEffect(() => {
    api.getFileTree(projectId).then(setEntries).catch(() => setEntries([]));
  }, [projectId, refreshKey]);

  const tree = useMemo(() => buildTree(entries), [entries]);

  async function handleSelect(path: string) {
    setSelectedPath(path);
    setLoadingFile(true);
    try {
      const res = await api.getFile(projectId, path);
      setContent(res.content);
    } catch (e: any) {
      setContent(`// Unable to load file: ${e.message}`);
    } finally {
      setLoadingFile(false);
    }
  }

  return (
    <div className="flex flex-col h-full border-l border-border bg-panel">
      <div className="overflow-y-auto" style={{ maxHeight: selectedPath ? '40%' : '100%' }}>
        {entries.length === 0 ? (
          <div className="p-4 text-sm text-neutral-500">No files yet</div>
        ) : (
          tree.map((node) => (
            <FileNode key={node.path} node={node} onSelect={handleSelect} selectedPath={selectedPath} depth={0} />
          ))
        )}
      </div>
      {selectedPath && (
        <div className="flex-1 border-t border-border flex flex-col min-h-0">
          <div className="px-3 py-1.5 text-xs text-neutral-500 border-b border-border truncate">
            {selectedPath}
          </div>
          <pre className="flex-1 overflow-auto p-3 text-xs leading-relaxed text-neutral-300 font-mono whitespace-pre">
            {loadingFile ? 'Loading…' : content}
          </pre>
        </div>
      )}
    </div>
  );
}
