'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';

export default function HomePage() {
  const router = useRouter();
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const name = prompt.trim().slice(0, 40);
      const project = await api.createProject(name, prompt.trim());
      await api.generate(project.id);
      router.push(`/projects/${project.id}`);
    } catch (e: any) {
      setError(e.message || 'Failed to create project');
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-6">
      <div className="w-full max-w-2xl text-center">
        <h1 className="text-3xl font-semibold mb-2">AI Website Builder</h1>
        <p className="text-neutral-400 mb-8">
          Describe the website you want to build, and watch it come to life in a real live preview.
        </p>
        <textarea
          className="w-full h-32 rounded-xl bg-panel border border-border p-4 text-sm outline-none focus:border-neutral-500 resize-none"
          placeholder="Create a modern SaaS landing page for an AI analytics platform. Include navbar, hero, features, pricing, testimonials and footer."
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        {error && <p className="text-red-400 text-sm mt-3 text-left">{error}</p>}
        <button
          onClick={handleCreate}
          disabled={loading || !prompt.trim()}
          className="mt-4 px-6 py-2.5 rounded-lg bg-white text-black font-medium text-sm disabled:opacity-40 hover:bg-neutral-200 transition"
        >
          {loading ? 'Creating…' : 'Create Website'}
        </button>
      </div>
    </main>
  );
}
