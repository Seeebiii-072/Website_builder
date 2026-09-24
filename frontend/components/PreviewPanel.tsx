'use client';

import { useState } from 'react';

export default function PreviewPanel({
  previewUrl,
  status,
  onRefresh,
}: {
  previewUrl: string | null;
  status: string;
  onRefresh: () => void;
}) {
  const [iframeKey, setIframeKey] = useState(0);

  function refresh() {
    setIframeKey((k) => k + 1);
    onRefresh();
  }

  return (
    <div className="flex flex-col h-full bg-base">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <span className="text-xs text-neutral-500 truncate">
          {previewUrl || 'No preview yet'}
        </span>
        <div className="flex gap-2">
          <button
            onClick={refresh}
            className="text-xs px-2 py-1 rounded-md border border-border hover:bg-panel"
          >
            Refresh
          </button>
          {previewUrl && (
            <a
              href={previewUrl}
              target="_blank"
              rel="noreferrer"
              className="text-xs px-2 py-1 rounded-md border border-border hover:bg-panel"
            >
              Open in new tab
            </a>
          )}
        </div>
      </div>
      <div className="flex-1 relative">
        {previewUrl ? (
          <iframe
            key={iframeKey}
            src={previewUrl}
            className="w-full h-full border-0 bg-white"
            title="Live preview"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-neutral-500 text-sm">
            {status === 'GENERATING' && 'AI is generating your website…'}
            {status === 'BUILDING' && 'Building your website…'}
            {status === 'FAILED' && 'The project failed — see chat for details.'}
            {status === 'CREATING' && 'Setting up your project…'}
            {status === 'READY' && 'Starting preview…'}
          </div>
        )}
      </div>
    </div>
  );
}
