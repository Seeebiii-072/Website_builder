'use client';

import { useState, useRef, useEffect } from 'react';

export interface ChatMessage {
  id: string;
  role: 'user' | 'ai' | 'system';
  text: string;
  isError?: boolean;
}

export default function ChatPanel({
  messages,
  onSend,
  disabled,
}: {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  disabled: boolean;
}) {
  const [input, setInput] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  function handleSend() {
    if (!input.trim() || disabled) return;
    onSend(input.trim());
    setInput('');
  }

  return (
    <div className="flex flex-col h-full border-r border-border bg-panel">
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((m) => (
          <div key={m.id} className="text-sm">
            <div className="text-xs text-neutral-500 mb-1">
              {m.role === 'user' ? 'You' : m.role === 'ai' ? 'AI' : ''}
            </div>
            <div
              className={`whitespace-pre-wrap leading-relaxed ${
                m.isError ? 'text-red-400' : m.role === 'user' ? 'text-neutral-100' : 'text-neutral-300'
              }`}
            >
              {m.text}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="p-3 border-t border-border">
        <div className="flex gap-2">
          <input
            className="flex-1 bg-base border border-border rounded-lg px-3 py-2 text-sm outline-none focus:border-neutral-500"
            placeholder="Ask AI..."
            value={input}
            disabled={disabled}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          />
          <button
            onClick={handleSend}
            disabled={disabled || !input.trim()}
            className="px-3 py-2 rounded-lg bg-white text-black text-sm font-medium disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
