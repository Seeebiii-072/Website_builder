'use client';

import StatusBadge from './StatusBadge';
import { api } from '@/lib/api';

export default function TopBar({
  name,
  status,
  projectId,
}: {
  name: string;
  status: string;
  projectId: string;
}) {
  return (
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-panel">
      <div className="flex items-center gap-3">
        <span className="font-semibold text-sm">◉ AI Website Builder</span>
        <span className="text-neutral-500 text-sm">/</span>
        <span className="text-sm text-neutral-300">{name}</span>
        <StatusBadge status={status} />
      </div>
      <div className="flex gap-2">
        <a
          href={api.exportUrl(projectId)}
          className="text-xs px-3 py-1.5 rounded-md border border-border hover:bg-white/5"
        >
          Export ZIP
        </a>
      </div>
    </div>
  );
}
